import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import reality_check as RC

def test_run_reality_check_persists_and_summarizes(monkeypatch=None):
    # synthesize a dangerous gap via a temp trades list by monkeypatching load_trades
    import memory_store
    trades = [{"status":"closed","hunter_setup":{"סוג":"Pullback","כיוון":"LONG"},
               "simulation":{"pnl_pct":0.5},"timestamp_analyzed":"2026-05-20 15:00:00"} for _ in range(10)]
    trades += [{"status":"closed","hunter_setup":{"סוג":"Pullback","כיוון":"LONG"},
               "simulation":{"pnl_pct":-0.3},"timestamp_analyzed":"2026-05-20 15:00:00","live_mode":True} for _ in range(8)]
    orig = memory_store.load_trades
    memory_store.load_trades = lambda: trades
    RC.STATE_FILE.unlink(missing_ok=True)
    try:
        summary = RC.run_reality_check()
        assert "NY Pullback Trader" in summary["dangerous"], summary
        # state persisted with decayed mult
        st = json.loads(RC.STATE_FILE.read_text())
        assert st["live_size_mult"]["NY Pullback Trader"] < 1.0
        alert = RC.format_alert(summary)
        assert "NY Pullback Trader" in alert and "%" in alert
    finally:
        memory_store.load_trades = orig
        RC.STATE_FILE.unlink(missing_ok=True)

if __name__ == "__main__":
    test_run_reality_check_persists_and_summarizes(); print("PASS run_reality_check")
    print("\nALL TESTS PASSED")
