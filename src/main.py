from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from config import load_config, market_is_open
from market import fetch_quote
from state import evaluate
from storage import read_json, write_json


ROOT = Path(__file__).resolve().parents[1]
FILES = ("latest", "history", "events", "status")


def save_documents(root, documents, session_id):
    for name in FILES:
        value = documents[name]
        write_json(root / "data" / f"{name}.json", value)
        write_json(root / "docs" / f"{name}.json", value)
        write_json(root / "data" / "sessions" / session_id / f"{name}.json", value)


def new_documents(session):
    status = {
        "session_status": "RUNNING",
        "session_id": session["session_id"],
        "started_at": session["started_at"],
        "until": session["until"],
        "interval_minutes": session["interval_minutes"],
        "selected_stocks": session["selected_stocks"],
        "last_sample_at": None,
        "success_count": 0,
        "failed_count": 0,
        "errors": {},
    }
    return {
        "latest": {"generated_at": session["started_at"], "session": {**session, "status": "RUNNING"}, "stocks": {}},
        "history": [],
        "events": [],
        "status": status,
    }


def finish_session(root, session, now):
    directory = root / "data" / "sessions" / session["session_id"]
    defaults = new_documents(session)
    documents = {name: read_json(directory / f"{name}.json", defaults[name]) for name in FILES}
    stamp = now.isoformat(timespec="seconds")
    documents["latest"]["generated_at"] = stamp
    documents["latest"]["session"]["status"] = "FINISHED"
    documents["status"]["session_status"] = "FINISHED"
    documents["status"]["finished_at"] = stamp
    save_documents(root, documents, session["session_id"])


def run_sample(root, selected_symbols, session, now=None, providers=None):
    config = load_config(root / "config.yaml")
    monitor = config["monitor"]
    zone = ZoneInfo(monitor["timezone"])
    now = now.astimezone(zone) if now else datetime.now(zone)
    stamp = now.isoformat(timespec="seconds")
    catalog = {stock["symbol"]: stock for stock in config["stocks"]}
    active = [catalog[symbol] for symbol in selected_symbols]
    session_dir = root / "data" / "sessions" / session["session_id"]
    defaults = new_documents(session)
    latest = read_json(session_dir / "latest.json", defaults["latest"])
    history = read_json(session_dir / "history.json", defaults["history"])
    events = read_json(session_dir / "events.json", defaults["events"])
    stocks = latest.get("stocks", {})
    success = 0
    failures = 0
    errors = {}

    if not market_is_open(now, monitor):
        raise ValueError("当前时间不在 A 股交易时段")

    print(f"[{now:%H:%M:%S}] sampling {len(active)} stocks")
    for stock in active:
        symbol = stock["symbol"]
        previous = stocks.get(symbol, {})
        try:
            quote = fetch_quote(stock, now, providers)
            quote_time = quote["timestamp"].isoformat(timespec="seconds")
            if previous.get("quote_timestamp", "") > quote_time:
                raise ValueError("行情时间早于上次有效报价")

            previous_sample = previous.get("sample_timestamp")
            same_day = previous.get("quote_timestamp", "")[:10] == quote_time[:10]
            prior = previous if same_day else {}
            sample_interval = None
            if previous_sample:
                sample_interval = int((now - datetime.fromisoformat(previous_sample)).total_seconds())

            if quote["freshness"] == "stale":
                failures += 1
                errors[symbol] = f"行情过期 {quote['data_age_seconds']} 秒"
                snapshot = {
                    **{k: v for k, v in quote.items() if k != "timestamp"},
                    "code": stock["code"], "name": stock["name"], "symbol": symbol,
                    "quote_timestamp": quote_time, "sample_timestamp": stamp,
                    "previous_sample_timestamp": previous_sample,
                    "sample_interval_seconds": sample_interval,
                    "state": "STALE", "signal_state": prior.get("signal_state", "NORMAL"),
                    "seen_pullback": prior.get("seen_pullback", False),
                    "trigger": prior.get("trigger"), "error": errors[symbol],
                }
            else:
                success += 1
                state, trigger, seen_pullback = evaluate(stock, quote, prior)
                prior_price = prior.get("price")
                price_delta = quote["price"] - prior_price if prior_price is not None else None
                volume_delta = quote["volume"] - prior["volume"] if prior.get("volume") is not None else None
                amount_delta = quote["turnover_amount"] - prior["turnover_amount"] if prior.get("turnover_amount") is not None else None
                snapshot = {
                    **{k: v for k, v in quote.items() if k != "timestamp"},
                    "quote_timestamp": quote_time, "sample_timestamp": stamp,
                    "previous_sample_timestamp": previous_sample,
                    "sample_interval_seconds": sample_interval,
                    "interval_price_change": round(price_delta, 4) if price_delta is not None else None,
                    "interval_price_change_percent": round(price_delta / prior_price * 100, 4) if prior_price else None,
                    "interval_volume": volume_delta if volume_delta is not None and volume_delta >= 0 else None,
                    "interval_turnover_amount": amount_delta if amount_delta is not None and amount_delta >= 0 else None,
                    "state": state, "signal_state": state,
                    "seen_pullback": seen_pullback, "trigger": trigger,
                }
                if state != prior.get("signal_state", "NORMAL") and state != "NORMAL":
                    event_name = "PRE_ALERT_BREAKOUT" if state == "PRE_ALERT" else state
                    events.append({"timestamp": stamp, "quote_timestamp": quote_time,
                                   "symbol": symbol, "name": stock["name"],
                                   "event": event_name, "price": quote["price"], "trigger": trigger})
                    print(f"[{now:%H:%M:%S}] {symbol} event: {event_name}")
            stocks[symbol] = snapshot
            history.append(dict(snapshot))
            print(f"[{now:%H:%M:%S}] {symbol} {snapshot.get('source')} price={snapshot.get('price')} state={snapshot['state']}")
        except (ValueError, KeyError, IndexError, OSError, TimeoutError) as exc:
            failures += 1
            errors[symbol] = str(exc)
            snapshot = {
                **previous, "symbol": symbol, "code": stock["code"], "name": stock["name"],
                "sample_timestamp": stamp, "state": "ERROR", "fresh": False, "error": str(exc),
            }
            stocks[symbol] = snapshot
            history.append(dict(snapshot))
            print(f"[{now:%H:%M:%S}] {symbol} ERROR: {ascii(str(exc))}")

    status = {
        "session_status": "RUNNING", "session_id": session["session_id"],
        "started_at": session["started_at"], "until": session["until"],
        "interval_minutes": session["interval_minutes"],
        "selected_stocks": selected_symbols, "last_sample_at": stamp,
        "success_count": success, "failed_count": failures, "errors": errors,
    }
    documents = {
        "latest": {"generated_at": stamp, "session": {**session, "status": "RUNNING"}, "stocks": stocks},
        "history": history[-monitor["output"]["history_max_records"]:],
        "events": events[-monitor["output"]["event_max_records"]:],
        "status": status,
    }
    save_documents(root, documents, session["session_id"])
    return status
