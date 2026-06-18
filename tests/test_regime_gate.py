import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from regime_gate import regime_gate_veto

def _ms(trend, ema50, ema200):
    return {"trend": trend, "indicators": {"ema_50": ema50, "ema_200": ema200}}

def _ms_no_emas(trend):
    return {"trend": trend, "indicators": {}}

# ── ema_cross mode (default, R1) ─────────────────────────────────────────────

def test_ema_cross_long_vetoed_on_death_cross():
    # ema50 < ema200 → LONG vetoed regardless of trend label
    assert regime_gate_veto("LONG", _ms("מעורבב", 100.0, 110.0)) is not None

def test_ema_cross_long_vetoed_in_confirmed_downtrend():
    assert regime_gate_veto("LONG", _ms("יורד", 100.0, 110.0)) is not None

def test_ema_cross_long_allowed_when_ema50_above_ema200():
    # No death cross → no veto even if trend label says down
    assert regime_gate_veto("LONG", _ms("יורד", 110.0, 100.0)) is None

def test_ema_cross_long_allowed_in_uptrend_with_golden_cross():
    assert regime_gate_veto("LONG", _ms("עולה", 110.0, 100.0)) is None

def test_ema_cross_short_vetoed_on_golden_cross():
    # ema50 > ema200 → SHORT vetoed regardless of trend label
    assert regime_gate_veto("SHORT", _ms("מעורבב", 110.0, 100.0)) is not None

def test_ema_cross_short_vetoed_in_confirmed_uptrend():
    assert regime_gate_veto("SHORT", _ms("עולה", 110.0, 100.0)) is not None

def test_ema_cross_short_allowed_when_ema50_below_ema200():
    # No golden cross → no veto
    assert regime_gate_veto("SHORT", _ms("יורד", 100.0, 110.0)) is None

def test_ema_cross_absent_emas_no_veto():
    # Missing EMA values → safe default, no veto
    assert regime_gate_veto("LONG", _ms_no_emas("יורד")) is None
    assert regime_gate_veto("SHORT", _ms_no_emas("עולה")) is None

def test_empty_direction_is_safe():
    assert regime_gate_veto("", _ms("יורד", 100.0, 110.0)) is None

# ── trend_and_cross mode (R0, preserved for comparison) ──────────────────────

def test_trend_and_cross_long_vetoed_in_confirmed_downtrend():
    # trend=down AND cross-down → veto
    assert regime_gate_veto("LONG", _ms("יורד", 100.0, 110.0), signal="trend_and_cross") is not None

def test_trend_and_cross_long_not_vetoed_mixed_with_cross_down():
    # trend=mixed, even though cross-down — NOT vetoed in trend_and_cross mode
    assert regime_gate_veto("LONG", _ms("מעורבב", 100.0, 110.0), signal="trend_and_cross") is None

def test_trend_and_cross_long_not_vetoed_when_ema_not_crossed():
    # trend says down but ema50 still above ema200 → unconfirmed → no veto
    assert regime_gate_veto("LONG", _ms("יורד", 110.0, 100.0), signal="trend_and_cross") is None

def test_trend_and_cross_short_vetoed_in_confirmed_uptrend():
    assert regime_gate_veto("SHORT", _ms("עולה", 110.0, 100.0), signal="trend_and_cross") is not None

def test_trend_and_cross_short_allowed_in_downtrend():
    assert regime_gate_veto("SHORT", _ms("יורד", 100.0, 110.0), signal="trend_and_cross") is None

if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print(f"PASS {fn.__name__}")
    print(f"\nALL {len(fns)} TESTS PASSED")
