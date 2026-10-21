"""Single-pair user-facing paper runner acceptance; no trade/network APIs."""
from __future__ import annotations

import copy
import json

import pytest

from scripts.paper_trade_quickstart import (
    run_one, _params, PASS, CAPITAL_STATUS, main,
)


def fixture_capture(symbols, *, limit, interval):
    assert len(symbols) == 1 and interval in ("1h", "4h", "1d")
    unit = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}[interval]
    start = 1_780_000_000_000 // unit * unit
    bars = []
    for i in range(limit):
        # A known hypothetical price path, NOT public venue results.
        if i < limit // 2:
            price = 100 + i * .15
        else:
            price = 100 + (limit - i) * .15
        open_ = round(price, 6)
        bars.append([
            start + i * unit, str(open_), str(open_ * 1.004),
            str(open_ * .996), str(open_), "1000",
            start + (i + 1) * unit - 1, "50000", 10,
            "500", "25000", "0",
        ])
    return {
        "source": "binance_spot_public", "asof_ms": start + limit * unit,
        "symbols": symbols, "timeframe": interval,
        "market": {symbols[0]: bars},
        "rules": {symbols[0]: {"step_size": "0.001", "min_notional": 5.0}},
    }


def test_one_paper_command_simulates_and_audits_both_fee_scenarios(tmp_path):
    output = (tmp_path / "demo").resolve()
    result = run_one("BTCUSDT", capital=100, interval="4h",
                     candles=500, output=output, capture=fixture_capture)
    assert result["verdict"] == PASS
    assert result["capital_readiness"] == CAPITAL_STATUS
    assert result["execution_mode"] == "SIMULATION_HISTORICAL_REPLAY_ONLY"
    assert result["symbol"] == "BTCUSDT"
    assert result["real_exchange_orders"] == 0
    assert result["closed_candles"] == 500
    assert result["base"]["simulated_fill_count"] > 0
    assert result["base"]["net_pnl_usdt"] != result["cost_stress_3x"]["net_pnl_usdt"]
    assert result["prospective_live_profit_proven"] is False
    assert result["order_endpoints_available"] is False
    assert (output / "market.json").exists()
    assert (output / "rules.json").exists()
    for f in ("base_replay.json", "stress_3x_replay.json"):
        payload = json.loads((output / f).read_text())
        assert payload["audit"]["status"] == "SIMULATED_LEDGER_RECONCILED"
        assert payload["config"]["fill_rule"] == (
            "previous closed candle signal, next historical open fill"
        )
        assert all(x["execution_mode"] == "SIMULATION"
                   for x in payload["report"]["fills"])
    from econith.quant.public_binance_data import load_snapshot
    assert set(load_snapshot(output)["market"]) == {"BTCUSDT"}
    assert json.loads((output / "summary.json").read_text()) == result


@pytest.mark.parametrize("capital", [10, 100, 1000])
def test_virtual_capital_and_exchange_size_respected(capital, tmp_path):
    out = (tmp_path / ("sim_" + str(capital))).resolve()
    result = run_one("BTCUSDT", capital=capital, interval="4h",
                     candles=500, output=out, capture=fixture_capture)
    assert result["starting_virtual_usdt"] == capital
    assert result["base"]["equity_usdt"] > 0
    assert result["real_exchange_orders"] == 0


@pytest.mark.parametrize("symbol", ["DOG", "DOGE", "../DOG", "dogusdt", "DOG/USDT",
                                    "BTCUSD", "BTCUSDT X", ""])
def test_invalid_symbol_or_ambiguous_dog_not_traded(tmp_path, symbol):
    with pytest.raises(ValueError):
        _params(symbol, 100, "4h", 500, (tmp_path / "new").resolve())


@pytest.mark.parametrize("capital", [0, -1, 9.99, 1000.01, float("nan"),
                                    float("inf"), True])
def test_unbounded_or_invalid_funds_refused(tmp_path, capital):
    with pytest.raises(ValueError):
        _params("BTCUSDT", capital, "4h", 500, (tmp_path / "new").resolve())


def test_unlisted_exact_pair_never_silently_substituted(tmp_path):
    def absent(symbols, **kw):
        assert symbols == ["DOGUSDT"]
        raise ValueError("DOGUSDT not currently spot-trading on public venue")
    out = (tmp_path / "dog").resolve()
    with pytest.raises(ValueError, match="not currently"):
        run_one("DOGUSDT", capital=100, interval="4h",
                candles=500, output=out, capture=absent)
    assert not out.exists()


def test_wrong_pair_or_swapped_snapshot_refused_no_written_evidence(tmp_path):
    def swapped(symbols, **kw):
        obj = fixture_capture(["DOGEUSDT"], limit=500, interval="4h")
        return obj
    out = (tmp_path / "mislabelled").resolve()
    with pytest.raises(ValueError, match="replaced pair"):
        run_one("DOGUSDT", capital=100, interval="4h",
                candles=500, output=out, capture=swapped)
    assert not out.exists()


def test_broken_gapped_candles_not_filled_with_future_data(tmp_path):
    def gapped(symbols, **kw):
        obj = fixture_capture(symbols, limit=500, interval="4h")
        obj["market"][symbols[0]].pop(111)
        return obj
    out = (tmp_path / "gapped").resolve()
    with pytest.raises(ValueError, match="missing"):
        run_one("BTCUSDT", capital=100, interval="4h",
                candles=500, output=out, capture=gapped)
    assert not out.exists()


def test_existing_private_evidence_cannot_be_overwritten(tmp_path):
    folder = (tmp_path / "existing").resolve()
    folder.mkdir()
    (folder / "data.txt").write_text("keep")
    with pytest.raises(ValueError, match="scope"):
        run_one("BTCUSDT", capital=100, interval="4h",
                candles=500, output=folder, capture=fixture_capture)
    assert (folder / "data.txt").read_text() == "keep"


def test_cli_requires_explicit_public_data_and_simulation(tmp_path):
    output = (tmp_path / "cli").resolve()
    for flags in ([], ["--simulation-only"], ["--allow-public-market-data"]):
        with pytest.raises(SystemExit) as err:
            main(["--output-dir", str(output)] + flags)
        assert err.value.code == 2
    assert not output.exists()


def test_failed_cli_prints_no_deposit_and_no_orders(monkeypatch, tmp_path, capsys):
    import scripts.paper_trade_quickstart as module
    def broken(*args, **kw):
        raise ValueError("test public venue refused")
    monkeypatch.setattr(module, "run_one", broken)
    code = main(["--output-dir", str((tmp_path / "none").resolve()),
                 "--allow-public-market-data", "--simulation-only"])
    assert code == 2
    status = json.loads(capsys.readouterr().out)
    assert status["verdict"] == "REFUSED_PAPER_EVIDENCE"
    assert status["capital_readiness"] == CAPITAL_STATUS
    assert status["real_exchange_orders"] == 0
