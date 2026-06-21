import os, sys, json
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import strategies as S

DISC = S.ROOT / "memory" / "discovered_strategies.json"
# bear: ema_9 < ema_21 < ema_50 < ema_200
BEAR = {"trend":"?","indicators":{"ema_9":90,"ema_21":92,"ema_50":95,"ema_200":100}}
# bull: ema_9 > ema_21 > ema_50 > ema_200
BULL = {"trend":"?","indicators":{"ema_9":110,"ema_21":108,"ema_50":105,"ema_200":100}}

# Probe: Asia hour 3, Breakout SHORT.
# No library strategy covers this cell:
#   - Asian Range:  Pullback only, LONG only
#   - Bear Short:   Pullback/Bounce only (no Breakout)
#   - NY ORB:       hours 13-16 (not Asia)
# → genuinely uncovered without a discovered strategy.
PROBE_DT = datetime(2026, 1, 1, 3, 5)  # hour 3 UTC — Asia window
DISCOVERED = [
    {"name": "Explorer: Asia Breakout SHORT (bear)",
     "start_hour_utc": 0, "end_hour_utc": 7,
     "allowed_setups": ["Breakout"], "allowed_directions": ["SHORT"],
     "position_size_mult": 0.5, "kind": "blessed", "required_regime": "bear"}
]

def _write(disc):
    DISC.write_text(json.dumps(disc, ensure_ascii=False))

def test_discovered_matches_only_in_its_regime():
    _write(DISCOVERED)
    try:
        # BEAR regime → discovered strategy is matched
        r = S.classify_trade_intent(PROBE_DT, "Breakout", "SHORT", BEAR)
        assert r["kind"] == "blessed" and r["strategy_name"] == "Explorer: Asia Breakout SHORT (bear)", r
        # BULL regime → regime gate skips it → experimental
        r2 = S.classify_trade_intent(PROBE_DT, "Breakout", "SHORT", BULL)
        assert r2["kind"] == "experimental", r2
    finally:
        DISC.unlink(missing_ok=True)

def test_no_discovered_file_is_safe():
    DISC.unlink(missing_ok=True)
    # No discovered file → Asia Breakout SHORT is experimental (no library match either)
    r = S.classify_trade_intent(PROBE_DT, "Breakout", "SHORT", BEAR)
    assert r["kind"] == "experimental", r

if __name__ == "__main__":
    test_no_discovered_file_is_safe(); print("PASS no_file")
    test_discovered_matches_only_in_its_regime(); print("PASS regime_match")
    print("\nALL TESTS PASSED")
