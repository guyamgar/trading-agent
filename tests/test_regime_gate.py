import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from regime_gate import regime_gate_veto

def _ms(trend, ema50, ema200):
    return {"trend": trend, "indicators": {"ema_50": ema50, "ema_200": ema200}}

def test_long_vetoed_in_confirmed_downtrend():
    assert regime_gate_veto("LONG", _ms("יורד", 100.0, 110.0)) is not None

def test_long_allowed_in_uptrend():
    assert regime_gate_veto("LONG", _ms("עולה", 110.0, 100.0)) is None

def test_long_allowed_in_mixed():
    assert regime_gate_veto("LONG", _ms("מעורבב", 100.0, 110.0)) is None

def test_long_not_vetoed_when_ema_not_crossed():
    # trend says down but EMA50 still above EMA200 → unconfirmed → no veto
    assert regime_gate_veto("LONG", _ms("יורד", 110.0, 100.0), require_ema_cross=True) is None

def test_short_vetoed_in_confirmed_uptrend():
    assert regime_gate_veto("SHORT", _ms("עולה", 110.0, 100.0)) is not None

def test_short_allowed_in_downtrend():
    assert regime_gate_veto("SHORT", _ms("יורד", 100.0, 110.0)) is None

def test_empty_direction_is_safe():
    assert regime_gate_veto("", _ms("יורד", 100.0, 110.0)) is None

if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print(f"PASS {fn.__name__}")
    print(f"\nALL {len(fns)} TESTS PASSED")
