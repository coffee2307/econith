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
