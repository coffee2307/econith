"""Fixture derived from a public Binance Spot BTC/USDT 1h read-only snapshot (2026-10-16).

Snapshot is strictly a parser smoke test, not proof of strategy profitability.
"""
from econith.quant.paper_v1 import validated_candles, replay_spot, SymbolRules, PaperConfig

def test_binance_live_format_smoke():
    klines = [
       [1792130400000,'82162.87','82303.77','82020.00','82180.01','366.31626',1792133999999],
       [1792134000000,'82180.00','82324.07','82112.01','82290.00','386.3942',1792137599999],
       [1792137600000,'82289.99','82388.00','82168.00','82248.09','317.59403',1792141199999],
       [1792141200000,'82248.08','82280.01','82015.31','82017.96','491.59776',1792144799999],
       [1792144800000,'82018.01','82312.72','81842.20','82218.82','596.13397',1792148399999],
       [1792148400000,'82218.82','82940.81','82202.75','82640.73','1069.16672',1792151999999],
       [1792152000000,'82640.74','82658.97','82017.00','82052.49','673.31253',1792155599999],
       [1792155600000,'82052.49','82114.03','81777.00','81822.01','783.66832',1792159199999],
    ]
    bars = validated_candles(klines, timeframe_ms=3600000, asof_ms=1792159200000)
    assert len(bars) == 8
    report = replay_spot({'BTCUSDT':bars},{'BTCUSDT':SymbolRules('0.00001',5.0)},PaperConfig(lookback=2))
    assert report.final_equity > 0
    assert all(fill.execution_mode == 'SIMULATION' for fill in report.fills)
