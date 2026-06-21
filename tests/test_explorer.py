import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import explorer as E

def _t(setup, direction, hour, pnl, regime):
    return {"status":"closed","hunter_setup":{"סוג":setup,"כיוון":direction},
            "simulation":{"pnl_pct":pnl},"timestamp_analyzed":f"2026-05-20 {hour:02d}:00:00","regime":regime}

def test_strong_uncovered_cell_is_candidate():
    # Breakout SHORT in NY, bear — NOT a blessed library combo; strong stats
    trades = [_t("Breakout","SHORT",14, 0.4, "bear") for _ in range(13)]   # 13 wins
    trades += [_t("Breakout","SHORT",14, -0.2, "bear") for _ in range(3)]  # 3 losses → n=16, WR=81%, EV~+0.29%
    cands = E.discover_candidates(trades)
    hit = [c for c in cands if c["setup"]=="Breakout" and c["direction"]=="SHORT" and c["required_regime"]=="bear"]
    assert len(hit)==1 and hit[0]["session"]=="NY", cands

def test_below_sample_not_candidate():
    trades = [_t("Breakout","SHORT",14, 0.4, "bear") for _ in range(10)]  # n=10 < 15
    assert E.discover_candidates(trades) == []

def test_low_wr_not_candidate():
    trades = [_t("Breakout","SHORT",14, 0.4, "bear") for _ in range(8)]
    trades += [_t("Breakout","SHORT",14, -0.4, "bear") for _ in range(8)]  # WR 50%
    assert E.discover_candidates(trades) == []

def test_unknown_regime_skipped():
    trades = [_t("Breakout","SHORT",14, 0.4, "unknown") for _ in range(16)]
    assert E.discover_candidates(trades) == []

def test_already_covered_skipped():
    # NY Pullback LONG IS a blessed strategy (NY Pullback Trader) → must be skipped
    trades = [_t("Pullback","LONG",15, 0.5, "bull") for _ in range(16)]
    assert all(not (c["setup"]=="Pullback" and c["direction"]=="LONG") for c in E.discover_candidates(trades))

if __name__ == "__main__":
    fns=[v for k,v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns: fn(); print(f"PASS {fn.__name__}")
    print(f"\nALL {len(fns)} TESTS PASSED")
