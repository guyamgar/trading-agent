import os, sys
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-import")  # avoid client init failure on import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.orchestrator import run_committee

DOWN_MS = {"timestamp": "2026-05-28 09:00:00", "trend": "יורד",
           "indicators": {"ema_50": 70000.0, "ema_200": 76000.0}}
UP_MS = {"timestamp": "2026-05-20 12:00:00", "trend": "עולה",
         "indicators": {"ema_50": 78000.0, "ema_200": 72000.0}}

def test_long_into_downtrend_is_vetoed_without_llm():
    res = run_committee(DOWN_MS, setup={"סוג": "Pullback", "כיוון": "LONG"}, verbose=False)
    parsed = res["head_decision"]["parsed"]
    assert parsed["החלטה"] == "אין כניסה", parsed
    assert parsed.get("_regime_rejected") is True, parsed
    assert res["totals"]["cost_usd"] == 0.0, "veto must be pre-LLM (zero cost)"

def test_short_into_downtrend_is_allowed_to_proceed():
    # SHORT in a downtrend must NOT be regime-vetoed (it may still be rejected by the LLM,
    # but it must not carry the _regime_rejected flag).
    res = run_committee(DOWN_MS, setup={"סוג": "Pullback", "כיוון": "SHORT"}, verbose=False)
    assert res["head_decision"]["parsed"].get("_regime_rejected") is not True

if __name__ == "__main__":
    # Only the veto test is fully offline; run it as the gate regression.
    test_long_into_downtrend_is_vetoed_without_llm()
    print("PASS test_long_into_downtrend_is_vetoed_without_llm")
    print("\nGATE VETO TEST PASSED (run the SHORT test manually — it makes live LLM calls)")
