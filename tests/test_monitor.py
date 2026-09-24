import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from config import load_config, market_is_open
from main import run
from market import EastmoneyProvider, TencentProvider, fetch_quote
from state import evaluate


ZONE = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parents[1]
STOCK = {"code": "300398", "market": "SZ", "symbol": "300398.SZ", "name": "飞凯材料", "enabled": True,
         "triggers": {"pullback": {"enabled": True, "low": 36.4, "high": 37.3},
                      "breakout": {"enabled": True, "price": 39.3},
                      "invalidation": {"enabled": True, "price": 36.0}},
         "alert": {"pre_alert_percent": 1.0}}


def quote(at, price=39.0, high=None, volume=10000000):
    return {"symbol": STOCK["symbol"], "code": STOCK["code"], "name": STOCK["name"],
            "timestamp": at, "price": price, "change": 0, "change_percent": 0,
            "open": 38.0, "high": high if high is not None else price, "low": 37.0,
            "previous_close": 38.0, "volume": volume, "turnover_amount": volume * price,
            "turnover_rate": 1.2, "source": "test"}


class Provider:
    name = "test"

    def __init__(self, result):
        self.result = result
        self.calls = []

    def get_quote(self, stock):
        self.calls.append(stock["symbol"])
        value = self.result[stock["symbol"]]
        if isinstance(value, Exception):
            raise value
        return dict(value)


class StateTests(unittest.TestCase):
    def test_requested_states(self):
        at = datetime(2026, 9, 28, 10, 15, tzinfo=ZONE)
        self.assertEqual(evaluate(STOCK, quote(at, 39.0))[0], "PRE_ALERT")
        self.assertEqual(evaluate(STOCK, quote(at, 39.1, 39.3))[0], "BREAKOUT_TOUCH")
        self.assertEqual(evaluate(STOCK, quote(at, 39.35))[0], "BREAKOUT_TRIGGER")
        self.assertEqual(evaluate(STOCK, quote(at, 39.45), {"signal_state": "BREAKOUT_TRIGGER"})[0], "BREAKOUT_HOLD")
        self.assertEqual(evaluate(STOCK, quote(at, 39.18), {"signal_state": "BREAKOUT_HOLD"})[0], "BREAKOUT_LOST")
        self.assertEqual(evaluate(STOCK, quote(at, 39.4), {"signal_state": "BREAKOUT_LOST"})[0], "BREAKOUT_TRIGGER")
        self.assertEqual(evaluate(STOCK, quote(at, 36.8))[0], "PULLBACK_ZONE")
        self.assertEqual(evaluate(STOCK, quote(at, 37.5), {"seen_pullback": True, "price": 37.2})[0], "PULLBACK_RECOVERY")
        self.assertEqual(evaluate(STOCK, quote(at, 35.9))[0], "INVALIDATION_TOUCH")
        self.assertEqual(evaluate(STOCK, quote(at, 35.8), {"signal_state": "INVALIDATION_TOUCH"})[0], "INVALIDATION_HOLD")

    def test_config_and_market_hours(self):
        config = load_config(ROOT / "config.yaml")
        self.assertTrue(all(not s["enabled"] for s in config["stocks"]))
        monitor = config["monitor"]
        self.assertTrue(market_is_open(datetime(2026, 9, 28, 10, tzinfo=ZONE), monitor))
        self.assertFalse(market_is_open(datetime(2026, 9, 28, 12, tzinfo=ZONE), monitor))
        self.assertFalse(market_is_open(datetime(2026, 9, 27, 10, tzinfo=ZONE), monitor))
        monitor["monitor_until"] = "2026-09-28T11:30:00+08:00"
        self.assertFalse(market_is_open(datetime(2026, 9, 29, 10, tzinfo=ZONE), monitor))

    def test_sources_normalize_units_and_time(self):
        fields = [""] * 39
        for index, value in {1:"飞凯材料",2:"300398",3:"39.07",4:"37.92",5:"37.89",6:"357130",
                             30:"20260923145251",31:"1.15",32:"3.03",33:"39.30",34:"37.72",
                             35:"39.07/357130/1380980217",38:"6.30"}.items():
            fields[index] = value
        with patch("market._get", return_value=('v_sz300398="'+'~'.join(fields)+'";').encode("gbk")):
            result = TencentProvider().get_quote(STOCK)
        self.assertEqual(result["volume"], 35713000)
        self.assertEqual(result["turnover_amount"], 1380980217)
        self.assertEqual(result["turnover_rate"], 6.3)
        self.assertEqual(result["timestamp"].isoformat(), "2026-09-23T14:52:51+08:00")
        payload = {"data": {"f43":39.07,"f44":39.3,"f45":37.72,"f46":37.89,"f47":357130,
                            "f48":1380980217,"f57":"300398","f58":"飞凯材料","f60":37.92,
                            "f168":6.3,"f169":1.15,"f170":3.03,
                            "f86":int(datetime(2026,9,23,14,52,51,tzinfo=ZONE).timestamp())}}
        with patch("market._get", return_value=json.dumps(payload).encode()):
            result = EastmoneyProvider().get_quote(STOCK)
        self.assertEqual(result["volume"], 35713000)
        self.assertEqual(result["timestamp"].isoformat(), "2026-09-23T14:52:51+08:00")
        payload["data"]["f47"] = 35713000
        with patch("market._get", return_value=json.dumps(payload).encode()):
            self.assertEqual(EastmoneyProvider().get_quote(STOCK)["volume"], 35713000)


class RunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        import yaml
        config = load_config(ROOT / "config.yaml")
        config["stocks"] = [dict(STOCK), {**STOCK, "code":"600000", "market":"SH", "symbol":"600000.SH", "name":"测试B", "enabled": False}]
        (self.root / "config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
        self.at = datetime(2026,9,28,10,15,tzinfo=ZONE)

    def data(self, name):
        return json.loads((self.root / "data" / (name+".json")).read_text(encoding="utf-8"))

    def test_disabled_duplicate_interval_and_new_day(self):
        provider = Provider({STOCK["symbol"]: quote(self.at,39.35,volume=10000000)})
        run(self.root,self.at+timedelta(seconds=5),[provider])
        self.assertEqual(provider.calls, [STOCK["symbol"]])
        self.assertEqual(self.data("events")[0]["event"], "BREAKOUT_TRIGGER")
        run(self.root,self.at+timedelta(minutes=1),[provider])
        self.assertEqual(len(self.data("history")), 1)
        self.assertEqual(len(self.data("events")), 1)
        provider.result[STOCK["symbol"]] = quote(self.at+timedelta(minutes=5),39.45,volume=12000000)
        run(self.root,self.at+timedelta(minutes=5,seconds=5),[provider])
        self.assertEqual(self.data("history")[-1]["interval_volume"],2000000)
        self.assertEqual(self.data("history")[-1]["interval_seconds"],300)
        self.assertEqual(self.data("events")[-1]["event"],"BREAKOUT_HOLD")
        next_day = self.at+timedelta(days=1)
        provider.result[STOCK["symbol"]] = quote(next_day,39.45,volume=200000)
        run(self.root,next_day+timedelta(seconds=5),[provider])
        self.assertIsNone(self.data("history")[-1]["interval_volume"])
        self.assertEqual(self.data("events")[-1]["event"],"BREAKOUT_TRIGGER")

    def test_stale_and_individual_failure(self):
        import yaml
        config = yaml.safe_load((self.root / "config.yaml").read_text(encoding="utf-8"))
        config["stocks"][1]["enabled"] = True
        (self.root / "config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
        provider = Provider({STOCK["symbol"]: quote(self.at-timedelta(minutes=11),39.35),
                             "600000.SH": ValueError("upstream unavailable")})
        result = run(self.root,self.at,[provider])
        self.assertEqual(result["failed_count"],2)
        self.assertEqual(self.data("latest")["stocks"][STOCK["symbol"]]["state"],"STALE")
        self.assertEqual(self.data("latest")["stocks"]["600000.SH"]["state"],"ERROR")
        self.assertEqual(self.data("events"),[])
        provider.result[STOCK["symbol"]] = quote(self.at+timedelta(minutes=5),39.35)
        result = run(self.root,self.at+timedelta(minutes=5,seconds=5),[provider])
        self.assertEqual(result["success_count"],1)
        self.assertEqual(self.data("events")[-1]["event"],"BREAKOUT_TRIGGER")

    def test_closed_does_not_fetch(self):
        provider = Provider({STOCK["symbol"]: quote(self.at)})
        result = run(self.root,self.at.replace(hour=12),[provider])
        self.assertFalse(result["market_open"])
        self.assertEqual(provider.calls,[])

    def test_older_quote_cannot_rewind_history(self):
        provider = Provider({STOCK["symbol"]: quote(self.at,39.35)})
        run(self.root,self.at+timedelta(seconds=5),[provider])
        provider.result[STOCK["symbol"]] = quote(self.at-timedelta(minutes=1),39.55)
        result = run(self.root,self.at+timedelta(minutes=1),[provider])
        self.assertEqual(result["failed_count"],1)
        self.assertEqual(len(self.data("history")),1)
        self.assertEqual(self.data("latest")["stocks"][STOCK["symbol"]]["quote_timestamp"],self.at.isoformat())


if __name__ == "__main__":
    unittest.main()
