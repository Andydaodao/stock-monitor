import os
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from config import load_config
from main import finish_session, new_documents, run_sample, save_documents


ROOT = Path(__file__).resolve().parents[1]
MAX_SESSION_MINUTES = 340


def parse_symbols(value, catalog):
    symbols = []
    for part in value.split(","):
        symbol = part.strip().upper()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    if not symbols:
        raise ValueError("stocks 不能为空")
    unknown = [symbol for symbol in symbols if symbol not in catalog]
    if unknown:
        raise ValueError("INVALID_STOCK: " + ", ".join(unknown))
    return symbols


def resolve_until(value, now):
    try:
        end_time = datetime.strptime(value.strip(), "%H:%M").time()
    except ValueError:
        raise ValueError("until 必须使用 HH:MM，例如 11:30 或 15:00") from None
    until = datetime.combine(now.date(), end_time, tzinfo=now.tzinfo)
    if until <= now:
        raise ValueError("Session end time already passed")
    if until - now > timedelta(minutes=MAX_SESSION_MINUTES):
        raise ValueError(f"Session 最长支持 {MAX_SESSION_MINUTES} 分钟")
    return until


def next_market_time(now, until, market_sessions):
    for period in market_sessions:
        start = datetime.combine(now.date(), datetime.strptime(period["start"], "%H:%M").time(), tzinfo=now.tzinfo)
        end = datetime.combine(now.date(), datetime.strptime(period["end"], "%H:%M").time(), tzinfo=now.tzinfo)
        if now < start:
            return start if start < until else None
        if start <= now < end:
            return now
    return None


def persist(root, session_id):
    paths = ["data", "docs/latest.json", "docs/history.json", "docs/events.json", "docs/status.json"]
    subprocess.run(["git", "add", *paths], cwd=root, check=True)
    changed = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=root).returncode != 0
    if not changed:
        return
    subprocess.run(["git", "commit", "-m", f"chore: update stock session {session_id}"], cwd=root, check=True)
    subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=root, check=True)
    subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=root, check=True)


def run_session(root=ROOT):
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        raise RuntimeError("实时行情 Session 只能在 GitHub-hosted Actions runner 中运行")

    config = load_config(root / "config.yaml")
    monitor = config["monitor"]
    zone = ZoneInfo(monitor["timezone"])
    now = datetime.now(zone)
    if monitor.get("trading_day_only", True) and now.weekday() >= 5:
        raise ValueError("今天不是交易日（当前版本按周一至周五判断）")

    catalog = {stock["symbol"]: stock for stock in config["stocks"]}
    symbols = parse_symbols(os.environ.get("SESSION_STOCKS", ""), catalog)
    until = resolve_until(os.environ.get("SESSION_UNTIL", ""), now)
    try:
        interval = int(os.environ.get("SESSION_INTERVAL_MINUTES", "5"))
    except ValueError:
        raise ValueError("interval_minutes 必须是整数") from None
    if interval < 5:
        raise ValueError("interval_minutes 不能小于 5")

    session_id = now.strftime("%Y%m%d-%H%M%S")
    session = {
        "session_id": session_id,
        "started_at": now.isoformat(timespec="seconds"),
        "until": until.isoformat(timespec="seconds"),
        "interval_minutes": interval,
        "selected_stocks": symbols,
    }
    documents = new_documents(session)
    save_documents(root, documents, session_id)
    persist(root, session_id)
    print(f"Session {session_id} started: {', '.join(symbols)} until {until:%H:%M}")

    while True:
        now = datetime.now(zone)
        if now >= until:
            break
        sample_time = next_market_time(now, until, monitor["market_sessions"])
        if sample_time is None:
            break
        if sample_time > now:
            seconds = (sample_time - now).total_seconds()
            print(f"Market closed; waiting {int(seconds)} seconds until {sample_time:%H:%M}")
            time.sleep(seconds)
            continue

        run_sample(root, symbols, session, now)
        persist(root, session_id)
        remaining = (until - datetime.now(zone)).total_seconds()
        if remaining <= 0:
            break
        time.sleep(min(interval * 60, remaining))

    finished_at = datetime.now(zone)
    finish_session(root, session, finished_at)
    persist(root, session_id)
    print(f"Session {session_id} finished at {finished_at:%H:%M:%S}")


if __name__ == "__main__":
    run_session()
