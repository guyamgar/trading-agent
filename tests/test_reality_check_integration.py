import os, sys, json
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from strategies import classify_trade_intent
import reality_check as RC

def test_blessed_size_scaled_by_live_mult(tmp_state=RC.STATE_FILE):
    # write a live mult of 0.5 for NY Pullback Trader
    RC.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    RC.STATE_FILE.write_text(json.dumps({"live_size_mult": {"NY Pullback Trader": 0.5}}))
    try:
        r = classify_trade_intent(datetime(2026,1,1,15,5), "Pullback", "LONG")
        # base mult for NY Pullback Trader is 1.3; ×0.5 = 0.65 (no candle-open boost at minute 5)
        assert abs(r["size_mult"] - 0.65) < 1e-6, f"Expected 0.65, got {r['size_mult']}: {r}"
    finally:
        RC.STATE_FILE.unlink(missing_ok=True)

def test_default_mult_when_no_state():
    RC.STATE_FILE.unlink(missing_ok=True)
    r = classify_trade_intent(datetime(2026,1,1,15,5), "Pullback", "LONG")
    assert abs(r["size_mult"] - 1.3) < 1e-6, f"Expected 1.3, got {r['size_mult']}: {r}"  # base, unscaled

if __name__ == "__main__":
    test_default_mult_when_no_state(); print("PASS default")
    test_blessed_size_scaled_by_live_mult(); print("PASS scaled")
    print("\nALL TESTS PASSED")
