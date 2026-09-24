import json
import re
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")


def _get(url):
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 stock-monitor/1.0"})
    with urlopen(request, timeout=10) as response:
        return response.read()


def _number(value, field):
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"无效行情字段: {field}") from None
    if result != result or result in (float("inf"), float("-inf")):
        raise ValueError(f"无效行情字段: {field}")
    return result


def _validate(quote, stock):
    if quote["code"] != stock["code"] or quote["price"] <= 0:
        raise ValueError("行情代码或价格无效")
    if quote["volume"] < 0 or quote["turnover_amount"] < 0:
        raise ValueError("行情成交量或成交额无效")
    if quote["volume"] and quote["turnover_amount"] and quote["low"] > 0:
        average = quote["turnover_amount"] / quote["volume"]
        if not quote["low"] * 0.95 <= average <= quote["high"] * 1.05:
            raise ValueError("成交量与成交额口径不一致")
    if quote["timestamp"].tzinfo is None:
        raise ValueError("行情缺少时区")
    return quote


def _eastmoney_volume(raw, amount, low, high):
    if raw == 0 and amount == 0:
        return 0
    for candidate in (raw, raw * 100):
        if candidate > 0 and low > 0 and low * 0.95 <= amount / candidate <= high * 1.05:
            return int(candidate)
    raise ValueError("无法核验东方财富成交量单位")


class MarketDataProvider:
    name = "unknown"

    def get_quote(self, stock):
        raise NotImplementedError


class TencentProvider(MarketDataProvider):
    name = "tencent"

    def get_quote(self, stock):
        prefix = stock["market"].lower()
        body = _get(f"https://qt.gtimg.cn/q={prefix}{stock['code']}").decode("gbk")
        match = re.search(r'="([^"]*)"', body)
        if not match:
            raise ValueError("腾讯行情响应格式错误")
        fields = match.group(1).split("~")
        if len(fields) <= 38:
            raise ValueError("腾讯行情字段不完整")
        stamp = datetime.strptime(fields[30], "%Y%m%d%H%M%S").replace(tzinfo=SHANGHAI)
        amount = _number(fields[35].split("/")[2], "amount")
        quote = {
            "symbol": stock["symbol"], "code": fields[2], "name": fields[1],
            "timestamp": stamp, "price": _number(fields[3], "price"),
            "change": _number(fields[31], "change"),
            "change_percent": _number(fields[32], "change_percent"),
            "open": _number(fields[5], "open"), "high": _number(fields[33], "high"),
            "low": _number(fields[34], "low"),
            "previous_close": _number(fields[4], "previous_close"),
            "volume": int(_number(fields[6], "volume") * 100),
            "turnover_amount": amount,
            "turnover_rate": _number(fields[38], "turnover_rate"),
            "source": self.name,
        }
        return _validate(quote, stock)


class EastmoneyProvider(MarketDataProvider):
    name = "eastmoney"

    def get_quote(self, stock):
        market = "1" if stock["market"] == "SH" else "0"
        params = urlencode({
            "secid": f"{market}.{stock['code']}", "fltt": "2", "invt": "2",
            "fields": "f43,f44,f45,f46,f47,f48,f57,f58,f60,f168,f169,f170,f86",
        })
        body = json.loads(_get(f"https://push2.eastmoney.com/api/qt/stock/get?{params}"))
        data = body.get("data")
        if not isinstance(data, dict):
            raise ValueError("东方财富行情为空")
        stamp = datetime.fromtimestamp(_number(data.get("f86"), "timestamp"), timezone.utc).astimezone(SHANGHAI)
        low = _number(data.get("f45"), "low")
        high = _number(data.get("f44"), "high")
        amount = _number(data.get("f48"), "turnover_amount")
        quote = {
            "symbol": stock["symbol"], "code": str(data.get("f57", "")),
            "name": data.get("f58") or stock["name"], "timestamp": stamp,
            "price": _number(data.get("f43"), "price"),
            "change": _number(data.get("f169"), "change"),
            "change_percent": _number(data.get("f170"), "change_percent"),
            "open": _number(data.get("f46"), "open"),
            "high": high, "low": low,
            "previous_close": _number(data.get("f60"), "previous_close"),
            "volume": _eastmoney_volume(_number(data.get("f47"), "volume"), amount, low, high),
            "turnover_amount": amount,
            "turnover_rate": _number(data.get("f168"), "turnover_rate"),
            "source": self.name,
        }
        return _validate(quote, stock)


def fetch_quote(stock, now, providers=None):
    providers = providers or (TencentProvider(), EastmoneyProvider())
    errors = []
    stale_quote = None
    for provider in providers:
        try:
            quote = provider.get_quote(stock)
            age = int((now - quote["timestamp"]).total_seconds())
            if age < -60:
                raise ValueError("行情时间超前")
            quote["data_age_seconds"] = max(0, age)
            quote["fresh"] = age <= 300
            quote["freshness"] = "fresh" if age <= 300 else "acceptable" if age <= 600 else "stale"
            if age <= 600:
                return quote
            if stale_quote is None or age < stale_quote["data_age_seconds"]:
                stale_quote = quote
            errors.append(f"{provider.name}: 行情过期 {age} 秒")
        except (ValueError, KeyError, IndexError, OSError, TimeoutError) as exc:
            errors.append(f"{provider.name}: {exc}")
    if stale_quote is not None:
        return stale_quote
    raise ValueError("; ".join(errors))
