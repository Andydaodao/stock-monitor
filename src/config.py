from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml


def _clock(value):
    if not isinstance(value, str):
        raise ValueError("交易时间必须写成 HH:MM")
    return time.fromisoformat(value)


def load_config(path: Path):
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or not isinstance(config.get("monitor"), dict):
        raise ValueError("config.yaml 缺少 monitor")
    monitor = config["monitor"]
    ZoneInfo(monitor.get("timezone", "Asia/Shanghai"))
    if monitor.get("interval_minutes") != 5:
        raise ValueError("当前 workflow 仅支持 interval_minutes: 5")
    if not isinstance(monitor.get("enabled"), bool):
        raise ValueError("monitor.enabled 必须是布尔值")
    if not isinstance(monitor.get("market_sessions"), list):
        raise ValueError("monitor.market_sessions 必须是列表")
    for session in monitor["market_sessions"]:
        if _clock(session["start"]) >= _clock(session["end"]):
            raise ValueError("交易时段起点必须早于终点")
    until = monitor.get("monitor_until")
    if until is not None:
        if not isinstance(until, str):
            raise ValueError("monitor_until 必须是字符串或 null")
        if len(until) == 5:
            _clock(until)
        elif datetime.fromisoformat(until).tzinfo is None:
            raise ValueError("monitor_until 的完整日期必须包含时区")
    output = monitor.get("output", {})
    for key in ("history_max_records", "event_max_records"):
        if not isinstance(output.get(key), int) or output[key] < 1:
            raise ValueError(f"monitor.output.{key} 必须是正整数")
    stocks = config.get("stocks")
    if not isinstance(stocks, list):
        raise ValueError("stocks 必须是列表")
    seen = set()
    for stock in stocks:
        code, market, symbol = stock["code"], stock["market"], stock["symbol"]
        if not isinstance(code, str) or len(code) != 6 or not code.isdigit():
            raise ValueError("股票 code 必须是六位字符串")
        if market not in ("SH", "SZ") or symbol != f"{code}.{market}":
            raise ValueError(f"股票 {code} 的 market/symbol 不匹配")
        if symbol in seen:
            raise ValueError(f"重复股票: {symbol}")
        seen.add(symbol)
        if not isinstance(stock.get("enabled"), bool) or not stock.get("name"):
            raise ValueError(f"股票 {symbol} 缺少 name 或 enabled")
        for kind, trigger in stock.get("triggers", {}).items():
            if kind not in ("pullback", "breakout", "invalidation", "strong_invalidation"):
                raise ValueError(f"未知 trigger: {kind}")
            if not isinstance(trigger.get("enabled"), bool):
                raise ValueError(f"{symbol}.{kind}.enabled 必须是布尔值")
            if trigger["enabled"]:
                keys = ("low", "high") if kind == "pullback" else ("price",)
                if any(not isinstance(trigger.get(k), (int, float)) or trigger[k] <= 0 for k in keys):
                    raise ValueError(f"{symbol}.{kind} 缺少有效价位")
                if kind == "pullback" and trigger["low"] >= trigger["high"]:
                    raise ValueError(f"{symbol} 回踩区上下限错误")
        percent = stock.get("alert", {}).get("pre_alert_percent", 1.0)
        if not isinstance(percent, (int, float)) or not 0 <= percent < 100:
            raise ValueError(f"{symbol} 的 pre_alert_percent 无效")
    return config


def is_trading_day(now, monitor):
    return not monitor.get("trading_day_only", True) or now.weekday() < 5


def market_is_open(now, monitor):
    if not is_trading_day(now, monitor):
        return False
    until = monitor.get("monitor_until")
    if until:
        if len(until) == 5 and now.time() > _clock(until):
            return False
        if len(until) != 5 and now > datetime.fromisoformat(until):
            return False
    return any(_clock(s["start"]) <= now.time() < _clock(s["end"]) for s in monitor["market_sessions"])
