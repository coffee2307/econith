"""Train-only selected spot strategies never tune against the locked test."""
from __future__ import annotations

from copy import deepcopy

from scripts.walkforward_spot_tournament import tournament, _bootstrap_lower90


def _sample(*, flat: bool = False, test_reversal: bool = False) -> dict:
    base = 1_700_000_000_000
    candles = []
    for i in range(400):
        price = 100.0 if flat else 100 + i * .4
        if test_reversal and i >= 280:
            price = max(20., 100 + 280*.4 - (i-280)*1.1)
        candles.append([
            base + i * 14_400_000, str(price), str(price+1),
            str(price-1), str(price), "100",
            base + (i+1)*14_400_000-1,
        ])
    return {
        "timeframe": "4h",
        "asof_ms": base + 400 * 14_400_000,
        "market": {"BTCUSDT": candles},
        "rules": {"BTCUSDT": {"step_size": "0.00001", "min_notional": 5.0}},
    }


def test_cannot_promote_weak_training_when_cash_beats_bot() -> None:
    output = tournament(_sample(flat=True), capital=1000)
    assert output["selected_on_training_only"] == "CASH"
    assert output["locked_test"]["tested"] is False
    assert output["ready_for_exchange_demo"] is False
    assert output["ready_for_real_money"] is False


def test_locked_holdout_has_no_influence_on_train_selection() -> None:
    strong = tournament(_sample(), capital=1000)
    reversed_test = tournament(_sample(test_reversal=True), capital=1000)
    assert strong["candidate_training_trials"] == reversed_test["candidate_training_trials"]
    assert strong["selected_on_training_only"] == reversed_test["selected_on_training_only"]
    assert strong["selected_on_training_only"] != "CASH"
    assert strong["locked_test"]["tested"] is True
    assert reversed_test["locked_test"]["tested"] is True
    assert strong["ready_for_real_money"] is False
    assert reversed_test["ready_for_real_money"] is False
    assert strong["confidence_90_pct_of_future_profit"] is None


def test_block_bootstrap_is_deterministic() -> None:
    from math import sin
    equity = [{"equity": 1000 * (1 + .003*sin(i/10) + .0002*i)}
              for i in range(120)]
    assert _bootstrap_lower90(equity) == _bootstrap_lower90(equity)
