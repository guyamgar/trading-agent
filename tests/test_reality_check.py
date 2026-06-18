import os, sys, json
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import reality_check as RC

def _trade(setup, direction, hour, pnl, live):
    t = {"status": "closed", "hunter_setup": {"סוג": setup, "כיוון": direction},
         "simulation": {"pnl_pct": pnl}, "timestamp_analyzed": f"2026-05-20 {hour:02d}:00:00"}
    if live: t["live_mode"] = True
    return t

def test_attribute_known_strategy():
    # NY Pullback LONG (hour 15) → "NY Pullback Trader"
    assert RC.attribute_strategy(datetime(2026,1,1,15,5), "Pullback", "LONG") == "NY Pullback Trader"

def test_attribute_experimental():
    assert RC.attribute_strategy(datetime(2026,1,1,15,5), "Engulfing", "SHORT") == "_experimental_"

def test_dangerous_gap_flagged():
    # one strategy: paper positive, live negative, n_live>=8 → dangerous
    trades = [_trade("Pullback","LONG",15, 0.5, False) for _ in range(10)]
    trades += [_trade("Pullback","LONG",15, -0.3, True) for _ in range(8)]
    gaps = RC.compute_strategy_gaps(trades)
    assert "NY Pullback Trader" in gaps
    assert RC.detect_dangerous(gaps) == ["NY Pullback Trader"]

def test_small_live_sample_not_flagged():
    trades = [_trade("Pullback","LONG",15, 0.5, False) for _ in range(10)]
    trades += [_trade("Pullback","LONG",15, -0.3, True) for _ in range(3)]  # n_live<8
    assert RC.detect_dangerous(RC.compute_strategy_gaps(trades)) == []

def test_graduated_decay_and_recovery():
    state = {}
    s1 = RC.update_live_mults(["NY Pullback Trader"], ["NY Pullback Trader"], state)
    assert abs(s1["live_size_mult"]["NY Pullback Trader"] - 0.7) < 1e-9
    s2 = RC.update_live_mults(["NY Pullback Trader"], ["NY Pullback Trader"], s1)
    assert abs(s2["live_size_mult"]["NY Pullback Trader"] - 0.49) < 1e-9
    # not dangerous this round → recovery ×1.15
    s3 = RC.update_live_mults([], ["NY Pullback Trader"], s2)
    assert abs(s3["live_size_mult"]["NY Pullback Trader"] - 0.49*1.15) < 1e-9

def test_decay_floor_and_cap():
    state = {"live_size_mult": {"X": 0.12}}
    s = RC.update_live_mults(["X"], ["X"], state)
    assert s["live_size_mult"]["X"] >= 0.1  # floor
    state2 = {"live_size_mult": {"X": 0.95}}
    s2 = RC.update_live_mults([], ["X"], state2)
    assert s2["live_size_mult"]["X"] <= 1.0  # cap

if __name__ == "__main__":
    fns = [v for k,v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns: fn(); print(f"PASS {fn.__name__}")
    print(f"\nALL {len(fns)} TESTS PASSED")
