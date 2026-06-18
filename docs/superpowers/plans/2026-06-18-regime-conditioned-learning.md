# Regime-Conditioned Learning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the agent's memory regime-aware — tag lessons + strategy stats by market regime (bull/bear/range) and prioritize regime-matched lessons in decisions (today `relevant_lessons` ignores the market entirely).

**Architecture:** A deterministic `classify_regime(market_summary)` in `strategies.py`; `save_lesson` tags each lesson's `regime`; `relevant_lessons` prioritizes lessons matching the current regime (filling with recent others); `record_trade_outcome` gains per-regime sub-stats; callers pass the regime where market context is available.

**Tech Stack:** Python 3.9. No pytest — tests are runnable assert-scripts (`python3 tests/test_*.py`).

## Global Constraints

- Project root `$ROOT = /Users/guyamgar/Desktop/Agents_markering/trading_agent`. Work on a new branch `regime-learning` off `main`.
- **Deterministic, backtest-safe:** regime is derived only from the passed `market_summary` (`indicators.ema_9/21/50/200`, `trend`). No network/LLM in the regime path.
- **Regime taxonomy (verbatim):** `bull` = EMA9>EMA21>EMA50 AND EMA50>EMA200; `bear` = EMA9<EMA21<EMA50 AND EMA50<EMA200; else `range`. Missing indicators → `range` (safe default). This is consistent with the gate/Reality-Check (bear ⊇ EMA50<EMA200).
- **Backward-compatible / additive only:** new lesson field `regime` (default `"unknown"` when not provided); new optional `record_trade_outcome(..., regime=None)`; the 283 existing lessons have no `regime` → treated as `"unknown"` (fill, never matched).
- **`relevant_lessons` = prioritize:** regime-matched lessons first (most recent), fill remaining slots with recent others; when no matches, behaves exactly as today (recent N). Keep the existing `min_confidence` filter.
- No pytest. Only `git add` files named per task; NEVER `git add memory/`.
- Scope: regime-tagging of lessons + per-regime strategy stats. Explorer (sub-project B) and a historical backfill of old lessons (needs klines) are OUT.

---

### Task 1: `classify_regime` (deterministic)

**Files:**
- Modify: `strategies.py` (add `classify_regime` near `_is_confirmed_downtrend`)
- Test: `tests/test_classify_regime.py`

**Interfaces:**
- Produces: `classify_regime(market_summary: Optional[Dict]) -> str` returning `"bull"|"bear"|"range"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_classify_regime.py`:

```python
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from strategies import classify_regime

def _ms(e9, e21, e50, e200, trend="?"):
    return {"trend": trend, "indicators": {"ema_9": e9, "ema_21": e21, "ema_50": e50, "ema_200": e200}}

def test_bull():
    assert classify_regime(_ms(110, 108, 105, 100)) == "bull"   # 9>21>50 and 50>200

def test_bear():
    assert classify_regime(_ms(90, 92, 95, 100)) == "bear"      # 9<21<50 and 50<200

def test_range_mixed():
    assert classify_regime(_ms(100, 99, 101, 100)) == "range"   # not strictly ordered

def test_range_when_ema_aligned_but_no_long_cross():
    assert classify_regime(_ms(110, 108, 105, 106)) == "range"  # 9>21>50 but 50<200 → not bull

def test_missing_indicators_defaults_range():
    assert classify_regime({}) == "range"
    assert classify_regime(None) == "range"

if __name__ == "__main__":
    fns = [v for k,v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns: fn(); print(f"PASS {fn.__name__}")
    print(f"\nALL {len(fns)} TESTS PASSED")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd $ROOT && python3 tests/test_classify_regime.py`
Expected: FAIL — `ImportError: cannot import name 'classify_regime'`.

- [ ] **Step 3: Implement**

In `strategies.py`, add (next to `_is_confirmed_downtrend`):

```python
def classify_regime(market_summary: Optional[Dict]) -> str:
    """Deterministic regime label from EMA structure. bull/bear/range.
    Consistent with the regime gate (bear requires EMA50<EMA200)."""
    ind = (market_summary or {}).get("indicators") or {}
    e9, e21, e50, e200 = ind.get("ema_9"), ind.get("ema_21"), ind.get("ema_50"), ind.get("ema_200")
    if None in (e9, e21, e50, e200):
        return "range"
    if e9 > e21 > e50 and e50 > e200:
        return "bull"
    if e9 < e21 < e50 and e50 < e200:
        return "bear"
    return "range"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd $ROOT && python3 tests/test_classify_regime.py`
Expected: `ALL 5 TESTS PASSED`

- [ ] **Step 5: Commit**

```bash
cd $ROOT && git add strategies.py tests/test_classify_regime.py
git commit -m "feat: classify_regime deterministic bull/bear/range from EMA structure"
```

---

### Task 2: Regime-aware lessons — tag + prioritized selection

**Files:**
- Modify: `memory_store.py` (`save_lesson` ~131-152; `relevant_lessons` ~206-214)
- Test: `tests/test_regime_lessons.py`

**Interfaces:**
- Consumes: `strategies.classify_regime` (Task 1).
- Produces: `save_lesson` sets `lesson["regime"]` (from a `regime` field, else from a `market_summary` field it pops, else `"unknown"`); `relevant_lessons(market_summary, limit, min_confidence)` returns regime-matched lessons first, filled with recent others.

- [ ] **Step 1: Write the failing test**

Create `tests/test_regime_lessons.py`:

```python
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import memory_store as M

BULL = {"trend":"?","indicators":{"ema_9":110,"ema_21":108,"ema_50":105,"ema_200":100}}
BEAR = {"trend":"?","indicators":{"ema_9":90,"ema_21":92,"ema_50":95,"ema_200":100}}

def _reset():
    M.LESSONS_FILE.write_text(json.dumps({"lessons": []}, ensure_ascii=False))

def test_save_lesson_tags_regime_from_market_summary():
    _reset()
    lid = M.save_lesson({"rule":"r","trigger":"t","market_summary": BEAR})
    saved = [l for l in M.load_lessons() if l["id"]==lid][0]
    assert saved["regime"] == "bear", saved
    assert "market_summary" not in saved  # popped, not persisted

def test_save_lesson_defaults_unknown():
    _reset()
    lid = M.save_lesson({"rule":"r","trigger":"t"})
    saved = [l for l in M.load_lessons() if l["id"]==lid][0]
    assert saved["regime"] == "unknown", saved

def test_relevant_lessons_prioritizes_current_regime():
    _reset()
    M.save_lesson({"rule":"bull-lesson","trigger":"t","regime":"bull"})
    M.save_lesson({"rule":"bear-lesson","trigger":"t","regime":"bear"})
    M.save_lesson({"rule":"old-untagged","trigger":"t"})  # regime=unknown
    picked = M.relevant_lessons(BEAR, limit=1)
    assert len(picked) == 1 and picked[0]["rule"] == "bear-lesson", picked

def test_relevant_lessons_fills_when_no_match():
    _reset()
    M.save_lesson({"rule":"a","trigger":"t","regime":"bull"})
    M.save_lesson({"rule":"b","trigger":"t","regime":"bull"})
    picked = M.relevant_lessons(BEAR, limit=2)  # no bear lessons → fall back to recent
    assert len(picked) == 2, picked

if __name__ == "__main__":
    fns = [v for k,v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns: fn(); print(f"PASS {fn.__name__}")
    print(f"\nALL {len(fns)} TESTS PASSED")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd $ROOT && python3 tests/test_regime_lessons.py`
Expected: FAIL — `test_save_lesson_tags_regime_from_market_summary` (no `regime` field set / `market_summary` persisted).

- [ ] **Step 3: Implement `save_lesson` tagging**

In `memory_store.py` `save_lesson`, after the existing `lesson["times_wrong"] = ...` defaults and BEFORE `lessons.append(lesson)`, add:

```python
    # ─── regime tag (regime-conditioned learning) ───
    ms = lesson.pop("market_summary", None)
    if "regime" not in lesson:
        if ms is not None:
            try:
                from strategies import classify_regime
                lesson["regime"] = classify_regime(ms)
            except Exception:
                lesson["regime"] = "unknown"
        else:
            lesson["regime"] = "unknown"
```

- [ ] **Step 4: Implement `relevant_lessons` prioritization**

In `memory_store.py`, replace the body of `relevant_lessons` (the `return lessons[-limit:]` version) with:

```python
def relevant_lessons(market_summary: Dict, limit: int = 10, min_confidence: int = 0) -> List[Dict]:
    """לקחים מתועדפים לפי רג'יים: תואמי-רג'יים קודם, מילוי באחרונים."""
    lessons = load_lessons()
    if min_confidence > 0:
        lessons = [l for l in lessons if l.get("confidence", 1) >= min_confidence]
    try:
        from strategies import classify_regime
        cur = classify_regime(market_summary)
    except Exception:
        cur = None
    if not cur:
        return lessons[-limit:]
    matched = [l for l in lessons if l.get("regime") == cur][-limit:]
    if len(matched) >= limit:
        return matched
    others = [l for l in lessons if l.get("regime") != cur][-(limit - len(matched)):]
    return others + matched
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd $ROOT && python3 tests/test_regime_lessons.py`
Expected: `ALL 4 TESTS PASSED`

- [ ] **Step 6: Commit**

```bash
cd $ROOT && git add memory_store.py tests/test_regime_lessons.py
git commit -m "feat: tag lessons with regime + regime-prioritized relevant_lessons"
```

---

### Task 3: Per-regime strategy stats

**Files:**
- Modify: `strategies.py` (`record_trade_outcome` ~304-326)
- Test: `tests/test_record_outcome_regime.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `record_trade_outcome(strategy_name, pnl_pct, regime: Optional[str] = None)` — keeps the existing bucket AND, when `regime` is given, updates a nested `by_regime[regime]` sub-bucket (`trades/wins/losses/total_pnl`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_record_outcome_regime.py`:

```python
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import strategies as S

def test_record_trade_outcome_per_regime():
    S.STRATEGIES_STATS_FILE.write_text("{}")
    S.record_trade_outcome("NY Pullback Trader", 0.5, regime="bull")
    S.record_trade_outcome("NY Pullback Trader", -0.3, regime="bear")
    stats = json.loads(S.STRATEGIES_STATS_FILE.read_text())
    s = stats["NY Pullback Trader"]
    assert s["trades"] == 2  # main bucket unchanged behavior
    assert s["by_regime"]["bull"]["trades"] == 1 and s["by_regime"]["bull"]["wins"] == 1
    assert s["by_regime"]["bear"]["trades"] == 1 and s["by_regime"]["bear"]["losses"] == 1

def test_record_trade_outcome_no_regime_still_works():
    S.STRATEGIES_STATS_FILE.write_text("{}")
    S.record_trade_outcome("Night Owl", 0.4)  # no regime arg
    stats = json.loads(S.STRATEGIES_STATS_FILE.read_text())
    assert stats["Night Owl"]["trades"] == 1
    assert "by_regime" not in stats["Night Owl"] or stats["Night Owl"]["by_regime"] == {}

if __name__ == "__main__":
    test_record_trade_outcome_per_regime(); print("PASS per_regime")
    test_record_trade_outcome_no_regime_still_works(); print("PASS no_regime")
    print("\nALL TESTS PASSED")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd $ROOT && python3 tests/test_record_outcome_regime.py`
Expected: FAIL — `record_trade_outcome` does not accept `regime` (TypeError) / no `by_regime`.

- [ ] **Step 3: Implement**

In `strategies.py`, change `record_trade_outcome`'s signature and add the per-regime update. Replace the signature line `def record_trade_outcome(strategy_name: Optional[str], pnl_pct: float):` with:

```python
def record_trade_outcome(strategy_name: Optional[str], pnl_pct: float, regime: Optional[str] = None):
```
Then, immediately before the final `stats[key] = s` / `_save_stats(stats)` lines, insert:

```python
    if regime:
        br = s.setdefault("by_regime", {})
        r = br.setdefault(regime, {"trades": 0, "wins": 0, "losses": 0, "total_pnl": 0.0})
        r["trades"] += 1
        if pnl_pct > 0:
            r["wins"] += 1
        else:
            r["losses"] += 1
        r["total_pnl"] += pnl_pct
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd $ROOT && python3 tests/test_record_outcome_regime.py`
Expected: `ALL TESTS PASSED`

- [ ] **Step 5: Commit**

```bash
cd $ROOT && git add strategies.py tests/test_record_outcome_regime.py
git commit -m "feat: record_trade_outcome stores per-regime sub-stats"
```

---

### Task 4: Wire the live close path to pass regime

**Files:**
- Modify: `live_monitor.py` (the `save_lesson({...})` call ~line 153 and `record_trade_outcome(strat_name, sim["pnl_pct"])` ~line 167)

**Interfaces:**
- Consumes: `strategies.classify_regime` (Task 1); the regime-aware `save_lesson` (Task 2) and `record_trade_outcome` (Task 3).
- Produces: live-closed trades now tag their lesson + stats with the regime.

- [ ] **Step 1: Determine the available market context**

Read `live_monitor.py` `close_recommendation` (and what `rec` carries). Identify a `market_summary`-shaped dict available at close (e.g. the rec's stored market data, or fetch the latest 15m candle's indicators that live_monitor already pulls). If a full `market_summary` with `indicators.ema_*` is available, use it; if only partial, build the minimal `{"indicators": {...}}` needed by `classify_regime`; if genuinely none, regime resolves to `"range"`/`"unknown"` gracefully (no crash).

- [ ] **Step 2: Wire it**

Compute once near the top of the close path: `from strategies import classify_regime` and `_regime = classify_regime(<the market context found in Step 1>)`. Then:
- In the `save_lesson({...})` call (~line 153), add `"regime": _regime,` to the lesson dict.
- Change the `record_trade_outcome(strat_name, sim["pnl_pct"])` call (~line 167) to `record_trade_outcome(strat_name, sim["pnl_pct"], regime=_regime)`.

(Show the exact edited lines in your implementation.)

- [ ] **Step 3: Smoke-test**

Run: `cd $ROOT && python3 -c "import ast; ast.parse(open('live_monitor.py').read()); print('live_monitor.py parses OK')"`
Expected: `live_monitor.py parses OK`
Then run the full regime test suite to confirm no regression:
`cd $ROOT && python3 tests/test_classify_regime.py && python3 tests/test_regime_lessons.py && python3 tests/test_record_outcome_regime.py`
Expected: all three print their PASSED line.

- [ ] **Step 4: Commit**

```bash
cd $ROOT && git add live_monitor.py
git commit -m "feat: live close path tags lessons + stats with regime"
```

---

## Self-Review

**Spec coverage:**
- §3.1 `classify_regime` (bull/bear/range, EMA rule, range default) → Task 1. ✓
- §3.2 tag lessons at creation → Task 2 (`save_lesson`) + Task 4 (live caller passes context). ✓
- §3.3 `relevant_lessons` prioritize (matched first, fill recent, min_confidence kept, fallback when no match) → Task 2. ✓
- §3.4 per-regime strategy stats → Task 3 (`record_trade_outcome`) + Task 4 (live caller passes regime). ✓
- §4 tests → each task's assert-script. ✓
- §2 non-goals (no prompt changes, no Explorer, no historical backfill) → respected. ✓

**Placeholder scan:** Tasks 1-3 are full code. Task 4 is caller-wiring where the exact available `market_summary` source must be read from `live_monitor.py` (an in-file integration, with a defined `"range"`/`"unknown"` fallback) — explicit instruction, not a vague TODO.

**Type consistency:** `classify_regime(market_summary) -> str` (Task 1) consumed by `save_lesson`/`relevant_lessons` (Task 2) and `live_monitor` (Task 4). `record_trade_outcome(strategy_name, pnl_pct, regime=None)` (Task 3) called with `regime=` in Task 4. Lesson `regime` field written in Task 2, read in Task 2's `relevant_lessons`. `by_regime` sub-stat shape consistent in Task 3.
