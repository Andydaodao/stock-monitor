def _active(stock, name):
    rule = stock.get("triggers", {}).get(name, {})
    return rule if rule.get("enabled") else None


def evaluate(stock, quote, previous=None):
    previous = previous or {}
    prior = previous.get("signal_state", "NORMAL")
    price = quote["price"]
    seen_pullback = previous.get("seen_pullback", False)
    breakout = _active(stock, "breakout")
    pullback = _active(stock, "pullback")

    for name in ("strong_invalidation", "invalidation"):
        rule = _active(stock, name)
        if rule and price < rule["price"]:
            stem = "STRONG_INVALIDATION" if name == "strong_invalidation" else "INVALIDATION"
            state = stem + ("_HOLD" if prior.startswith(stem) else "_TOUCH")
            return state, {"type": name, "price": rule["price"], "distance_percent": round((price / rule["price"] - 1) * 100, 3)}, seen_pullback

    if breakout and price > breakout["price"]:
        state = "BREAKOUT_HOLD" if prior in ("BREAKOUT_TRIGGER", "BREAKOUT_HOLD") else "BREAKOUT_TRIGGER"
        return state, {"type": "breakout", "price": breakout["price"], "distance_percent": round((price / breakout["price"] - 1) * 100, 3)}, seen_pullback
    if breakout and prior in ("BREAKOUT_TRIGGER", "BREAKOUT_HOLD", "BREAKOUT_LOST") and price < breakout["price"]:
        return "BREAKOUT_LOST", {"type": "breakout", "price": breakout["price"], "distance_percent": round((price / breakout["price"] - 1) * 100, 3)}, seen_pullback

    if pullback and pullback["low"] <= price <= pullback["high"]:
        return "PULLBACK_ZONE", {"type": "pullback", "low": pullback["low"], "high": pullback["high"]}, True
    if pullback and seen_pullback and previous.get("price") is not None and previous["price"] <= pullback["high"] < price:
        return "PULLBACK_RECOVERY", {"type": "pullback", "low": pullback["low"], "high": pullback["high"]}, seen_pullback

    if breakout:
        info = {"type": "breakout", "price": breakout["price"], "distance_percent": round((price / breakout["price"] - 1) * 100, 3)}
        if quote["high"] >= breakout["price"] and price <= breakout["price"]:
            return "BREAKOUT_TOUCH", info, seen_pullback
        near = stock.get("alert", {}).get("pre_alert_percent", 1.0)
        if breakout["price"] * (1 - near / 100) <= price < breakout["price"]:
            return "PRE_ALERT", info, seen_pullback
    return "NORMAL", None, seen_pullback
