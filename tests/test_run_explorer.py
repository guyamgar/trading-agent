import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import explorer as E

def _t(setup, direction, hour, pnl, regime):
    return {"status":"closed","hunter_setup":{"סוג":setup,"כיוון":direction},
            "simulation":{"pnl_pct":pnl},"timestamp_analyzed":f"2026-05-20 {hour:02d}:00:00","regime":regime}

def test_run_explorer_promotes_and_dedupes():
    E.DISCOVERED_FILE.unlink(missing_ok=True)
    trades = [_t("Breakout","SHORT",14, 0.4, "bear") for _ in range(14)]
    trades += [_t("Breakout","SHORT",14, -0.2, "bear") for _ in range(2)]  # n=16 WR 87.5% EV +0.325%
    import memory_store  # run_explorer reads trades via load_trades; monkeypatch
    orig = memory_store.load_trades; memory_store.load_trades = lambda: trades
    try:
        s1 = E.run_explorer()
        assert len(s1["promoted"]) == 1, s1
        disc = json.loads(E.DISCOVERED_FILE.read_text())
        rec = disc[0]
        assert rec["position_size_mult"] == 0.5 and rec["required_regime"] == "bear" and rec["kind"]=="blessed"
        assert rec["allowed_setups"]==["Breakout"] and rec["allowed_directions"]==["SHORT"]
        # second run → no duplicate promotion
        s2 = E.run_explorer()
        assert s2["promoted"] == [], s2
        assert len(json.loads(E.DISCOVERED_FILE.read_text())) == 1
        assert "Breakout" in E.format_explorer_alert(s1)
    finally:
        memory_store.load_trades = orig
        E.DISCOVERED_FILE.unlink(missing_ok=True)

if __name__ == "__main__":
    test_run_explorer_promotes_and_dedupes(); print("PASS run_explorer")
    print("\nALL TESTS PASSED")
