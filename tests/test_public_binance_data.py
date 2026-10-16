import hashlib
import json
import pytest
from econith.quant.public_binance_data import capture_spot_hourly, write_snapshot


def feed(endpoint, params):
    symbol = params['symbol']
    if endpoint == 'exchangeInfo':
        return {'symbols': [{'symbol': symbol, 'status': 'TRADING', 'permissions': ['SPOT'],
             'filters': [{'filterType': 'LOT_SIZE', 'stepSize': '0.001'},
                         {'filterType': 'MIN_NOTIONAL', 'minNotional': '5.00'}]}]}
    rows = []
    for i in range(24):
        op = 100 + i
        rows.append([1000000000000 + i*3600000, str(op), str(op+2), str(op-2),
                     str(op+1), '100', 1000000000000 + (i+1)*3600000 - 1])
    return rows


def test_multiasset_capture_aligns_and_hashes(tmp_path):
    captured = capture_spot_hourly(['BTCUSDT','ETHUSDT'],limit=24,
                                   asof_ms=1000000000000+24*3600000,get=feed)
    assert len(captured['market']['BTCUSDT']) == 24
    assert len(captured['market']['ETHUSDT']) == 24
    manifest = write_snapshot(captured,tmp_path)
    for filename, expected in manifest['sha256'].items():
        assert hashlib.sha256((tmp_path/filename).read_bytes()).hexdigest() == expected
    assert json.loads((tmp_path/'manifest.json').read_text())['source'] == 'binance_spot_public'


def test_partial_candle_rejected_by_minimum_data():
    with pytest.raises(ValueError, match='too few'):
        capture_spot_hourly(['BTCUSDT'],limit=24,
                            asof_ms=1000000000000+19*3600000,get=feed)


def test_invalid_market_filters_fail_closed():
    def bad(endpoint, params):
        if endpoint == 'exchangeInfo':
            return {'symbols':[{'symbol': params['symbol'],'status':'BREAK','permissions':['SPOT'],
                                'filters':[]}]}
        return feed(endpoint,params)
    with pytest.raises(ValueError, match='spot-trading'):
        capture_spot_hourly(['BTCUSDT'],limit=24,asof_ms=1000000000000+24*3600000,get=bad)


def test_missing_bar_rejected():
    def gap(endpoint, params):
        x=feed(endpoint,params)
        return x[:12]+x[13:] if endpoint=='klines' else x
    with pytest.raises(ValueError, match='missing'):
        capture_spot_hourly(['BTCUSDT'],limit=24,asof_ms=1000000000000+24*3600000,get=gap)


def test_stale_snapshot_rejected():
    with pytest.raises(ValueError, match='stale'):
        capture_spot_hourly(['BTCUSDT'],limit=24,
                            asof_ms=1000000000000+30*3600000,get=feed)


def test_malformed_candle_is_not_silently_discarded():
    def malformed(endpoint, params):
        values = feed(endpoint, params)
        return values[:10]+[[]]+values[11:] if endpoint == 'klines' else values
    with pytest.raises(ValueError, match='malformed'):
        capture_spot_hourly(['BTCUSDT'],limit=24,
                            asof_ms=1000000000000+24*3600000,get=malformed)


def test_manifest_tampering_is_rejected(tmp_path):
    from econith.quant.public_binance_data import load_snapshot
    capture = capture_spot_hourly(['BTCUSDT'], limit=24,
                    asof_ms=1000000000000+24*3600000, get=feed)
    write_snapshot(capture, tmp_path)
    result = load_snapshot(tmp_path)
    assert result['asof_ms'] == capture['asof_ms']
    (tmp_path/'market.json').write_text('{}')
    with pytest.raises(ValueError, match='checksum'):
        load_snapshot(tmp_path)


def test_paper_cli_verified_snapshot_integration(tmp_path):
    from econith.quant.paper_v1 import main
    capture = capture_spot_hourly(['BTCUSDT'], limit=24,
                    asof_ms=1000000000000+24*3600000, get=feed)
    write_snapshot(capture, tmp_path)
    report_file = tmp_path/'output.json'
    assert main(['--snapshot-dir',str(tmp_path),'--output',str(report_file)]) == 0
    report = json.loads(report_file.read_text())
    assert report['initial_cash'] == 10000.0
    assert len(report['equity_curve']) == 24
    assert all(x['execution_mode']=='SIMULATION' for x in report['fills'])


def test_daily_capture_with_24h_spacing():
    def daily(endpoint, params):
        if endpoint == "exchangeInfo":
            return feed(endpoint, params)
        rows = []
        base = 1000000000000
        for i in range(24):
            start = base + i * 86_400_000
            rows.append([start, "100", "105", "95", "101", "100",
                         start + 86_400_000 - 1])
        return rows
    out = capture_spot_hourly(
        ["BTCUSDT"], limit=24, interval="1d",
        asof_ms=1000000000000 + 24 * 86_400_000,
        get=daily
    )
    assert out["timeframe"] == "1d"
    assert len(out["market"]["BTCUSDT"]) == 24
