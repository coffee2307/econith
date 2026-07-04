"""ECONITH :: core.ingestion.macro_hub

The :class:`MacroIngestionHub` -- the CORE's exhaustive, zero-cost macro
ingestion orchestrator. It owns every institutional adapter (FRED, World Bank,
IMF, Eurostat, yfinance), schedules them per their frequency class, folds their
semantic features into the frequency-isolated :class:`ExhaustiveContextState`
and publishes them onto the EventBus under the strictly-namespaced
``core.macro.*`` topics.

The hub is the macro (low-frequency) half of the CORE's epistemic isolation: it
NEVER writes into the micro order-flow plane. The QUANT desks read the emitted
:class:`ExhaustiveContextState`, not the hub directly.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from core.event_bus import EventBus
from core.ingestion.adapters import AsyncFetch, IngestionAdapter, build_adapter
from core.ingestion.config import (
    IngestionFrequency,
    MacroIngestionSettings,
    SourceConfig,
)
from core.ingestion.context_state import (
    ExhaustiveContextState,
    MacroFeatureBlock,
)

logger = logging.getLogger("econith.core.ingestion.macro_hub")

__all__ = ["MacroIngestionHub", "MacroSnapshotWriter"]

# Semantic feature -> scalar attribute on MacroFeatureBlock. Country-qualified
# features ("debt_to_gdp.CHN") are folded into the block's dict fields.
_SCALAR_FEATURES: frozenset[str] = frozenset(
    {
        "fed_funds_effective_rate",
        "consumer_price_index",
        "core_pce",
        "unemployment_rate",
        "yield_spread_10y_2y",
        "industrial_production",
        "dollar_index_dxy",
        "gold_xau_spot",
        "wti_crude_spot",
        "euro_hicp_index",
    }
)
_DICT_FEATURES: frozenset[str] = frozenset({"debt_to_gdp", "trade_balance_pct_gdp"})


class MacroSnapshotWriter:
    """Append-only, time-indexed historical macro sink for training.

    Macro pulls are low-frequency, so a lossless newline-delimited JSON append
    (partitioned per source per month) is the robust, dependency-free sink: it
    never blocks and works on a bare host. Each line is one point-in-time
    snapshot ``{ts_ms, ts, source, frequency, features}`` — exactly what the
    cross-asset feature store as-of-joins onto high-frequency coin rows later.
    """

    def __init__(self, root: Path | str = "datasets/raw/macro") -> None:
        self._root = Path(root)
        self._written = 0

    @property
    def written(self) -> int:
        return self._written

    async def append(
        self, source: str, frequency: str, features: dict[str, float]
    ) -> None:
        """Append one snapshot line off the event loop (best-effort, never raises)."""
        try:
            await asyncio.to_thread(self._append_sync, source, frequency, features)
        except Exception as exc:  # noqa: BLE001 - persistence must not kill ingestion
            logger.debug("macro snapshot append skipped (%s)", exc)

    def _append_sync(self, source: str, frequency: str, features: dict[str, float]) -> None:
        now = datetime.now(timezone.utc)
        partition = self._root / source / f"{now.strftime('%Y-%m')}.jsonl"
        partition.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {
                "ts_ms": int(now.timestamp() * 1000),
                "ts": now.isoformat(),
                "source": source,
                "frequency": frequency,
                "features": features,
            },
            separators=(",", ":"),
            default=str,
        )
        with partition.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        self._written += 1


class MacroIngestionHub:
    """Schedules, retries and consolidates every zero-cost macro source."""

    def __init__(
        self,
        bus: EventBus,
        settings: MacroIngestionSettings | None = None,
        fetch: AsyncFetch | None = None,
        *,
        persist_history: bool = True,
        snapshot_root: Path | str = "datasets/raw/macro",
    ) -> None:
        self._bus = bus
        self._settings = settings or MacroIngestionSettings()
        self._adapters: dict[str, IngestionAdapter] = {}
        for cfg in self._settings.enabled_sources():
            self._adapters[cfg.kind.value] = build_adapter(cfg, fetch)
        self._state = ExhaustiveContextState()
        self._tasks: list[asyncio.Task[None]] = []
        self._running = False
        # Historical training sink: every pull is broadcast live AND appended.
        self._snapshots: MacroSnapshotWriter | None = (
            MacroSnapshotWriter(snapshot_root) if persist_history else None
        )

    # -- lifecycle ------------------------------------------------------------
    async def start(self) -> None:
        """Launch one scheduler loop per enabled source at its own cadence."""
        if self._running:
            return
        self._running = True
        for name, adapter in self._adapters.items():
            task = asyncio.create_task(
                self._schedule_loop(adapter), name=f"macro-ingest-{name}"
            )
            self._tasks.append(task)
        logger.info("MacroIngestionHub online with %d sources", len(self._adapters))

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()

    # -- scheduling -----------------------------------------------------------
    async def _schedule_loop(self, adapter: IngestionAdapter) -> None:
        cfg: SourceConfig = adapter.config
        interval = cfg.frequency.poll_interval.total_seconds()
        # Prime immediately, then poll on cadence.
        while self._running:
            await self._pull_once(adapter)
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                raise

    async def _pull_once(self, adapter: IngestionAdapter) -> None:
        features = await adapter.collect()
        if not features:
            return
        self._fold_into_state(features)
        source = adapter.config.kind.value
        frequency = adapter.config.frequency.value
        # (1) LIVE runtime broadcast onto the event bus.
        await self._bus.publish(
            adapter.topic,
            source=source,
            frequency=frequency,
            features=features,
            ts=datetime.now(timezone.utc).isoformat(),
        )
        # Emit the consolidated macro snapshot on the canonical world.macro-adjacent
        # channel the CORE regime layer subscribes to.
        await self._bus.publish(
            "core.macro.context",
            macro=self._state.macro.model_dump(exclude_none=True),
            regime_label=self._state.regime_label,
        )
        # (2) HISTORICAL append-only persistence for retrospective training.
        if self._snapshots is not None:
            await self._snapshots.append(source, frequency, features)
        logger.debug("ingested %d features from %s", len(features), source)

    # -- consolidation --------------------------------------------------------
    def _fold_into_state(self, features: dict[str, float]) -> None:
        """Fold a flat feature dict into the isolated macro block."""
        macro: MacroFeatureBlock = self._state.macro
        for key, value in features.items():
            base, _, country = key.partition(".")
            if country and base in _DICT_FEATURES:
                target: dict[str, float] = getattr(macro, base)
                target[country] = value
            elif base in _SCALAR_FEATURES:
                setattr(macro, base, value)
            else:
                # Unknown/extra macro feature -> preserved via extra="allow".
                setattr(macro, base if not country else key, value)

    # -- reads ----------------------------------------------------------------
    @property
    def context(self) -> ExhaustiveContextState:
        """The live, consolidated context snapshot (macro half populated)."""
        return self._state

    def set_regime(self, label: str, confidence: float) -> None:
        """Allow the CORE regime layer to stamp the current classification."""
        self._state.regime_label = label
        self._state.regime_confidence = max(0.0, min(1.0, confidence))

    def snapshot(self) -> dict[str, object]:
        """Serialisable read-model for the dashboard / diagnostics."""
        return {
            "generated_at": self._state.generated_at.isoformat(),
            "sources": list(self._adapters.keys()),
            "macro": self._state.macro.model_dump(exclude_none=True),
            "regime": {
                "label": self._state.regime_label,
                "confidence": self._state.regime_confidence,
            },
        }
