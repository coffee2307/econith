"""ECONITH :: tests.test_runtime

Runtime reliability regression suite covering the three highest-risk invariants
established during the system audit:

  1. Mode-gated isolation — SIMULATION state can never contaminate a REALITY
     order-routing (DOMAIN_QUANT) consumer.
  2. Multi-coin labeling protection — the per-symbol ``groupby`` labeler produces
     zero mathematical cross-contamination between heterogeneous price scales.
  3. Execution degradation status — a CCXT network drop surfaces as
     ``execution_routing == "DEGRADED"`` in the health read-model.

Run:  pytest -q
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Group 1.1 — Mode-gated isolation
# ---------------------------------------------------------------------------
async def test_mode_gate_blocks_world_in_reality() -> None:
    """world.* MUST NOT reach a DOMAIN_QUANT handler while in REALITY."""
    from core.event_bus import DOMAIN_QUANT, EventBus
    from core.mode import QuantMode

    bus = EventBus(mode_provider=lambda: QuantMode.REALITY)
    quant_hits: list[str] = []
    plain_hits: list[str] = []

    async def quant_handler(evt) -> None:
        quant_hits.append(evt.topic)

    async def plain_handler(evt) -> None:
        plain_hits.append(evt.topic)

    bus.subscribe("world.sovereign", quant_handler, domain=DOMAIN_QUANT)
    bus.subscribe("world.sovereign", plain_handler)
    bus.subscribe("order.intent", quant_handler, domain=DOMAIN_QUANT)

    await bus.start()
    await bus.publish("world.sovereign", sim_day=42)
    await bus.publish("order.intent", symbol="BTCUSDT", quantity=1.0)
    import asyncio

    await asyncio.sleep(0.05)
    await bus.stop()

    # The order-routing node saw ONLY its own domain topic; the simulated world
    # event was dropped before reaching it.
    assert quant_hits == ["order.intent"], quant_hits
    # A non-gated telemetry handler still receives the world event.
    assert plain_hits == ["world.sovereign"], plain_hits


async def test_mode_gate_allows_world_in_simulation() -> None:
    """In SIMULATION the coupling is permitted: the DOMAIN_QUANT handler sees world.*."""
    from core.event_bus import DOMAIN_QUANT, EventBus
    from core.mode import QuantMode

    bus = EventBus(mode_provider=lambda: QuantMode.SIMULATION)
    hits: list[str] = []

    async def quant_handler(evt) -> None:
        hits.append(evt.topic)

    bus.subscribe("world.sovereign", quant_handler, domain=DOMAIN_QUANT)
    await bus.start()
    await bus.publish("world.sovereign", sim_day=1)
    import asyncio

    await asyncio.sleep(0.05)
    await bus.stop()

    assert hits == ["world.sovereign"], hits


# ---------------------------------------------------------------------------
# Group 1.2 — Multi-coin labeling protection
# ---------------------------------------------------------------------------
def _synthetic_multicoin_frame():
    """Two assets, 30-min span @ 30s cadence, wildly different price scales."""
    import pandas as pd

    base = 1_751_600_000_000
    rows = []
    for i in range(60):
        rows.append(
            {"symbol": "BTCUSDT", "ts_ms": base + i * 30_000,
             "price": 60_000.0 + i * 10.0, "mid": 60_000.0 + i * 10.0}
        )
    for i in range(60):
        rows.append(
            {"symbol": "DOGEUSDT", "ts_ms": base + i * 30_000,
             "price": 0.12 + i * 0.0001, "mid": 0.12 + i * 0.0001}
        )
    return pd.DataFrame(rows)


def test_label_symbol_no_cross_contamination() -> None:
    """Forward returns must stay within each asset — no BTC/DOGE bleed-through."""
    pd = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")

    from training.quant.label_symbol import label_dataset

    tmp = Path(tempfile.mkdtemp())
    feat = tmp / "features"
    feat.mkdir(parents=True)
    _synthetic_multicoin_frame().to_parquet(feat / "features_00000.parquet", index=False)

    out = tmp / "processed" / "quant_labeled.parquet"
    summary = label_dataset(str(feat), str(out), 0.2)

    assert set(summary["symbols"]) == {"BTCUSDT", "DOGEUSDT"}, summary["symbols"]

    labeled = pd.read_parquet(out)
    btc = labeled[labeled.symbol == "BTCUSDT"]["forward_return_1m"].dropna()
    doge = labeled[labeled.symbol == "DOGEUSDT"]["forward_return_1m"].dropna()

    # Both series drift ~0.01%/step. If a global sort had interleaved symbols,
    # a BTC->DOGE jump would produce a ~ -0.999999 (or +500000x) return.
    assert btc.abs().max() < 0.05, f"BTC forward return contaminated: {btc.abs().max()}"
    assert doge.abs().max() < 0.05, f"DOGE forward return contaminated: {doge.abs().max()}"
    # Sanity: no NaN/inf leaked into the reward.
    assert labeled["reward"].apply(lambda v: v == v).all()  # NaN != NaN


def test_label_symbol_missing_symbol_column_is_safe() -> None:
    """A legacy single-asset capture (no 'symbol' col) must still label cleanly."""
    pd = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    from training.quant.label_symbol import label_dataset

    tmp = Path(tempfile.mkdtemp())
    feat = tmp / "features"
    feat.mkdir(parents=True)
    base = 1_751_600_000_000
    rows = [
        {"ts_ms": base + i * 30_000, "price": 100.0 + i, "mid": 100.0 + i}
        for i in range(60)
    ]
    pd.DataFrame(rows).to_parquet(feat / "features_00000.parquet", index=False)

    out = tmp / "processed" / "quant_labeled.parquet"
    summary = label_dataset(str(feat), str(out), 0.2)
    assert "UNKNOWN" in summary["symbols"]


# ---------------------------------------------------------------------------
# Group 1.3 — Execution degradation status
# ---------------------------------------------------------------------------
def _make_bridge(*, live: bool, credentialed: bool, mode: str):
    """Construct a CCXT bridge without invoking __init__ network paths."""
    from quant.ccxt_bridge import CCXTBinanceBridge

    bridge = CCXTBinanceBridge.__new__(CCXTBinanceBridge)
    bridge._live = live
    bridge._exchange = object() if live else None
    bridge._credentialed = credentialed
    bridge._execution_env = "demo"
    bridge._testnet = True
    return bridge


def test_execution_status_degraded_on_network_drop(monkeypatch) -> None:
    """REALITY + credentialed + no live session => DEGRADED with a clear detail."""
    import core.mode as mode_mod
    from core.mode import QuantMode

    monkeypatch.setattr(mode_mod, "current_mode", lambda: QuantMode.REALITY)
    # ccxt_bridge imports current_mode by reference; patch there too.
    import quant.ccxt_bridge as bridge_mod

    monkeypatch.setattr(bridge_mod, "current_mode", lambda: QuantMode.REALITY)

    bridge = _make_bridge(live=False, credentialed=True, mode="REALITY")
    status = bridge.execution_status()

    assert status["execution_routing"] == "DEGRADED", status
    assert status["exchange_live"] is False
    assert "unreachable" in status["detail"].lower()


def test_execution_status_live_when_authenticated(monkeypatch) -> None:
    import quant.ccxt_bridge as bridge_mod
    from core.mode import QuantMode

    monkeypatch.setattr(bridge_mod, "current_mode", lambda: QuantMode.REALITY)
    bridge = _make_bridge(live=True, credentialed=True, mode="REALITY")
    status = bridge.execution_status()
    assert status["execution_routing"] == "LIVE", status
    assert status["exchange_live"] is True


def test_execution_status_synthetic_in_simulation(monkeypatch) -> None:
    import quant.ccxt_bridge as bridge_mod
    from core.mode import QuantMode

    monkeypatch.setattr(bridge_mod, "current_mode", lambda: QuantMode.SIMULATION)
    bridge = _make_bridge(live=False, credentialed=True, mode="SIMULATION")
    status = bridge.execution_status()
    assert status["execution_routing"] == "SYNTHETIC", status


def test_binance_execution_env_resolves_demo_and_live(monkeypatch) -> None:
    from config.environment import Environment

    demo = Environment(
        BINANCE_EXECUTION_ENV="demo",
        BINANCE_TESTNET=False,
        BINANCE_DEMO_API_KEY="k_demo",
        BINANCE_DEMO_API_SECRET="s_demo",
        BINANCE_TRADE_API_KEY="k_live",
        BINANCE_TRADE_API_SECRET="s_live",
    )
    assert demo.binance_execution_env_resolved == "demo"
    assert demo.is_demo_execution is True
    assert demo.effective_binance_execution_api_key == "k_demo"
    assert demo.has_binance_execution_credentials is True

    live = Environment(
        BINANCE_EXECUTION_ENV="live",
        BINANCE_TESTNET=True,
        BINANCE_DEMO_API_KEY="k_demo",
        BINANCE_DEMO_API_SECRET="s_demo",
        BINANCE_TRADE_API_KEY="k_live",
        BINANCE_TRADE_API_SECRET="s_live",
    )
    assert live.binance_execution_env_resolved == "live"
    assert live.is_demo_execution is False
    assert live.effective_binance_execution_api_key == "k_live"


def test_ccxt_spot_order_omits_futures_params() -> None:
    from quant.payloads import CCXTOrderPayload, ExecutionPayload, OrderSide, OrderType

    payload = ExecutionPayload(
        symbol="BTCUSDT",
        desk="crypto",
        mode="REALITY",
        side=OrderSide.LONG_CLOSE,
        order_type=OrderType.MARKET,
        quantity=0.001,
        leverage=3.0,
        client_order_id="econith-test-1",
    )
    order = CCXTOrderPayload.from_execution(payload, "BTC/USDT", market_type="spot")
    assert "reduceOnly" not in order.params
    assert "leverage" not in order.params
    assert order.params.get("newClientOrderId") == "econith-test-1"


def test_ccxt_futures_symbol_maps_to_linear_perp() -> None:
    from quant.ccxt_bridge import _to_ccxt_symbol

    assert _to_ccxt_symbol("BTCUSDT", "future") == "BTC/USDT:USDT"
    assert _to_ccxt_symbol("BTCUSDT", "spot") == "BTC/USDT"


def test_ccxt_futures_order_includes_reduce_only_when_closing() -> None:
    from quant.payloads import CCXTOrderPayload, ExecutionPayload, OrderSide

    payload = ExecutionPayload(
        symbol="BTCUSDT",
        desk="crypto",
        mode="REALITY",
        side=OrderSide.LONG_CLOSE,
        quantity=0.001,
        leverage=3.0,
    )
    order = CCXTOrderPayload.from_execution(payload, "BTC/USDT", market_type="future")
    assert order.params.get("reduceOnly") is True
    assert order.params.get("leverage") == 3
