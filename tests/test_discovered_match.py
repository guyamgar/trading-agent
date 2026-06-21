import os, sys, json
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import strategies as S

DISC = S.ROOT / "memory" / "discovered_strategies.json"
BEAR = {"trend":"?","indicators":{"ema_9":90,"ema_21":92,"ema_50":95,"ema_200":100}}
BULL = {"trend":"?","indicators":{"ema_9":110,"ema_21":108,"ema_50":105,"ema_200":100}}

def _write(disc):
    DISC.write_text(json.dumps(disc, ensure_ascii=False))

def test_discovered_matches_only_in_its_regime():
    _write([{"name":"Explorer: NY Breakout SHORT (bear)","start_hour_utc":13,"end_hour_utc":21,
             "allowed_setups":["Breakout"],"allowed_directions":["SHORT"],
             "position_size_mult":0.5,"kind":"blessed","required_regime":"bear"}])
    try:
        r = S.classify_trade_intent(datetime(2026,1,1,14,5), "Breakout", "SHORT", BEAR)
        assert r["kind"]=="blessed" and r["strategy_name"]=="Explorer: NY Breakout SHORT (bear)", r
        # wrong regime → not matched (experimental)
        r2 = S.classify_trade_intent(datetime(2026,1,1,14,5), "Breakout", "SHORT", BULL)
        assert r2["kind"]=="experimental", r2
    finally:
        DISC.unlink(missing_ok=True)

def test_no_discovered_file_is_safe():
    DISC.unlink(missing_ok=True)
    r = S.classify_trade_intent(datetime(2026,1,1,14,5), "Breakout", "SHORT", BEAR)
    assert r["kind"]=="experimental", r  # nothing matches → experimental, no crash

if __name__ == "__main__":
    test_no_discovered_file_is_safe(); print("PASS no_file")
    test_discovered_matches_only_in_its_regime(); print("PASS regime_match")
    print("\nALL TESTS PASSED")
