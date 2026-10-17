"""Research-only SIMULATION acknowledgement of fully persisted order intents.

EventBus.publish() is a queue insertion, NOT a durable receipt. This explicitly
opt-in observer writes each routed order.intent to the OfflineAdmissionLedger's
SQLite database, and writes a receipt once ALL expected legs are committed.
Only then is an offline.signal.ack message issued on the in-process EventBus.

An ACK listener independently checks both outbox source integrity and the
durable complete receipt before retiring a signal. No CCXT/broker API exists.
This is not registered in main.py, cannot authorize REALITY execution, and
does not acknowledge fills or exchange execution.
"""
from __future__ import annotations

import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from core.event_bus import DOMAIN_QUANT, Event, EventBus
from core.mode import QuantMode
from quant.offline_admission import (
    IntentConflict,
    OfflineAdmissionLedger,
    fingerprint,
    stable_client_order_id,
)
from quant.offline_signal_outbox import (
    OfflineSignalOutbox,
    derive_leg_intent_id,
)

_HEX64 = re.compile(r"^[a-f0-9]{64}$")


class AdmissionReceiptError(ValueError):
    """An untrusted, conflicting, or incomplete signal/leg envelope."""


class OfflineSignalAckBridge:
    """Simulation-only durable inbox; every order remains ADMITTED, never sent."""

    def __init__(
        self,
        bus: EventBus,
        outbox: OfflineSignalOutbox,
        ledger: OfflineAdmissionLedger,
        *,
        mode_provider: Callable[[], QuantMode],
    ) -> None:
        self.bus = bus
        self.outbox = outbox
        self.ledger = ledger
        self._mode_provider = mode_provider
        self.errors: list[str] = []
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with self.ledger._connect() as conn:
            yield conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS offline_signal_receipts (
                    signal_id TEXT PRIMARY KEY,
                    signal_fingerprint TEXT NOT NULL,
                    expected_legs INTEGER NOT NULL CHECK (expected_legs BETWEEN 1 AND 32),
                    status TEXT NOT NULL CHECK (status IN ('COLLECTING','COMMITTED')),
                    updated_at_ms INTEGER NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS offline_signal_receipt_legs (
                    signal_id TEXT NOT NULL,
                    leg_index INTEGER NOT NULL,
                    intent_id TEXT NOT NULL UNIQUE,
                    intent_fingerprint TEXT NOT NULL,
                    PRIMARY KEY(signal_id, leg_index),
                    FOREIGN KEY(signal_id) REFERENCES offline_signal_receipts(signal_id)
                )
            """)

    def register(self) -> None:
        self.bus.subscribe("order.intent", self._on_intent, domain=DOMAIN_QUANT)
        self.bus.subscribe("offline.signal.ack", self._on_ack, domain=DOMAIN_QUANT)

    def _simulation_only(self) -> None:
        if self._mode_provider() is not QuantMode.SIMULATION:
            raise AdmissionReceiptError("acknowledgement inbox strictly SIMULATION-only")

    def admit_leg(self, payload: Mapping[str, Any]) -> bool:
        """One atomic transaction persists leg and (when complete) signal receipt.

        Returns True only when all expected legs are durable, including duplicate
        legs with the same immutable payload. No broker submission is possible.
        """
        self._simulation_only()
        signal_id = payload.get("signal_id")
        intent_id = payload.get("intent_id")
        symbol = payload.get("symbol")
        index, count = payload.get("leg_index"), payload.get("leg_count")
        source_hash = payload.get("signal_fingerprint")
        if not isinstance(signal_id, str) or not isinstance(intent_id, str):
            raise AdmissionReceiptError("missing durable source or intent identity")
        try:
            stable_client_order_id(signal_id)
            stable_client_order_id(intent_id)
        except ValueError as exc:
            raise AdmissionReceiptError("invalid intent provenance") from exc
        if (not isinstance(index, int) or isinstance(index, bool)
            or not isinstance(count, int) or isinstance(count, bool)
            or not 1 <= count <= 32 or not 0 <= index < count):
            raise AdmissionReceiptError("invalid expected leg count/index")
        if not isinstance(source_hash, str) or not _HEX64.fullmatch(source_hash):
            raise AdmissionReceiptError("missing or invalid source fingerprint")
        try:
            expected_id = derive_leg_intent_id(signal_id, index, symbol)
        except (ValueError, TypeError) as exc:
            raise AdmissionReceiptError("invalid symbol or intent leg") from exc
        if intent_id != expected_id:
            raise AdmissionReceiptError("derived intent identity mismatch")
        if payload.get("side") not in ("BUY", "SELL"):
            raise AdmissionReceiptError("unrecognized simulated order side")
        try:
            intent_hash = fingerprint(payload)
        except ValueError as exc:
            raise AdmissionReceiptError("unserializable intent payload") from exc

        # Confirm the source was actually staged, not just an event with a
        # plausible name. The source payload cannot be forged or re-created.
        try:
            origin = self.outbox.get(signal_id)
        except ValueError as exc:
            raise AdmissionReceiptError("unknown outbox origin namespace") from exc
        if origin is None or origin.status == "ACKED":
            raise AdmissionReceiptError("source signal is missing or retired")
        if fingerprint(origin.payload) != source_hash:
            raise AdmissionReceiptError("outbox source payload mismatch")

        now = time.time_ns() // 1_000_000
        with self._connect() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            try:
                receipt = conn.execute(
                    "SELECT * FROM offline_signal_receipts WHERE signal_id=?",
                    (signal_id,),
                ).fetchone()
                if receipt is None:
                    conn.execute(
                        """INSERT INTO offline_signal_receipts
                           (signal_id,signal_fingerprint,expected_legs,status,updated_at_ms)
                           VALUES (?,?,?,'COLLECTING',?)""",
                        (signal_id, source_hash, count, now),
                    )
                elif (receipt["signal_fingerprint"] != source_hash
                      or receipt["expected_legs"] != count):
                    raise AdmissionReceiptError("conflicting signal plan cardinality/digest")

                previous_leg = conn.execute(
                    """SELECT * FROM offline_signal_receipt_legs
                       WHERE signal_id=? AND leg_index=?""",
                    (signal_id, index),
                ).fetchone()
                if previous_leg is not None:
                    if (previous_leg["intent_id"] != intent_id
                        or previous_leg["intent_fingerprint"] != intent_hash):
                        raise IntentConflict("routed leg reappeared with changed business data")
                else:
                    # The admission and receipt leg live in the SAME SQLite
                    # transaction: there is no crash interval between them.
                    existing = conn.execute(
                        "SELECT payload_fingerprint FROM offline_intent_admission WHERE intent_id=?",
                        (intent_id,),
                    ).fetchone()
                    if existing is not None and existing["payload_fingerprint"] != intent_hash:
                        raise IntentConflict("order intent exists with conflicting payload")
                    if existing is None:
                        conn.execute(
                            """INSERT INTO offline_intent_admission
                               (intent_id,client_order_id,payload_fingerprint,status,
                                created_at_ms,updated_at_ms)
                               VALUES (?,?,?,'ADMITTED',?,?)""",
                            (intent_id, stable_client_order_id(intent_id), intent_hash, now, now),
                        )
                    conn.execute(
                        """INSERT INTO offline_signal_receipt_legs
                           (signal_id,leg_index,intent_id,intent_fingerprint)
                           VALUES (?,?,?,?)""",
                        (signal_id, index, intent_id, intent_hash),
                    )
                found = conn.execute(
                    """SELECT COUNT(*) FROM offline_signal_receipt_legs
                       WHERE signal_id=?""",
                    (signal_id,),
                ).fetchone()[0]
                complete = found == count
                if complete:
                    conn.execute(
                        """UPDATE offline_signal_receipts SET status='COMMITTED',
                           updated_at_ms=? WHERE signal_id=?""",
                        (now, signal_id),
                    )
                conn.execute("COMMIT")
                return complete
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    def is_complete(self, signal_id: str, source_hash: str, leg_count: int) -> bool:
        """Re-read authoritative journal; an EventBus ACK is never trusted alone."""
        with self._connect() as conn:
            row = conn.execute(
                """SELECT signal_fingerprint,expected_legs,status
                   FROM offline_signal_receipts WHERE signal_id=?""",
                (signal_id,),
            ).fetchone()
            if row is None:
                return False
            count = conn.execute(
                "SELECT COUNT(*) FROM offline_signal_receipt_legs WHERE signal_id=?",
                (signal_id,),
            ).fetchone()[0]
        return (row["status"] == "COMMITTED"
                and row["signal_fingerprint"] == source_hash
                and row["expected_legs"] == leg_count == count)

    def acknowledge_if_committed(
        self, signal_id: str, source_hash: str, leg_count: int,
    ) -> bool:
        """Only a verified downstream receipt may transition QUEUED -> ACKED."""
        self._simulation_only()
        if not self.is_complete(signal_id, source_hash, leg_count):
            return False
        try:
            origin = self.outbox.get(signal_id)
        except ValueError:
            return False
        if origin is None or fingerprint(origin.payload) != source_hash:
            return False
        if origin.status == "ACKED":
            return True  # duplicate ACK is idempotent
        if origin.status != "QUEUED":
            return False  # queue insertion may still be in progress
        self.outbox.acknowledge(signal_id)
        return True

    async def _on_intent(self, event: Event) -> None:
        try:
            is_finished = self.admit_leg(event.payload)
            if is_finished:
                await self.bus.publish(
                    "offline.signal.ack",
                    signal_id=event.payload["signal_id"],
                    signal_fingerprint=event.payload["signal_fingerprint"],
                    leg_count=event.payload["leg_count"],
                )
        except (ValueError, sqlite3.Error) as exc:
            self.errors.append(type(exc).__name__ + ": " + str(exc))

    async def _on_ack(self, event: Event) -> None:
        try:
            signal_id = event.payload["signal_id"]
            source_hash = event.payload["signal_fingerprint"]
            count = event.payload["leg_count"]
            if not isinstance(signal_id, str) or not isinstance(source_hash, str):
                return
            if not isinstance(count, int) or isinstance(count, bool):
                return
            self.acknowledge_if_committed(signal_id, source_hash, count)
        except (KeyError, ValueError, sqlite3.Error) as exc:
            self.errors.append(type(exc).__name__ + ": " + str(exc))

    async def replay_verified_acks(self) -> int:
        """Manual recovery: reissue ACKs for already committed receipts only.

        Safe after a crash between durable receipt commit and ACK. It does
        not send an order.intent or connect to any venue. Caller must start
        the SIMULATION EventBus and explicitly invoke this method.
        """
        self._simulation_only()
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT signal_id,signal_fingerprint,expected_legs
                   FROM offline_signal_receipts WHERE status='COMMITTED'"""
            ).fetchall()
        emitted = 0
        for row in rows:
            try:
                origin = self.outbox.get(row["signal_id"])
            except ValueError:
                continue
            if origin is None or origin.status != "QUEUED":
                continue
            if fingerprint(origin.payload) != row["signal_fingerprint"]:
                continue
            if not self.is_complete(
                row["signal_id"], row["signal_fingerprint"], row["expected_legs"]
            ):
                continue
            await self.bus.publish(
                "offline.signal.ack", signal_id=row["signal_id"],
                signal_fingerprint=row["signal_fingerprint"],
                leg_count=row["expected_legs"],
            )
            emitted += 1
        return emitted
