"""Offline, fail-closed Binance SPOT diff-depth + REST snapshot synchronizer.

This is a deterministic research component, not a websocket client or source
authenticator. The caller must capture original (unmodified) depthUpdate frames
at receipt and obtain a REST snapshot for the same authenticated venue/symbol.
Session reconnect always invalidates the previous order book.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping

from core.mode import QuantMode

_NAME = re.compile(r"^[A-Za-z0-9_-]{1,48}$")
_SYMBOL = re.compile(r"^[A-Z0-9]{3,20}$")


class DepthSyncError(ValueError):
    """Malformed scope/frame, missing continuity or invalid book."""


@dataclass(frozen=True)
class DepthSyncResult:
    status: str
    last_update_id: int | None
    reason: str = ""


@dataclass(frozen=True)
class DepthBookView:
    symbol: str
    market: str
    source: str
    session_id: str
    last_update_id: int
    available_at_ms: int
    snapshot_received_at_ms: int
    bids: tuple[tuple[str, str], ...]
    asks: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _Diff:
    first: int
    final: int
    available_at_ms: int
    digest: str
    bids: tuple[tuple[Decimal, Decimal], ...]
    asks: tuple[tuple[Decimal, Decimal], ...]


class OfflineSpotDepthSynchronizer:
    """Per-session bounded local book; no persistence, trading or EventBus ACK.

    State: BUFFERING -> WAITING_BRIDGE -> SYNCED; any unrecoverable ambiguity
    leads to RESET_REQUIRED (and clears the book). After reconnect, a NEW
    snapshot is mandatory; the previous book is never re-used. Spot-specific
    interval semantics differ from aggregate trade sequence and Futures pu.
    """

    def __init__(
        self, *, symbol: str, source: str, session_id: str,
        mode_provider: Callable[[], QuantMode], market: str = "binance_spot",
        max_buffer: int = 2048, max_levels: int = 5000,
        max_age_ms: int = 10_000, max_future_skew_ms: int = 1000,
    ) -> None:
        if market != "binance_spot":
            raise DepthSyncError("only binance_spot diff depth is supported")
        if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
            raise DepthSyncError("invalid canonical symbol")
        if not callable(mode_provider):
            raise DepthSyncError("simulation mode provider required")
        if (
            not isinstance(max_buffer, int) or isinstance(max_buffer, bool)
            or not 1 <= max_buffer <= 100_000
            or not isinstance(max_levels, int) or isinstance(max_levels, bool)
            or not 1 <= max_levels <= 5000
            or not isinstance(max_age_ms, int) or isinstance(max_age_ms, bool)
            or max_age_ms < 0
            or not isinstance(max_future_skew_ms, int)
            or isinstance(max_future_skew_ms, bool)
            or max_future_skew_ms < 0
        ):
            raise DepthSyncError("invalid bounded depth or clock configuration")
        self._validate_name(source, "source")
        self._validate_name(session_id, "session_id")
        self.symbol, self.source, self.session_id = symbol, source, session_id
        self.market = market
        self._mode_provider = mode_provider
        self.max_buffer, self.max_levels = max_buffer, max_levels
        self.max_age_ms, self.max_future_skew_ms = max_age_ms, max_future_skew_ms
        self._clear("BUFFERING")

    @staticmethod
    def _validate_name(value: str, name: str) -> None:
        if not isinstance(value, str) or not _NAME.fullmatch(value):
            raise DepthSyncError(f"invalid {name}")

    def _simulation_only(self) -> None:
        if self._mode_provider() is not QuantMode.SIMULATION:
            raise RuntimeError("depth synchronization strictly SIMULATION-only")

    @staticmethod
    def _positive(value: Any, name: str) -> int:
        if type(value) is not int or value <= 0:
            raise DepthSyncError(f"{name} must be a positive integer")
        return value

    @staticmethod
    def _decimal(value: Any, *, zero_ok: bool) -> Decimal:
        # Binance sends decimal strings. No binary float coercion or NaN.
        if not isinstance(value, str) or len(value) > 50:
            raise DepthSyncError("book price/quantity must be bounded decimal strings")
        try:
            number = Decimal(value)
        except InvalidOperation as exc:
            raise DepthSyncError("invalid book decimal") from exc
        if not number.is_finite() or (number < 0 if zero_ok else number <= 0):
            raise DepthSyncError("negative/nonfinite/zero book value")
        if number != 0 and not -32 <= number.adjusted() <= 32:
            raise DepthSyncError("book decimal is outside supported bounds")
        return number

    def _levels(self, value: Any) -> tuple[tuple[Decimal, Decimal], ...]:
        if not isinstance(value, list) or len(value) > self.max_levels:
            raise DepthSyncError("book side must be a bounded levels list")
        out = []
        seen: set[Decimal] = set()
        for row in value:
            if not isinstance(row, (tuple, list)) or len(row) != 2:
                raise DepthSyncError("invalid price/quantity level")
            p = self._decimal(row[0], zero_ok=False)
            q = self._decimal(row[1], zero_ok=True)
            if p in seen:
                raise DepthSyncError("duplicate price within book side")
            seen.add(p)
            out.append((p, q))
        return tuple(out)

    def _parse_diff(self, frame: Mapping[str, Any], received: int) -> _Diff:
        if (
            not isinstance(frame, Mapping)
            or frame.get("e") != "depthUpdate"
            or frame.get("s") != self.symbol
            or "pu" in frame
            or "lastUpdateId" in frame
        ):
            raise DepthSyncError("wrong stream, symbol, or market payload")
        first = self._positive(frame.get("U"), "U")
        final = self._positive(frame.get("u"), "u")
        event_ms = self._positive(frame.get("E"), "E")
        if first > final:
            raise DepthSyncError("invalid diff depth update interval")
        if event_ms > received + self.max_future_skew_ms:
            raise DepthSyncError("depth event is future-dated")
        if received - event_ms > self.max_age_ms:
            raise DepthSyncError("depth event is stale")
        bids = self._levels(frame.get("b"))
        asks = self._levels(frame.get("a"))
        try:
            raw = json.dumps(
                dict(frame), sort_keys=True, separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise DepthSyncError("non-JSON or nonfinite depth payload") from exc
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return _Diff(first, final, received, digest, bids, asks)

    def _clear(self, status: str) -> None:
        self.status = status
        self._buffer: list[_Diff] = []
        self._bids: dict[Decimal, Decimal] = {}
        self._asks: dict[Decimal, Decimal] = {}
        self.last_update_id: int | None = None
        self.snapshot_received_at_ms: int | None = None
        self.available_at_ms: int | None = None
        self._last_seen_ms: int | None = None

    def _reset(self, reason: str) -> DepthSyncResult:
        self._clear("RESET_REQUIRED")
        return DepthSyncResult(self.status, None, reason)

    def reconnect(self, session_id: str) -> None:
        """Invalidate all session state and require entirely new snapshot."""
        self._simulation_only()
        self._validate_name(session_id, "session_id")
        if session_id == self.session_id:
            raise DepthSyncError("reconnect requires a distinct session_id")
        self.session_id = session_id
        self._clear("BUFFERING")

    @staticmethod
    def _replace(
        book: dict[Decimal, Decimal], changes: tuple[tuple[Decimal, Decimal], ...],
    ) -> None:
        for price, qty in changes:
            if qty == 0:
                book.pop(price, None)
            else:
                book[price] = qty

    @staticmethod
    def _assert_book(
        bids: dict[Decimal, Decimal], asks: dict[Decimal, Decimal],
    ) -> None:
        if not bids or not asks:
            raise DepthSyncError("unusable book: empty bid or ask")
        if max(bids) >= min(asks):
            raise DepthSyncError("invalid crossed/locked order book")

    def _apply(self, diff: _Diff) -> DepthSyncResult:
        assert self.last_update_id is not None
        if diff.final <= self.last_update_id:
            return DepthSyncResult("IGNORED", self.last_update_id, "stale/duplicate interval")
        if diff.first > self.last_update_id + 1:
            return self._reset("missing diff-depth updates; new snapshot required")
        # Spot interval bridge: U <= (lastUpdateId+1) <= u.
        bids = self._bids.copy()
        asks = self._asks.copy()
        self._replace(bids, diff.bids)
        self._replace(asks, diff.asks)
        if len(bids) > self.max_levels or len(asks) > self.max_levels:
            return self._reset("local book grew beyond safe bounded depth")
        try:
            self._assert_book(bids, asks)
        except DepthSyncError as exc:
            return self._reset(str(exc))
        self._bids, self._asks = bids, asks
        self.last_update_id = diff.final
        self.available_at_ms = max(self.available_at_ms or 0, diff.available_at_ms)
        self.status = "SYNCED"
        return DepthSyncResult("APPLIED", diff.final)

    def ingest_diff(
        self, frame: Mapping[str, Any], *, received_at_ms: int,
    ) -> DepthSyncResult:
        """Read one original Spot diff frame; buffer before snapshot, never skip."""
        self._simulation_only()
        received = self._positive(received_at_ms, "received_at_ms")
        if self.status == "RESET_REQUIRED":
            return DepthSyncResult(self.status, None, "call reconnect and reacquire snapshot")
        try:
            diff = self._parse_diff(frame, received)
        except DepthSyncError:
            # A malformed packet can hide a required update. The current
            # book must never remain trusted after rejecting one.
            self._reset("malformed depth update; continuity cannot be proven")
            raise
        if (
            (self._last_seen_ms is not None and received < self._last_seen_ms)
            or (
                self.snapshot_received_at_ms is not None
                and received < self.snapshot_received_at_ms
            )
        ):
            return self._reset("non-monotonic local receipt clock")
        self._last_seen_ms = received
        if self.status in {"SYNCED", "WAITING_BRIDGE"}:
            return self._apply(diff)
        if len(self._buffer) >= self.max_buffer:
            return self._reset("pre-snapshot diff buffer overflow")
        if self._buffer:
            last = self._buffer[-1]
            if diff.final < last.final or diff.first < last.first:
                return self._reset("out-of-order buffered diff events")
            if (diff.first, diff.final) == (last.first, last.final):
                if diff.digest != last.digest:
                    return self._reset("conflicting duplicate depth interval")
                return DepthSyncResult("IGNORED", None, "identical buffered replay")
        self._buffer.append(diff)
        return DepthSyncResult("BUFFERED", None)

    def load_snapshot(
        self, snapshot: Mapping[str, Any], *,
        received_at_ms: int, session_id: str,
    ) -> DepthSyncResult:
        """Apply a REST snapshot only after stream buffering began.

        The REST depth response has no symbol/venue/arrival attestation. A
        trusted caller must bind its request to this exact session and symbol.
        """
        self._simulation_only()
        received = self._positive(received_at_ms, "received_at_ms")
        if session_id != self.session_id:
            raise DepthSyncError("snapshot from another socket session")
        if self.status != "BUFFERING" or not self._buffer:
            raise DepthSyncError("snapshot requires pending diff stream buffer")
        if not isinstance(snapshot, Mapping):
            raise DepthSyncError("invalid REST depth snapshot")
        if "U" in snapshot or "u" in snapshot or "pu" in snapshot:
            raise DepthSyncError("partial/diff depth is not an order-book snapshot")
        snapshot_id = self._positive(snapshot.get("lastUpdateId"), "lastUpdateId")
        bids = self._levels(snapshot.get("bids"))
        asks = self._levels(snapshot.get("asks"))
        if received < self._buffer[0].available_at_ms:
            raise DepthSyncError("snapshot receipt must follow first buffered diff")
        if snapshot_id < self._buffer[0].first:
            return DepthSyncResult("AWAITING_NEW_SNAPSHOT", None, "snapshot older than first U")
        bd, ad = dict(bids), dict(asks)
        self._assert_book(bd, ad)
        # Candidate snapshot is not exposed until a live diff bridges the ID.
        self._bids, self._asks = bd, ad
        self.snapshot_received_at_ms = received
        self.available_at_ms = received
        self.last_update_id = snapshot_id
        self.status = "WAITING_BRIDGE"
        pending, self._buffer = self._buffer, []
        for diff in pending:
            self._apply(diff)
            if self.status == "RESET_REQUIRED":
                return DepthSyncResult(self.status, None, "snapshot/diff gap or invalid book")
        return DepthSyncResult(self.status, self.last_update_id)

    def view(self, *, as_of_ms: int | None = None) -> DepthBookView:
        """Only expose fully bridged book at/after its last update availability."""
        self._simulation_only()
        if self.status != "SYNCED":
            raise DepthSyncError("order book unavailable until snapshot/diff sync")
        if as_of_ms is not None:
            self._positive(as_of_ms, "as_of_ms")
            if as_of_ms < (self.available_at_ms or 0):
                raise DepthSyncError("order book is not available at requested as-of")
        return DepthBookView(
            symbol=self.symbol, market=self.market,
            source=self.source, session_id=self.session_id,
            last_update_id=self.last_update_id,
            available_at_ms=self.available_at_ms,
            snapshot_received_at_ms=self.snapshot_received_at_ms,
            bids=tuple(
                (str(p), str(q)) for p, q in
                sorted(self._bids.items(), key=lambda x: x[0], reverse=True)
            ),
            asks=tuple(
                (str(p), str(q)) for p, q in
                sorted(self._asks.items(), key=lambda x: x[0])
            ),
        )
