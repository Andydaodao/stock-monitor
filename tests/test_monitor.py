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
from main import finish_session, new_documents, run_sample, save_documents
from market import EastmoneyProvider, TencentProvider
from session import next_market_time, parse_symbols, resolve_until
from state import evaluate


ZONE = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parents[1]
STOCK = {"code": "300398", "market": "SZ", "symbol": "300398.SZ", "name": "飞凯材料",
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
        self.assertEqual(evaluate(STOCK, quote(at, 36.8))[0], "PULLBACK_ZONE")
        self.assertEqual(evaluate(STOCK, quote(at, 35.9))[0], "INVALIDATION_TOUCH")

    def test_config_and_market_hours(self):
        config = load_config(ROOT / "config.yaml")
        self.assertTrue(all("enabled" not in s for s in config["stocks"]))
        monitor = config["monitor"]
        self.assertTrue(market_is_open(datetime(2026, 9, 28, 10, tzinfo=ZONE), monitor))
        self.assertFalse(market_is_open(datetime(2026, 9, 28, 12, tzinfo=ZONE), monitor))
        self.assertFalse(market_is_open(datetime(2026, 9, 27, 10, tzinfo=ZONE), monitor))

    def test_sources_normalize_units_and_time(self):
        fields = [""] * 39
        for index, value in {1:"飞凯材料",2:"300398",3:"39.07",4:"37.92",5:"37.89",6:"357130",
                             30:"20260923145251",31:"1.15",32:"3.03",33:"39.30",34:"37.72",
                             35:"39.07/357130/1380980217",38:"6.30"}.items():
            fields[index] = value
        with patch("market._get", return_value=('v_sz300398="'+'~'.join(fields)+'";').encode("gbk")):
            result = TencentProvider().get_quote(STOCK)
        self.assertEqual(result["volume"], 35713000)
        payload = {"data": {"f43":39.07,"f44":39.3,"f45":37.72,"f46":37.89,"f47":357130,
                            "f48":1380980217,"f57":"300398","f58":"飞凯材料","f60":37.92,
                            "f168":6.3,"f169":1.15,"f170":3.03,
                            "f86":int(datetime(2026,9,23,14,52,51,tzinfo=ZONE).timestamp())}}
        with patch("market._get", return_value=json.dumps(payload).encode()):
            self.assertEqual(EastmoneyProvider().get_quote(STOCK)["volume"], 35713000)


class SessionInputTests(unittest.TestCase):
    def test_symbols_until_and_lunch(self):
        catalog = {"300398.SZ": STOCK}
        self.assertEqual(parse_symbols(" 300398.sz,300398.SZ ", catalog), ["300398.SZ"])
        with self.assertRaisesRegex(ValueError, "INVALID_STOCK"):
            parse_symbols("600000.SH", catalog)
        now = datetime(2026, 9, 28, 10, 0, tzinfo=ZONE)
        self.assertEqual(resolve_until("11:30", now).hour, 11)
        sessions = [{"start":"09:30","end":"11:30"},{"start":"13:00","end":"15:00"}]
        lunch = now.replace(hour=12)
        self.assertEqual(next_market_time(lunch, now.replace(hour=15), sessions).hour, 13)


class RunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        import yaml
        config = load_config(ROOT / "config.yaml")
        config["stocks"] = [dict(STOCK), {**STOCK, "code":"600000", "market":"SH", "symbol":"600000.SH", "name":"测试B"}]
        (self.root / "config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
        self.at = datetime(2026,9,28,10,15,tzinfo=ZONE)
        self.session = {"session_id":"20260928-101500", "started_at":self.at.isoformat(),
                        "until":self.at.replace(hour=11,minute=30).isoformat(), "interval_minutes":5,
                        "selected_stocks":[STOCK["symbol"]]}
        save_documents(self.root, new_documents(self.session), self.session["session_id"])

    def data(self, name):
        return json.loads((self.root / "data" / (name+".json")).read_text(encoding="utf-8"))

    def test_samples_use_real_interval_and_do_not_repeat_unchanged_events(self):
        provider = Provider({STOCK["symbol"]: quote(self.at,39.35,volume=10000000)})
        run_sample(self.root,[STOCK["symbol"]],self.session,self.at+timedelta(seconds=5),[provider])
        run_sample(self.root,[STOCK["symbol"]],self.session,self.at+timedelta(minutes=1),[provider])
        self.assertEqual(len(self.data("history")),2)
        self.assertEqual(len(self.data("events")),2)
        provider.result[STOCK["symbol"]] = quote(self.at+timedelta(minutes=5),39.45,volume=12000000)
        run_sample(self.root,[STOCK["symbol"]],self.session,self.at+timedelta(minutes=5,seconds=5),[provider])
        record = self.data("history")[-1]
        self.assertEqual(record["interval_volume"],2000000)
        self.assertEqual(record["sample_interval_seconds"],245)
        self.assertAlmostEqual(record["interval_price_change"],0.1)
        self.assertEqual(self.data("events")[-1]["event"],"BREAKOUT_HOLD")
        self.assertEqual(len(self.data("events")),2)

    def test_stale_and_individual_failure(self):
        self.session["selected_stocks"].append("600000.SH")
        provider = Provider({STOCK["symbol"]: quote(self.at-timedelta(minutes=11),39.35),
                             "600000.SH": ValueError("upstream unavailable")})
        result = run_sample(self.root,self.session["selected_stocks"],self.session,self.at,[provider])
        self.assertEqual(result["failed_count"],2)
        self.assertEqual(self.data("latest")["stocks"][STOCK["symbol"]]["state"],"STALE")
        self.assertEqual(self.data("latest")["stocks"]["600000.SH"]["state"],"ERROR")
        self.assertEqual(self.data("events"),[])

    def test_closed_and_finish(self):
        provider = Provider({STOCK["symbol"]: quote(self.at)})
        with self.assertRaisesRegex(ValueError, "不在 A 股交易时段"):
            run_sample(self.root,[STOCK["symbol"]],self.session,self.at.replace(hour=12),[provider])
        finish_session(self.root,self.session,self.at.replace(hour=11,minute=30))
        self.assertEqual(self.data("status")["session_status"],"FINISHED")


if __name__ == "__main__":
    unittest.main()
