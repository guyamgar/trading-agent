import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from strategies import classify_regime

def _ms(e9, e21, e50, e200, trend="?"):
    return {"trend": trend, "indicators": {"ema_9": e9, "ema_21": e21, "ema_50": e50, "ema_200": e200}}

def test_bull():
    assert classify_regime(_ms(110, 108, 105, 100)) == "bull"   # 9>21>50 and 50>200

def test_bear():
    assert classify_regime(_ms(90, 92, 95, 100)) == "bear"      # 9<21<50 and 50<200

def test_range_mixed():
    assert classify_regime(_ms(100, 99, 101, 100)) == "range"   # not strictly ordered

def test_range_when_ema_aligned_but_no_long_cross():
    assert classify_regime(_ms(110, 108, 105, 106)) == "range"  # 9>21>50 but 50<200 → not bull

def test_missing_indicators_defaults_range():
    assert classify_regime({}) == "range"
    assert classify_regime(None) == "range"

if __name__ == "__main__":
    fns = [v for k,v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns: fn(); print(f"PASS {fn.__name__}")
    print(f"\nALL {len(fns)} TESTS PASSED")
