"""No internet, no orders, deterministic negative acceptance matrix."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from ci.phase1_spot_multiasset_ohlcv import audit_klines, collect, INTERVALS, ROWS
from ci.phase1_spot_multiasset_ohlcv_audit import inspect

BEFORE=1700000000000

def candle(opened,step):
    return [opened,"100","110","90","105","2",opened+step-1,"210",3,"1","105","0"]

def sample(step,cutoff):
    start=(cutoff//step)*step-(ROWS-1)*step
    return [candle(start+i*step,step) for i in range(ROWS)]

def requester(path,params=None):
    if path=="/api/v3/time":
        return json.dumps({"serverTime":BEFORE}).encode()
    if path=="/api/v3/exchangeInfo":
        s=params["symbol"]
        return json.dumps({"symbols":[{"symbol":s,"status":"TRADING","baseAsset":s.replace("USDT",""),"quoteAsset":"USDT"}]}).encode()
    if path=="/api/v3/klines":
        return json.dumps(sample(INTERVALS[params["interval"]],params["endTime"])).encode()
    raise AssertionError("unexpected endpoint")

class Quality(unittest.TestCase):
    def setUp(self):
        self.step=60000
        self.cutoff=BEFORE//self.step*self.step-1
        self.rows=sample(self.step,self.cutoff)
    def check_bad(self,change):
        rows=copy.deepcopy(self.rows)
        change(rows)
        with self.assertRaises(ValueError):
            audit_klines(json.dumps(rows).encode(),interval="1m",cutoff_ms=self.cutoff)
    def test_good(self):
        self.assertEqual(audit_klines(json.dumps(self.rows).encode(),interval="1m",cutoff_ms=self.cutoff)["candles"],100)
    def test_duplicate(self):self.check_bad(lambda x:x[-1].__setitem__(0,x[-2][0]))
    def test_missing_time(self):self.check_bad(lambda x:x[-1].__setitem__(0,x[-1][0]+self.step))
    def test_lookahead(self):self.check_bad(lambda x:x[-1].__setitem__(6,self.cutoff+self.step))
    def test_missing_last_requested_candle(self):
        self.check_bad(lambda x:[row.__setitem__(0,row[0]-self.step) or row.__setitem__(6,row[6]-self.step) for row in x])
    def test_inverted_high_low(self):self.check_bad(lambda x:x[0].__setitem__(2,"80"))
    def test_negative_volume(self):self.check_bad(lambda x:x[0].__setitem__(5,"-1"))
    def test_nonfinite(self):self.check_bad(lambda x:x[0].__setitem__(5,"NaN"))
    def test_taker_exceeds_total(self):self.check_bad(lambda x:x[0].__setitem__(9,"3"))
    def test_malformed_schema(self):self.check_bad(lambda x:x[0].pop())
    def test_empty(self):
        with self.assertRaises(ValueError):audit_klines(b"[]",interval="1m",cutoff_ms=self.cutoff)
    def test_bad_open_time_type(self):self.check_bad(lambda x:x[0].__setitem__(0,"100"))
    def test_zero_trade_nonzero_volume(self):self.check_bad(lambda x:x[0].__setitem__(8,0))
    def test_finite_probe_and_independent_audit(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/"new"
            r=collect(root,requester=requester)
            self.assertEqual(r["status"],"PASSED_FINITE_REST_QUALITY_ONLY")
            self.assertEqual(r["candle_count"],600)
            self.assertEqual(inspect(root)["verdict"],"PASSED_FINITE_REST_ONLY")
            self.assertFalse(r["history_continuity_verified"])
            self.assertEqual(r["orders_submitted"],0)
            with self.assertRaises(ValueError):collect(root,requester=requester)
            p=root/"klines_btcusdt_1m.json"
            p.write_text(p.read_text().replace('"105"','"106"',1))
            with self.assertRaises(ValueError): inspect(root)
    def test_refuse_nontrading(self):
        def unavailable(path,params=None):
            result=requester(path,params)
            if path=="/api/v3/exchangeInfo":return result.replace(b'TRADING',b'BREAK')
            return result
        with tempfile.TemporaryDirectory() as d:
            result=collect(Path(d)/"run",requester=unavailable)
            self.assertEqual(result["status"],"REFUSED_OR_INCOMPLETE")
            self.assertEqual(result["orders_submitted"],0)
    def test_refuse_partial(self):
        def gapped(path,params=None):
            result=requester(path,params)
            if path=="/api/v3/klines":
                r=json.loads(result);r.pop();return json.dumps(r).encode()
            return result
        with tempfile.TemporaryDirectory() as d:
            result=collect(Path(d)/"run",requester=gapped)
            self.assertEqual(result["status"],"REFUSED_OR_INCOMPLETE")
            self.assertEqual(result["orders_submitted"],0)

if __name__=="__main__":unittest.main()
