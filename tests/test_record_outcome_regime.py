import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import strategies as S

def test_record_trade_outcome_per_regime():
    S.STRATEGIES_STATS_FILE.write_text("{}")
    S.record_trade_outcome("NY Pullback Trader", 0.5, regime="bull")
    S.record_trade_outcome("NY Pullback Trader", -0.3, regime="bear")
    stats = json.loads(S.STRATEGIES_STATS_FILE.read_text())
    s = stats["NY Pullback Trader"]
    assert s["trades"] == 2  # main bucket unchanged behavior
    assert s["by_regime"]["bull"]["trades"] == 1 and s["by_regime"]["bull"]["wins"] == 1
    assert s["by_regime"]["bear"]["trades"] == 1 and s["by_regime"]["bear"]["losses"] == 1

def test_record_trade_outcome_no_regime_still_works():
    S.STRATEGIES_STATS_FILE.write_text("{}")
    S.record_trade_outcome("Night Owl", 0.4)  # no regime arg
    stats = json.loads(S.STRATEGIES_STATS_FILE.read_text())
    assert stats["Night Owl"]["trades"] == 1
    assert "by_regime" not in stats["Night Owl"] or stats["Night Owl"]["by_regime"] == {}

if __name__ == "__main__":
    test_record_trade_outcome_per_regime(); print("PASS per_regime")
    test_record_trade_outcome_no_regime_still_works(); print("PASS no_regime")
    print("\nALL TESTS PASSED")
