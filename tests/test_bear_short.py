import os, sys
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from strategies import classify_trade_intent

def _ms(e50, e200):  # market_summary with the EMA-cross signal
    return {"trend": "?", "indicators": {"ema_50": e50, "ema_200": e200}}
DOWN = _ms(100.0, 110.0)  # ema50 < ema200 → confirmed downtrend
UP = _ms(110.0, 100.0)    # ema50 > ema200 → not a downtrend
def _ts(hour):
    return datetime(2026, 1, 1, hour, 5)  # minute 5 = not a candle-open minute

def test_asia_pullback_short_blessed_in_downtrend():
    r = classify_trade_intent(_ts(3), "Pullback", "SHORT", DOWN)
    assert r["kind"] == "blessed", r
    assert r["strategy_name"] == "Bear Short", r

def test_night_bounce_short_blessed_in_downtrend():
    r = classify_trade_intent(_ts(22), "Bounce", "SHORT", DOWN)
    assert r["kind"] == "blessed" and r["strategy_name"] == "Bear Short", r

def test_short_not_blessed_when_not_downtrend():
    r = classify_trade_intent(_ts(3), "Pullback", "SHORT", UP)
    assert r["kind"] == "experimental", r  # Bear Short skipped, no other Asia-short match

def test_short_not_blessed_when_market_summary_missing():
    r = classify_trade_intent(_ts(3), "Pullback", "SHORT")  # no market_summary
    assert r["kind"] == "experimental", r

def test_ny_pullback_short_keeps_existing_strategy():
    # NY Counter-Trend Short (13-21 Pullback SHORT) has precedence over Bear Short
    r = classify_trade_intent(_ts(15), "Pullback", "SHORT", DOWN)
    assert r["kind"] == "blessed" and r["strategy_name"] == "NY Counter-Trend Short", r

def test_london_pullback_short_still_anti():
    # ANTI precedence preserved even in a downtrend
    r = classify_trade_intent(_ts(9), "Pullback", "SHORT", DOWN)
    assert r["kind"] == "anti", r

if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print(f"PASS {fn.__name__}")
    print(f"\nALL {len(fns)} TESTS PASSED")
