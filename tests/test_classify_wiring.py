import os, sys
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-import")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import strategies
from agents.orchestrator import run_committee

DOWN_MS = {"timestamp": "t", "trend": "יורד",
           "indicators": {"ema_50": 100.0, "ema_200": 110.0}}

def test_run_committee_passes_market_summary_to_classifier():
    captured = {}
    orig = strategies.classify_trade_intent
    def spy(ts, setup_type, direction, market_summary=None):
        captured["ms"] = market_summary
        return orig(ts, setup_type, direction, market_summary)
    strategies.classify_trade_intent = spy
    try:
        # LONG in a downtrend: classify runs, then the regime gate vetoes (early return, no LLM).
        run_committee(DOWN_MS, setup={"סוג": "Pullback", "כיוון": "LONG"}, verbose=False)
    finally:
        strategies.classify_trade_intent = orig
    assert captured.get("ms") is DOWN_MS, captured

if __name__ == "__main__":
    test_run_committee_passes_market_summary_to_classifier()
    print("PASS test_run_committee_passes_market_summary_to_classifier")
    print("\nALL TESTS PASSED")
