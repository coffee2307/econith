"""Independent durable named *read-only research observer* subscriptions.

Each explicitly named subscriber owns its own SQLite ledger and cursor, separate
from the existing derived observer ledger and original source. A subscriber's
ACK is only for recording the exact canonical source-bound derived projection.
No arbitrary side effects, portfolio/risk evaluation, order publication or
broker operations occur here. These named research consumers do NOT make other
EventBus handlers durable or turn EventBus into a transaction coordinator.
"""
from __future__ import annotations

import asyncio
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from core.event_bus import Event, EventBus
from core.mode import QuantMode
from infrastructure.market_derived_research_receipts import (
    DerivedResearchReceiptError,
    OfflineDerivedResearchReceiptLedger,
)

_TOPICS = ("md.ticker", "indicator.volume_delta", "indicator.obi")
_ALLOWED_SOURCE = frozenset((
    "verified_closed_session_replay",
    "verified_sealed_public_microbatch",
    "verified_forward_spot_shadow",
))
_NAME = re.compile(r"^research_[a-z][a-z0-9_]{0,39}$")


class NamedResearchSubscriberError(DerivedResearchReceiptError):
    """Named durable research observation failed closed."""


@dataclass(frozen=True)
class NamedResearchSubscriberStatus:
    name: str
    durable_receipts: int
    replayed_receipts: int


class OfflineNamedResearchSubscriber:
    """One immutable identity, one own SQLite, one cursor per source/topic.

    The identity is persisted before EventBus registration. A new process may
    reopen the same DB only for the identical name AND source policy. Failures
    in any subscriber propagate through the protected EventBus idle barrier.
    """

    def __init__(
        self, path: str | Path, *,
        name: str,
        expected_source: str,
        mode_provider: Callable[[], QuantMode],
        parallel_delivery: bool = False,
    ) -> None:
        if type(name) is not str or not _NAME.fullmatch(name):
            raise NamedResearchSubscriberError("invalid named research consumer identity")
        if expected_source not in _ALLOWED_SOURCE:
            raise NamedResearchSubscriberError("unapproved research source policy")
        if not callable(mode_provider) or mode_provider() is not QuantMode.SIMULATION:
            raise RuntimeError("named research consumers require SIMULATION")
        self.path = Path(path)
        if self.path.exists():
            if not self.path.is_file() or self.path.stat().st_size == 0:
                raise NamedResearchSubscriberError("invalid existing research receipt ledger")
            # Inspect existing DB read-only BEFORE constructing a ledger that
            # might otherwise create tables in a foreign/original SQLite.
            try:
                with sqlite3.connect(
                    self.path.resolve().as_uri() + "?mode=ro", uri=True,
                ) as conn:
                    rows = conn.execute("""
                        SELECT name, expected_source
                        FROM offline_named_research_identity
                    """).fetchall()
            except sqlite3.DatabaseError as exc:
                raise NamedResearchSubscriberError(
                    "existing ledger has no authorized named-consumer identity"
                ) from exc
            if rows != [(name, expected_source)]:
                raise NamedResearchSubscriberError(
                    "named research identity/source policy mismatch"
                )
        if type(parallel_delivery) is not bool:
            raise NamedResearchSubscriberError("invalid observer scheduling mode")
        self.parallel_delivery = parallel_delivery
        self.name = name
        self.expected_source = expected_source
        self._mode_provider = mode_provider
        self._ledger = OfflineDerivedResearchReceiptLedger(
            self.path, mode_provider=mode_provider,
        )
        with self._ledger._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS offline_named_research_identity (
                        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                        name TEXT NOT NULL, expected_source TEXT NOT NULL
                    )
                """)
                conn.execute("""
                    INSERT OR IGNORE INTO offline_named_research_identity
                    (singleton,name,expected_source) VALUES (1,?,?)
                """, (name, expected_source))
                row = conn.execute("""
                    SELECT name,expected_source FROM offline_named_research_identity
                    WHERE singleton=1
                """).fetchone()
                if row is None or tuple(row) != (name, expected_source):
                    raise NamedResearchSubscriberError(
                        "named research identity changed during initialization"
                    )
                conn.execute("COMMIT")
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        self.replayed_receipts = 0
        self._registered = False

    async def observe(self, event: Event) -> None:
        if self._mode_provider() is not QuantMode.SIMULATION:
            raise RuntimeError("named research consumer changed out of SIMULATION")
        if event.topic not in _TOPICS or event.payload.get("source") != self.expected_source:
            raise NamedResearchSubscriberError("unverified source in named research subscriber")
        arguments = dict(
            topic=event.topic,
            session_id=event.payload["verified_session_id"],
            ordinal=event.payload["verified_source_ordinal"],
            source_record_id=event.payload["verified_source_record_id"],
            source_available_at_ms=event.payload["verified_source_available_at_ms"],
            projection=event.payload,
        )
        # Experimental research-only scheduling: await the full durable
        # SQLite COMMIT before EventBus confirmation; never fire-and-forget.
        if self.parallel_delivery:
            result = await asyncio.to_thread(self._ledger.accept, **arguments)
        else:
            result = self._ledger.accept(**arguments)
        if result.replayed:
            self.replayed_receipts += 1

    def register(self, bus: EventBus) -> None:
        if self._registered:
            raise NamedResearchSubscriberError("duplicate named subscriber registration")
        if not isinstance(bus, EventBus):
            raise NamedResearchSubscriberError("named research subscriber requires EventBus")
        self._registered = True
        for topic in _TOPICS:
            bus.subscribe(topic, self.observe)

    def verify(self, session_id: str, expected: Mapping[str, int]) -> NamedResearchSubscriberStatus:
        if self._mode_provider() is not QuantMode.SIMULATION:
            raise RuntimeError("named research verification requires SIMULATION")
        if set(expected) != set(_TOPICS):
            raise NamedResearchSubscriberError("complete three-topic receipt proof required")
        if any(type(n) is not int or n < 0 for n in expected.values()):
            raise NamedResearchSubscriberError("invalid named consumer receipt counts")
        verified = {
            topic: self._ledger.verified_count(session_id, topic)
            for topic in _TOPICS
        }
        if verified != dict(expected):
            raise NamedResearchSubscriberError(
                "named research subscriber durable receipt count mismatch"
            )
        return NamedResearchSubscriberStatus(
            self.name, sum(verified.values()), self.replayed_receipts,
        )


def checked_named_recovery_paths(
    *, output_dir: Path, original_db: Path,
    primary_receipts: Path,
    names: tuple[str, ...],
    recovery: Mapping[str, Path] | None,
) -> dict[str, Path]:
    """A reused named DB must pre-exist outside fresh scratch and raw input."""
    if len(set(names)) != len(names) or not names:
        raise NamedResearchSubscriberError("unique named consumers required")
    if recovery is not None and set(recovery) != set(names):
        raise NamedResearchSubscriberError("all named recovery ledgers required")
    paths = {}
    forbidden = {output_dir.resolve(), original_db.resolve(), primary_receipts.resolve()}
    for name in names:
        path = (recovery[name] if recovery is not None
                else output_dir / (name + ".sqlite"))
        if not isinstance(path, Path):
            raise NamedResearchSubscriberError("named consumer ledger Path required")
        resolved = path.resolve()
        if resolved in forbidden or (
            recovery is not None
            and (not path.is_absolute() or not path.is_file()
                 or resolved.is_relative_to(output_dir.resolve()))
        ):
            raise NamedResearchSubscriberError("unsafe named consumer recovery path")
        if resolved in paths.values():
            raise NamedResearchSubscriberError("named consumers cannot share one ledger")
        paths[name] = resolved
    return paths
