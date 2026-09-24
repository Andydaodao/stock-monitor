from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from config import load_config, market_is_open
from market import fetch_quote
from state import evaluate
from storage import read_json, write_json


ROOT = Path(__file__).resolve().parents[1]
FILES = ("latest", "history", "events", "status")


def _save(root, documents):
    for name in FILES:
        for directory in ("data", "docs"):
            write_json(root / directory / f"{name}.json", documents[name])


def run(root=ROOT, now=None, providers=None):
    config = load_config(root / "config.yaml")
    monitor = config["monitor"]
    zone = ZoneInfo(monitor["timezone"])
    now = now.astimezone(zone) if now else datetime.now(zone)
    stamp = now.isoformat(timespec="seconds")
    enabled = monitor["enabled"]
    active = [stock for stock in config["stocks"] if stock["enabled"]] if enabled else []
    open_now = enabled and market_is_open(now, monitor)

    latest = read_json(root / "data/latest.json", {"generated_at": None, "monitor_enabled": False, "stocks": {}})
    history = read_json(root / "data/history.json", [])
    events = read_json(root / "data/events.json", [])
    status = read_json(root / "data/status.json", {})
    previous_stocks = latest.get("stocks", {})
    stocks = {s["symbol"]: previous_stocks[s["symbol"]] for s in active if s["symbol"] in previous_stocks}
    success = 0
    failures = 0
    errors = {}

    if open_now:
        print(f"[{now:%H:%M:%S}] market session: OPEN; enabled stocks: {len(active)}")
        for stock in active:
            symbol = stock["symbol"]
            previous = stocks.get(symbol, {})
            try:
                quote = fetch_quote(stock, now, providers)
                quote_time = quote["timestamp"].isoformat(timespec="seconds")
                if quote["freshness"] == "stale":
                    failures += 1
                    errors[symbol] = f"行情过期 {quote['data_age_seconds']} 秒"
                    stocks[symbol] = {**previous, "code": stock["code"], "name": stock["name"], "symbol": symbol,
                                      "price": previous.get("price", quote["price"]),
                                      "quote_timestamp": previous.get("quote_timestamp", quote_time),
                                      "source": previous.get("source", quote["source"]),
                                      "data_age_seconds": quote["data_age_seconds"], "fresh": False,
                                      "freshness": "stale", "state": "STALE", "error": errors[symbol]}
                    print(f"[{now:%H:%M:%S}] {symbol} STALE: {quote['data_age_seconds']} seconds")
                    continue
                success += 1
                same_day = previous.get("quote_timestamp", "")[:10] == quote_time[:10]
                prior = previous if same_day else {}
                if prior.get("quote_timestamp", "") > quote_time:
                    success -= 1
                    failures += 1
                    errors[symbol] = "行情时间早于上次有效报价"
                    stocks[symbol] = {**previous, "state": "ERROR", "fresh": False, "error": errors[symbol]}
                    print(f"[{now:%H:%M:%S}] {symbol} ERROR: quote timestamp moved backwards")
                    continue
                duplicate = previous.get("quote_timestamp") == quote_time
                state, trigger, seen_pullback = evaluate(stock, quote, prior)
                if duplicate:
                    state = prior.get("signal_state", state)
                    trigger = prior.get("trigger", trigger)
                    seen_pullback = prior.get("seen_pullback", seen_pullback)
                snapshot = {**{k: v for k, v in quote.items() if k != "timestamp"},
                            "quote_timestamp": quote_time, "state": state, "signal_state": state,
                            "seen_pullback": seen_pullback, "trigger": trigger}
                stocks[symbol] = snapshot
                if not duplicate:
                    prior_time = prior.get("quote_timestamp")
                    rising = prior_time and prior_time < quote_time and prior.get("volume") is not None
                    volume_delta = quote["volume"] - prior["volume"] if rising else None
                    amount_delta = quote["turnover_amount"] - prior["turnover_amount"] if rising else None
                    record = {**snapshot, "timestamp": quote_time,
                              "interval_volume": volume_delta if volume_delta is not None and volume_delta >= 0 else None,
                              "interval_turnover_amount": amount_delta if amount_delta is not None and amount_delta >= 0 else None,
                              "interval_seconds": int((quote["timestamp"] - datetime.fromisoformat(prior_time)).total_seconds()) if rising else None}
                    history.append(record)
                    if state != prior.get("signal_state", "NORMAL") and state != "NORMAL":
                        event = {"timestamp": quote_time, "symbol": symbol, "name": stock["name"],
                                 "event": "PRE_ALERT_BREAKOUT" if state == "PRE_ALERT" else state,
                                 "price": quote["price"], "trigger": trigger}
                        events.append(event)
                        print(f"[{now:%H:%M:%S}] {symbol} event: {event['event']}")
                print(f"[{now:%H:%M:%S}] {symbol} {quote['source']} price={quote['price']} volume={quote['volume']} state={state}")
            except (ValueError, KeyError, IndexError, OSError, TimeoutError) as exc:
                failures += 1
                errors[symbol] = str(exc)
                stocks[symbol] = {**previous, "symbol": symbol, "code": stock["code"], "name": stock["name"],
                                  "state": "ERROR", "fresh": False, "error": str(exc)}
                print(f"[{now:%H:%M:%S}] {symbol} ERROR: {ascii(str(exc))}")
    else:
        print(f"[{now:%H:%M:%S}] monitor disabled or market closed")

    new_status = {"status": "DISABLED" if not enabled else "CLOSED" if not open_now else "IDLE" if not active else
                  "OK" if failures == 0 else "PARTIAL_ERROR" if success else "ERROR",
                  "monitor_enabled": enabled, "market_open": open_now, "enabled_stocks": len(active),
                  "success_count": success, "failed_count": failures, "errors": errors}
    if open_now or {k: v for k, v in status.items() if k != "generated_at"} != new_status or latest.get("stocks") != stocks:
        latest = {"generated_at": stamp, "monitor_enabled": enabled, "stocks": stocks}
        status = {"generated_at": stamp, **new_status}
        documents = {"latest": latest, "history": history[-monitor["output"]["history_max_records"]:],
                     "events": events[-monitor["output"]["event_max_records"]:], "status": status}
        _save(root, documents)
    return new_status


if __name__ == "__main__":
    run()
