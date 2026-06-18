# Bear Short Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Pullback/Bounce SHORT a blessed strategy *only when the market is in a confirmed downtrend* (EMA50<EMA200), so the system has a profitable play when the regime gate blocks longs.

**Architecture:** Add a `required_downtrend` flag to the `Strategy` dataclass; make `find_matching_strategy`/`classify_trade_intent` regime-aware by reading the same `market_summary` EMA-cross signal the gate uses (backtest-safe, no look-ahead); add one new `"Bear Short"` strategy entry covering NY+Night+Asia (excluding London); wire `market_summary` from `run_committee` into the classifier; validate by re-classifying the real SHORT trade history.

**Tech Stack:** Python 3.9, pandas. No pytest — tests are runnable assert-scripts (`python3 tests/test_*.py`).

## Global Constraints

- Project root `$ROOT = /Users/guyamgar/Desktop/Agents_markering/trading_agent`. Continue on git branch `regime-gate` (do not merge to main).
- Backtest-safety is sacred: the downtrend signal MUST come only from the passed `market_summary` (`indicators.ema_50 < indicators.ema_200`) — never a live fetch in the classify path. Same signal the regime gate uses (one source of truth).
- Safe default: when `market_summary` is missing/None, a `required_downtrend` strategy is NOT selected (it falls through to experimental). Never bless a regime-gated strategy without regime evidence.
- Hebrew data contract, verbatim: setup type key `"סוג"`, direction key `"כיוון"` (values `"LONG"`/`"SHORT"`); `market_summary["indicators"]["ema_50"]`/`["ema_200"]`.
- Strategy precedence: `find_matching_strategy` returns the FIRST matching entry in `STRATEGY_LIBRARY` order. The new `"Bear Short"` MUST be appended AFTER the existing `"ANTI: London Pullback SHORT"` and `"NY Counter-Trend Short"` so those keep precedence.
- `Bear Short` window is `start_hour_utc=13, end_hour_utc=7` (crosses midnight → active hours 13-23 and 0-6 = NY+Night+Asia; excludes London 7-12). This relies on the existing `is_active_at` midnight-crossing logic (`strategies.py:44-47`).
- Only `git add` the files named in each task. NEVER `git add memory/`.
- Scope: SHORT regime-awareness only. No Kelly, no new short *setup types* (breakdown/distribution), no $27K reset — all explicitly out of scope.

---

### Task 1: Regime-aware classifier + "Bear Short" strategy

**Files:**
- Modify: `strategies.py` (dataclass field ~line 35; `find_matching_strategy` ~207-216; `classify_trade_intent` ~219-224; append strategy to `STRATEGY_LIBRARY` before the closing `]` at ~186)
- Test: `tests/test_bear_short.py`

**Interfaces:**
- Produces: `_is_confirmed_downtrend(market_summary: Optional[Dict]) -> bool`; `find_matching_strategy(timestamp_utc, setup_type, direction, market_summary=None)`; `classify_trade_intent(timestamp_utc, setup_type, direction, market_summary=None) -> Dict` (unchanged return shape: keys `strategy_name`, `kind`, `size_mult`, `context_for_committee`, ...). New `Strategy` field `required_downtrend: bool = False`. New strategy `name="Bear Short"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_bear_short.py`:

```python
import os, sys
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from strategies import classify_trade_intent

def _ms(e50, e200):  # market_summary with the EMA-cross signal
    return {"trend": "?", "indicators": {"ema_50": e50, "ema_200": e200}}
DOWN = _ms(100.0, 110.0)  # ema50 < ema200 → confirmed downtrend
UP = _ms(110.0, 100.0)    # ema50 > ema200 → not a downtrend
def _ts(hour):
    return datetime(2026, 1, 1, hour, 5)  # minute 5 = not a candle-open minute

def test_asia_pullback_short_blessed_in_downtrend():
    r = classify_trade_intent(_ts(3), "Pullback", "SHORT", DOWN)
    assert r["kind"] == "blessed", r
    assert r["strategy_name"] == "Bear Short", r

def test_night_bounce_short_blessed_in_downtrend():
    r = classify_trade_intent(_ts(22), "Bounce", "SHORT", DOWN)
    assert r["kind"] == "blessed" and r["strategy_name"] == "Bear Short", r

def test_short_not_blessed_when_not_downtrend():
    r = classify_trade_intent(_ts(3), "Pullback", "SHORT", UP)
    assert r["kind"] == "experimental", r  # Bear Short skipped, no other Asia-short match

def test_short_not_blessed_when_market_summary_missing():
    r = classify_trade_intent(_ts(3), "Pullback", "SHORT")  # no market_summary
    assert r["kind"] == "experimental", r

def test_ny_pullback_short_keeps_existing_strategy():
    # NY Counter-Trend Short (13-21 Pullback SHORT) has precedence over Bear Short
    r = classify_trade_intent(_ts(15), "Pullback", "SHORT", DOWN)
    assert r["kind"] == "blessed" and r["strategy_name"] == "NY Counter-Trend Short", r

def test_london_pullback_short_still_anti():
    # ANTI precedence preserved even in a downtrend
    r = classify_trade_intent(_ts(9), "Pullback", "SHORT", DOWN)
    assert r["kind"] == "anti", r

if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print(f"PASS {fn.__name__}")
    print(f"\nALL {len(fns)} TESTS PASSED")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd $ROOT && python3 tests/test_bear_short.py`
Expected: FAIL — `test_asia_pullback_short_blessed_in_downtrend` raises AssertionError (currently an Asia Pullback SHORT matches no strategy → `kind=="experimental"`, `strategy_name` is None), and/or `classify_trade_intent` does not yet accept a 4th argument (TypeError).

- [ ] **Step 3a: Add the `required_downtrend` field to the Strategy dataclass**

In `strategies.py`, immediately after the `kind: str = "blessed"` line (~line 35), add:

```python
    # אם True — האסטרטגיה "מבורכת" רק בשוק דובי מאושש (EMA50<EMA200). אחרת לא נבחרת.
    required_downtrend: bool = False
```

- [ ] **Step 3b: Add the downtrend helper and make `find_matching_strategy` regime-aware**

In `strategies.py`, replace the whole `find_matching_strategy` function (~lines 207-216) with:

```python
def _is_confirmed_downtrend(market_summary: Optional[Dict]) -> bool:
    """ירידה מאוששת = EMA50 < EMA200 ב-market_summary. אות זהה ל-regime gate; backtest-safe."""
    if not market_summary:
        return False
    ind = market_summary.get("indicators") or {}
    e50, e200 = ind.get("ema_50"), ind.get("ema_200")
    return e50 is not None and e200 is not None and e50 < e200


def find_matching_strategy(timestamp_utc: datetime, setup_type: str, direction: str,
                           market_summary: Optional[Dict] = None) -> Optional[Strategy]:
    """
    מחזיר את האסטרטגיה הראשונה שמתאימה ל-(שעה, setup, direction).
    אסטרטגיה עם required_downtrend נבחרת רק אם market_summary מראה ירידה מאוששת.
    מחזיר None אם לא תואם לאף אחת — סטאפ "ניסיוני".
    """
    for s in STRATEGY_LIBRARY:
        if s.is_active_at(timestamp_utc) and s.matches_setup(setup_type, direction):
            if s.required_downtrend and not _is_confirmed_downtrend(market_summary):
                continue  # אסטרטגיה מותנית-רג'יים, אבל לא בדאון-טרנד → דלג
            return s
    return None
```

- [ ] **Step 3c: Thread `market_summary` through `classify_trade_intent`**

In `strategies.py`, change the `classify_trade_intent` signature (~line 219) and its call to `find_matching_strategy` (~line 224):

```python
def classify_trade_intent(timestamp_utc: datetime, setup_type: str, direction: str,
                          market_summary: Optional[Dict] = None) -> Dict:
```
and
```python
    s = find_matching_strategy(timestamp_utc, setup_type, direction, market_summary)
```
Leave the rest of `classify_trade_intent` unchanged.

- [ ] **Step 3d: Append the "Bear Short" strategy**

In `strategies.py`, inside `STRATEGY_LIBRARY`, immediately AFTER the `"ANTI: London Pullback SHORT"` entry and BEFORE the closing `]` (~line 185-186), add:

```python
    # ─── SHORT מונחה-רג'יים: משלים את ה-regime gate ───
    Strategy(
        name="Bear Short",
        description=(
            "Pullback/Bounce SHORT בשוק דובי מאושש (EMA50<EMA200), בחלון NY+Night+Asia "
            "(מחריג London 7-13 שם short שלילי). משלים את ה-regime gate: כשלונגים חסומים "
            "בירידה — יש מה לסחור. edge דק ומותנה-רג'יים (~+0.1-0.3%/עסקה)."
        ),
        start_hour_utc=13,
        end_hour_utc=7,  # עובר חצות → פעיל 13-23 + 0-6 (NY+Night+Asia)
        allowed_setups=["Pullback", "Bounce"],
        allowed_directions=["SHORT"],
        position_size_mult=1.0,  # edge דק — בלי boost
        kind="blessed",
        required_downtrend=True,
        historical_wr=58.5,
        historical_trades=65,
        historical_avg_pnl=0.119,
    ),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd $ROOT && python3 tests/test_bear_short.py`
Expected: `ALL 6 TESTS PASSED`

- [ ] **Step 5: Commit**

```bash
cd $ROOT && git add strategies.py tests/test_bear_short.py
git commit -m "feat: regime-aware classifier + Bear Short strategy (SHORT blessed only in confirmed downtrend)"
```

---

### Task 2: Pass market_summary into the classifier from run_committee

**Files:**
- Modify: `agents/orchestrator.py` (the `classify_trade_intent(...)` call at ~line 115-119)
- Test: `tests/test_classify_wiring.py`

**Interfaces:**
- Consumes: `classify_trade_intent(timestamp_utc, setup_type, direction, market_summary=None)` from Task 1.
- Produces: `run_committee` passes its `market_summary` as the 4th argument to `classify_trade_intent`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_classify_wiring.py` (offline: a LONG in a downtrend lets `classify` run, then the regime gate early-returns before any LLM call — so we can spy on what `classify_trade_intent` received):

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd $ROOT && python3 tests/test_classify_wiring.py`
Expected: FAIL — `AssertionError` (`captured["ms"]` is `None`, because `run_committee` does not yet pass `market_summary` to `classify_trade_intent`).

- [ ] **Step 3: Implement the wiring**

In `agents/orchestrator.py`, the call at ~line 115-119 currently reads:

```python
            strategy_context = classify_trade_intent(
                _dt.utcnow(),
                setup.get("סוג", "?"),
                setup.get("כיוון", "?"),
            )
```
Change it to pass `market_summary`:

```python
            strategy_context = classify_trade_intent(
                _dt.utcnow(),
                setup.get("סוג", "?"),
                setup.get("כיוון", "?"),
                market_summary,
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd $ROOT && python3 tests/test_classify_wiring.py`
Expected: `ALL TESTS PASSED`

- [ ] **Step 5: Commit**

```bash
cd $ROOT && git add agents/orchestrator.py tests/test_classify_wiring.py
git commit -m "feat: pass market_summary into classify_trade_intent (enables regime-aware Bear Short)"
```

---

### Task 3: Validation — re-classify real SHORT trades

**Files:**
- Create: `scripts/validate_bear_short.py`

**Interfaces:**
- Consumes: `classify_trade_intent` (Task 1), `data.binance_client.BinanceClient`, `data.indicators.market_summary`.
- Produces: a printed report grouping the real closed SHORT trades by the NEW classification (`Bear Short` / `NY Counter-Trend Short` / `anti` / `experimental`) with each group's WR/PnL. Deliverable is the report; "test" = run it and read the numbers.

- [ ] **Step 1: Write the validation script**

Create `scripts/validate_bear_short.py`:

```python
"""Validate Bear Short by re-classifying the real closed SHORT trades with the new
regime-aware logic. GOAL: trades newly blessed as "Bear Short" have positive EV, and
shorts in non-downtrend regimes are NOT blessed (stay experimental/anti)."""
import json, os, sys, time
from datetime import datetime, timezone
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from strategies import classify_trade_intent
from data.binance_client import BinanceClient
from data.indicators import market_summary

TRADES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory", "trades.json")

def ms_epoch(s):
    return int(datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp() * 1000)

def main():
    trades = [t for t in json.load(open(TRADES))["trades"]
              if t.get("status") == "closed" and t.get("simulation", {}).get("pnl_pct") is not None
              and t["hunter_setup"].get("כיוון") == "SHORT"]
    client = BinanceClient()
    groups = defaultdict(list)
    for t in trades:
        setup = t["hunter_setup"].get("סוג")
        entry = float(t["decision"].get("כניסה") or 0)
        if not setup or entry <= 0:
            continue
        sym = "BTCUSDT" if entry > 10000 else "ETHUSDT"
        end = ms_epoch(t["timestamp_analyzed"])  # endTime inclusive on open_time → ends AT decision candle
        df = client.get_klines(sym, "15m", limit=250, end_time=end)
        time.sleep(0.08)
        if len(df) < 50:
            continue
        summary = market_summary(df)
        ts = datetime.strptime(t["timestamp_analyzed"], "%Y-%m-%d %H:%M:%S")
        intent = classify_trade_intent(ts, setup, "SHORT", summary)
        label = intent.get("strategy_name") or intent.get("kind")  # e.g. "Bear Short" / "NY Counter-Trend Short" / "experimental"
        groups[label].append(t["simulation"]["pnl_pct"])

    def stat(lst):
        if not lst:
            return "n=0"
        w = sum(1 for p in lst if p > 0)
        return f"n={len(lst):>3}  WR={100*w/len(lst):>5.1f}%  totPnl={sum(lst):>+7.2f}%  EV={sum(lst)/len(lst):>+6.3f}%"

    print("=== Bear Short validation — real SHORT trades re-classified (regime-aware) ===")
    print("GOAL: 'Bear Short' group EV>0; non-downtrend shorts land in 'experimental' (not blessed).")
    for label in sorted(groups, key=lambda k: -sum(groups[k])):
        print(f"  {str(label):26} {stat(groups[label])}")

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the validation and read the result**

Run: `cd $ROOT && python3 scripts/validate_bear_short.py`
Expected shape: per-classification report. The strategy is validated if the `Bear Short` group shows positive EV (it captures Asia/Night pullback + bounce shorts in downtrends), the `NY Counter-Trend Short` group stays strongly positive, and `experimental` collects the non-downtrend shorts (the weaker/negative ones — confirming we did NOT bless them). Record the actual numbers — they are the spec §5 regression evidence.

- [ ] **Step 3: Commit**

```bash
cd $ROOT && git add scripts/validate_bear_short.py
git commit -m "feat: add Bear Short validation script (re-classify real shorts, regime-aware)"
```

---

## Follow-on validation (operational, not code)

Per spec §5, before fully trusting Bear Short: run a multi-regime backtest over ≥2-3 distinct historical bear periods from Binance and require positive net EV in EACH — this guards against overfitting to the one bear window in the current data. Do NOT reset to `$27K` until the gate + Bear Short show a regime-robust positive edge across periods.

## Self-Review

**Spec coverage:**
- §3.1 "Bear Short" strategy (window, setups, SHORT, size 1.0, required_downtrend) → Task 1 Step 3d. ✓
- §3.2 regime-aware `find_matching_strategy`/`classify_trade_intent` + `required_downtrend` field + EMA-cross signal → Task 1 Steps 3a-3c. ✓
- §3.3 wire `market_summary` from `run_committee` → Task 2. ✓
- §3.4 interaction with gate (range stays experimental) → covered by the `_is_confirmed_downtrend` + precedence logic; asserted indirectly (`test_short_not_blessed_when_not_downtrend`). ✓
- §4/§5 unit tests + real-trade regression → Task 1 tests + Task 3 script. ✓
- §5 multi-period backtest → "Follow-on validation" (operational). ✓
- §2 non-goals (Kelly, new setup types, $27K) → none implemented. ✓

**Placeholder scan:** No TBD/TODO; every code step has complete code + exact commands. ✓

**Type consistency:** `classify_trade_intent(ts, setup, direction, market_summary=None)` and `find_matching_strategy(..., market_summary=None)` defined in Task 1, called identically in Task 2 (orchestrator), and Task 3 (validation). `_is_confirmed_downtrend` reads `market_summary["indicators"]["ema_50"/"ema_200"]`, matching the gate's contract. The `required_downtrend` field is added in 3a and used in 3b/3d. ✓
