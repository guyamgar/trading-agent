import os, sys
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live_monitor import check_rec_status

def _rec(direction="LONG"):
    opened = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    return {"direction": direction, "entry": 100.0, "stop": 98.0,
            "target_1": 103.0, "target_2": 105.0, "opened_at": opened,
            "max_wait_minutes": 24 * 60}

def test_minutes_held_present_on_target_hit():
    # candle high reaches target_1 (103) for a LONG → closes target_1
    res = check_rec_status(_rec("LONG"), current_price=103.0, candle_high=103.5, candle_low=101.0)
    assert res["closed"] is True, res
    assert "minutes_held" in res, "minutes_held missing (the bug)"
    assert res["minutes_held"] >= 100, res  # ~120 min

def test_minutes_held_present_on_stop_hit():
    res = check_rec_status(_rec("LONG"), current_price=97.0, candle_high=99.0, candle_low=97.0)
    assert res["outcome"] == "stop", res
    assert res["minutes_held"] >= 100, res

if __name__ == "__main__":
    test_minutes_held_present_on_target_hit(); print("PASS test_minutes_held_present_on_target_hit")
    test_minutes_held_present_on_stop_hit(); print("PASS test_minutes_held_present_on_stop_hit")
    print("\nALL TESTS PASSED")
