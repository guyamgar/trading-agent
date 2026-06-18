# Reality-Check Auditor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic auditor that detects per-strategy paper↔live generalization gaps and graduatedly de-weights what doesn't generalize (no trading halt).

**Architecture:** A new pure-Python `reality_check.py` re-attributes each closed trade to a strategy (offline, by time+setup+direction), splits by the `live_mode` flag, computes paper_ev vs live_ev, flags overfit gaps, and decays a per-strategy `live_size_mult` in `memory/reality_check_state.json`. `classify_trade_intent` multiplies its `size_mult` by that live mult. A daemon loop in `bot.py` runs it periodically and alerts via Telegram.

**Tech Stack:** Python 3.9. No pytest — tests are runnable assert-scripts (`python3 tests/test_*.py`).

## Global Constraints

- Project root `$ROOT = /Users/guyamgar/Desktop/Agents_markering/trading_agent`. Work on a new branch `reality-check` off `main`.
- **Deterministic only** — `reality_check.py` makes NO LLM calls and NO network calls (it reads local `memory/*.json`). An auditor must not hallucinate.
- **No trading halt.** The response is graduated de-weighting only: per-strategy `live_size_mult` ∈ [0.1, 1.0], decay ×0.7 on a dangerous gap, recover ×1.15 (cap 1.0) when the gap closes; plus lesson-confidence decay. Never set size to 0 / never block.
- **Dangerous gap** = `n_live ≥ 8` AND `live_ev < 0` AND `paper_ev > 0` (overfit signature). Constants: `MIN_LIVE_SAMPLE=8`, `DECAY_FACTOR=0.7`, `MULT_FLOOR=0.1`, `RECOVERY_FACTOR=1.15`, `MULT_CAP=1.0`.
- Backtest-safe: `classify_trade_intent` reads `live_size_mult` from the local state file (default 1.0 if absent) — no network.
- Data contract: closed trade = `status=="closed"` with `simulation.pnl_pct` not None; `live_mode is True` → live, else paper; `hunter_setup["סוג"]`=setup, `hunter_setup["כיוון"]`=direction; `timestamp_analyzed` = "YYYY-MM-DD HH:MM:SS" (UTC, 15m-aligned).
- Only `git add` files named per task. NEVER `git add memory/`.
- Scope: Reality-Check only. Regime-tagged learning (sub-project C) and Explorer (sub-project B) are OUT.

---

### Task 1: `reality_check.py` core — attribution, gap detection, graduated decay, state

**Files:**
- Create: `reality_check.py`
- Test: `tests/test_reality_check.py`

**Interfaces:**
- Produces:
  - `attribute_strategy(timestamp_utc: datetime, setup_type: str, direction: str) -> str` — strategy NAME by time+setup+direction (ignores `required_downtrend`; returns `"_experimental_"` if none).
  - `compute_strategy_gaps(trades: list) -> dict` — `{strategy_name: {paper_ev, live_ev, n_paper, n_live}}`.
  - `detect_dangerous(gaps: dict, min_live_sample: int = 8) -> list[str]` — names with a dangerous gap.
  - `update_live_mults(dangerous: list[str], all_names: list[str], state: dict) -> dict` — graduated decay/recovery; returns new state.
  - `get_live_size_mult(strategy_name: str) -> float` — reads `memory/reality_check_state.json` (default 1.0).
- Consumes: `strategies.STRATEGY_LIBRARY`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_reality_check.py`:

```python
import os, sys, json
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import reality_check as RC

def _trade(setup, direction, hour, pnl, live):
    t = {"status": "closed", "hunter_setup": {"סוג": setup, "כיוון": direction},
         "simulation": {"pnl_pct": pnl}, "timestamp_analyzed": f"2026-05-20 {hour:02d}:00:00"}
    if live: t["live_mode"] = True
    return t

def test_attribute_known_strategy():
    # NY Pullback LONG (hour 15) → "NY Pullback Trader"
    assert RC.attribute_strategy(datetime(2026,1,1,15,5), "Pullback", "LONG") == "NY Pullback Trader"

def test_attribute_experimental():
    assert RC.attribute_strategy(datetime(2026,1,1,15,5), "Engulfing", "SHORT") == "_experimental_"

def test_dangerous_gap_flagged():
    # one strategy: paper positive, live negative, n_live>=8 → dangerous
    trades = [_trade("Pullback","LONG",15, 0.5, False) for _ in range(10)]
    trades += [_trade("Pullback","LONG",15, -0.3, True) for _ in range(8)]
    gaps = RC.compute_strategy_gaps(trades)
    assert "NY Pullback Trader" in gaps
    assert RC.detect_dangerous(gaps) == ["NY Pullback Trader"]

def test_small_live_sample_not_flagged():
    trades = [_trade("Pullback","LONG",15, 0.5, False) for _ in range(10)]
    trades += [_trade("Pullback","LONG",15, -0.3, True) for _ in range(3)]  # n_live<8
    assert RC.detect_dangerous(RC.compute_strategy_gaps(trades)) == []

def test_graduated_decay_and_recovery():
    state = {}
    s1 = RC.update_live_mults(["NY Pullback Trader"], ["NY Pullback Trader"], state)
    assert abs(s1["live_size_mult"]["NY Pullback Trader"] - 0.7) < 1e-9
    s2 = RC.update_live_mults(["NY Pullback Trader"], ["NY Pullback Trader"], s1)
    assert abs(s2["live_size_mult"]["NY Pullback Trader"] - 0.49) < 1e-9
    # not dangerous this round → recovery ×1.15
    s3 = RC.update_live_mults([], ["NY Pullback Trader"], s2)
    assert abs(s3["live_size_mult"]["NY Pullback Trader"] - 0.49*1.15) < 1e-9

def test_decay_floor_and_cap():
    state = {"live_size_mult": {"X": 0.12}}
    s = RC.update_live_mults(["X"], ["X"], state)
    assert s["live_size_mult"]["X"] >= 0.1  # floor
    state2 = {"live_size_mult": {"X": 0.95}}
    s2 = RC.update_live_mults([], ["X"], state2)
    assert s2["live_size_mult"]["X"] <= 1.0  # cap

if __name__ == "__main__":
    fns = [v for k,v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns: fn(); print(f"PASS {fn.__name__}")
    print(f"\nALL {len(fns)} TESTS PASSED")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd $ROOT && python3 tests/test_reality_check.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'reality_check'`.

- [ ] **Step 3: Write the implementation**

Create `reality_check.py`:

```python
"""Reality-Check Auditor — deterministic. Detects per-strategy paper↔live
generalization gaps and graduatedly de-weights what doesn't generalize.
No LLM, no network. Reads local memory/*.json only."""
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

from strategies import STRATEGY_LIBRARY

ROOT = Path(__file__).parent
STATE_FILE = ROOT / "memory" / "reality_check_state.json"

MIN_LIVE_SAMPLE = 8
DECAY_FACTOR = 0.7
MULT_FLOOR = 0.1
RECOVERY_FACTOR = 1.15
MULT_CAP = 1.0


def attribute_strategy(timestamp_utc: datetime, setup_type: str, direction: str) -> str:
    """Strategy NAME by time+setup+direction (ignores required_downtrend — this is
    an audit grouping, not a live gate). '_experimental_' if no match."""
    for s in STRATEGY_LIBRARY:
        if s.is_active_at(timestamp_utc) and s.matches_setup(setup_type, direction):
            return s.name
    return "_experimental_"


def compute_strategy_gaps(trades: List[Dict]) -> Dict[str, Dict]:
    """Group closed trades by attributed strategy, split by live_mode, compute EVs."""
    buckets: Dict[str, Dict[str, list]] = {}
    for t in trades:
        if t.get("status") != "closed":
            continue
        pnl = (t.get("simulation") or {}).get("pnl_pct")
        hs = t.get("hunter_setup") or {}
        setup, direction = hs.get("סוג"), hs.get("כיוון")
        if pnl is None or not setup or not direction:
            continue
        try:
            dt = datetime.strptime(t["timestamp_analyzed"], "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        name = attribute_strategy(dt, setup, direction)
        b = buckets.setdefault(name, {"paper": [], "live": []})
        b["live" if t.get("live_mode") is True else "paper"].append(pnl)
    out = {}
    for name, b in buckets.items():
        p, l = b["paper"], b["live"]
        out[name] = {
            "n_paper": len(p), "n_live": len(l),
            "paper_ev": (sum(p) / len(p)) if p else 0.0,
            "live_ev": (sum(l) / len(l)) if l else 0.0,
        }
    return out


def detect_dangerous(gaps: Dict[str, Dict], min_live_sample: int = MIN_LIVE_SAMPLE) -> List[str]:
    """Overfit signature: enough live samples, profitable on paper, losing live."""
    return sorted(
        name for name, g in gaps.items()
        if g["n_live"] >= min_live_sample and g["live_ev"] < 0 and g["paper_ev"] > 0
    )


def update_live_mults(dangerous: List[str], all_names: List[str], state: Dict) -> Dict:
    """Graduated: decay dangerous strategies ×DECAY_FACTOR (floor), recover others ×RECOVERY_FACTOR (cap)."""
    mults = dict(state.get("live_size_mult", {}))
    dang = set(dangerous)
    for name in set(all_names) | dang | set(mults.keys()):
        cur = mults.get(name, 1.0)
        if name in dang:
            cur = max(MULT_FLOOR, round(cur * DECAY_FACTOR, 4))
        elif cur < MULT_CAP:
            cur = min(MULT_CAP, round(cur * RECOVERY_FACTOR, 4))
        mults[name] = cur
    new_state = dict(state)
    new_state["live_size_mult"] = mults
    return new_state


def _load_state() -> Dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def get_live_size_mult(strategy_name: Optional[str]) -> float:
    """Live-confidence multiplier for a strategy (default 1.0). Used by classify_trade_intent."""
    if not strategy_name:
        return 1.0
    return float(_load_state().get("live_size_mult", {}).get(strategy_name, 1.0))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd $ROOT && python3 tests/test_reality_check.py`
Expected: `ALL 6 TESTS PASSED`

- [ ] **Step 5: Commit**

```bash
cd $ROOT && git add reality_check.py tests/test_reality_check.py
git commit -m "feat: reality_check core — paper/live gap detection + graduated live_size_mult"
```

---

### Task 2: Apply `live_size_mult` in `classify_trade_intent`

**Files:**
- Modify: `strategies.py` (`classify_trade_intent` — the blessed branch return ~lines 264-282)
- Test: `tests/test_reality_check_integration.py`

**Interfaces:**
- Consumes: `reality_check.get_live_size_mult(strategy_name) -> float` (Task 1).
- Produces: a blessed strategy's returned `size_mult` is multiplied by its live mult.

- [ ] **Step 1: Write the failing test**

Create `tests/test_reality_check_integration.py`:

```python
import os, sys, json
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from strategies import classify_trade_intent
import reality_check as RC

def test_blessed_size_scaled_by_live_mult(tmp_state=RC.STATE_FILE):
    # write a live mult of 0.5 for NY Pullback Trader
    RC.STATE_FILE.write_text(json.dumps({"live_size_mult": {"NY Pullback Trader": 0.5}}))
    try:
        r = classify_trade_intent(datetime(2026,1,1,15,5), "Pullback", "LONG")
        # base mult for NY Pullback Trader is 1.3; ×0.5 = 0.65 (no candle-open boost at minute 5)
        assert abs(r["size_mult"] - 0.65) < 1e-6, r
    finally:
        RC.STATE_FILE.unlink(missing_ok=True)

def test_default_mult_when_no_state():
    RC.STATE_FILE.unlink(missing_ok=True)
    r = classify_trade_intent(datetime(2026,1,1,15,5), "Pullback", "LONG")
    assert abs(r["size_mult"] - 1.3) < 1e-6, r  # base, unscaled

if __name__ == "__main__":
    test_default_mult_when_no_state(); print("PASS default")
    test_blessed_size_scaled_by_live_mult(); print("PASS scaled")
    print("\nALL TESTS PASSED")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd $ROOT && python3 tests/test_reality_check_integration.py`
Expected: FAIL — `test_blessed_size_scaled_by_live_mult` gets 1.3 (unscaled), not 0.65.

- [ ] **Step 3: Implement**

In `strategies.py`, in the `blessed` return of `classify_trade_intent` (~line 264), change the `base_mult` line so the final size includes the live mult. Replace:

```python
    # blessed
    base_mult = s.position_size_mult
```
with:

```python
    # blessed — scale by the Reality-Check live-confidence multiplier (default 1.0)
    try:
        from reality_check import get_live_size_mult
        _live_mult = get_live_size_mult(s.name)
    except Exception:
        _live_mult = 1.0
    base_mult = s.position_size_mult * _live_mult
```

(The existing `round(base_mult * boost_factor, 3)` calls below now include the live mult.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd $ROOT && python3 tests/test_reality_check_integration.py`
Expected: `ALL TESTS PASSED`

- [ ] **Step 5: Commit**

```bash
cd $ROOT && git add strategies.py tests/test_reality_check_integration.py
git commit -m "feat: scale blessed size_mult by reality_check live_size_mult"
```

---

### Task 3: Orchestration `run_reality_check()` + bot loop + Telegram alert

**Files:**
- Modify: `reality_check.py` (add `run_reality_check()` + `format_alert()`)
- Modify: `bot.py` (add `_reality_check_loop()` mirroring `_lesson_decay_loop` ~line 1421; start it in `main()`)
- Test: `tests/test_run_reality_check.py`

**Interfaces:**
- Consumes: Task 1 functions; `memory_store.load_trades`, `load_lessons`, `adjust_lesson_confidence`.
- Produces: `run_reality_check() -> dict` (computes gaps, updates state, decays bad lessons, persists, returns summary); `format_alert(summary) -> str`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_run_reality_check.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd $ROOT && python3 tests/test_run_reality_check.py`
Expected: FAIL — `AttributeError: module 'reality_check' has no attribute 'run_reality_check'`.

- [ ] **Step 3: Implement `run_reality_check()` + `format_alert()`**

Append to `reality_check.py`:

```python
def _save_state(state: Dict):
    state["updated_at"] = datetime.now().isoformat()
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def run_reality_check() -> Dict:
    """Compute gaps from trades, graduate the live mults, decay bad lessons, persist, return summary."""
    from memory_store import load_trades, load_lessons, adjust_lesson_confidence
    trades = load_trades()
    gaps = compute_strategy_gaps(trades)
    dangerous = detect_dangerous(gaps)
    all_names = list(gaps.keys())
    state = _load_state()
    state = update_live_mults(dangerous, all_names, state)
    state.setdefault("history", []).append({
        "at": datetime.now().isoformat(), "dangerous": dangerous,
        "mults": {n: state["live_size_mult"][n] for n in (dangerous or all_names)},
    })
    state["history"] = state["history"][-50:]
    _save_state(state)
    # lessons: decay confidence of lessons whose live track record is bad
    lessons_decayed = 0
    for l in load_lessons():
        inv = l.get("times_invoked", 0) or 0
        wrong = l.get("times_wrong", 0) or 0
        if inv >= 5 and wrong > (inv / 2):  # mostly wrong when invoked
            adjust_lesson_confidence(l.get("id"), -2)
            lessons_decayed += 1
    return {
        "dangerous": dangerous,
        "gaps": {n: gaps[n] for n in dangerous},
        "live_size_mult": state["live_size_mult"],
        "lessons_decayed": lessons_decayed,
    }


def format_alert(summary: Dict) -> str:
    """Templated Telegram narrative (no LLM)."""
    if not summary["dangerous"] and not summary["lessons_decayed"]:
        return "🔍 Reality-Check: אין פערי-הכללה מסוכנים. הכל מכליל."
    lines = ["🔍 Reality-Check — זוהו פערי paper↔live:"]
    for n in summary["dangerous"]:
        g = summary["gaps"][n]
        lines.append(f"• {n}: paper EV {g['paper_ev']:+.3f}% → live EV {g['live_ev']:+.3f}% "
                     f"(n_live={g['n_live']}) → size×{summary['live_size_mult'][n]:.2f}")
    if summary["lessons_decayed"]:
        lines.append(f"• {summary['lessons_decayed']} לקחים הונמכו (track-record חי גרוע).")
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd $ROOT && python3 tests/test_run_reality_check.py`
Expected: `ALL TESTS PASSED`

- [ ] **Step 5: Add the bot loop**

In `bot.py`, read `_lesson_decay_loop` (~lines 1421-1479) to copy its exact structure: the daemon-loop shape, the `time.sleep` cadence, AND the exact Telegram-send helper it uses for its alert. Then add an analogous `_reality_check_loop()` that, once per cycle (every 6 hours, matching the decay loop's cadence), calls `reality_check.run_reality_check()` and, if `summary["dangerous"] or summary["lessons_decayed"]`, sends `reality_check.format_alert(summary)` via that SAME Telegram-send helper to the authorized chat. Use this body, substituting `<SEND_HELPER>` with the exact send function `_lesson_decay_loop` uses:

```python
def _reality_check_loop():
    import reality_check
    time.sleep(90)  # let the scanner settle first
    while True:
        try:
            summary = reality_check.run_reality_check()
            if summary["dangerous"] or summary["lessons_decayed"]:
                <SEND_HELPER>(reality_check.format_alert(summary))
        except Exception as e:
            print(f"⚠️ reality_check_loop: {e}")
        time.sleep(6 * 60 * 60)  # every 6h
```

Then, in `main()`, where the other loops are started as daemon threads (next to the `_lesson_decay_loop` / `_regime_detector_loop` starts), add:

```python
    threading.Thread(target=_reality_check_loop, daemon=True).start()
    print("🔍 Reality-Check auditor פעיל - בודק פער paper↔live כל 6 שעות.")
```

- [ ] **Step 6: Verify the bot still imports and starts (smoke)**

Run: `cd $ROOT && python3 -c "import ast; ast.parse(open('bot.py').read()); print('bot.py parses OK')"`
Expected: `bot.py parses OK`
(Do NOT launch the live bot in the test; a running instance already exists. Parse-check only.)

- [ ] **Step 7: Commit**

```bash
cd $ROOT && git add reality_check.py bot.py tests/test_run_reality_check.py
git commit -m "feat: run_reality_check orchestration + 6h bot auditor loop + telegram alert"
```

---

## Self-Review

**Spec coverage:**
- §3.1 deterministic gap detection (paper_ev/live_ev per strategy, dangerous = n_live≥8 ∧ live<0 ∧ paper>0) → Task 1 (`compute_strategy_gaps`, `detect_dangerous`). ✓
- §3.2 graduated decay/recovery + state file + lesson confidence decay → Task 1 (`update_live_mults`) + Task 3 (`run_reality_check` lesson decay). ✓
- §3.3 apply `live_size_mult` in `classify_trade_intent` → Task 2. ✓
- §3.4 periodic bot loop → Task 3 Step 5. ✓
- §3.5 templated Telegram alert (no LLM) → Task 3 (`format_alert`). ✓
- §4 tests (detection, decay floor/cap, integration, lessons) → Task 1+2+3 tests. ✓
- §2 non-goals (no halt, no library rewrite, no regime-tag/Explorer) → respected. ✓

**Placeholder scan:** Tasks 1-2 are full code. Task 3 Step 5 uses `<SEND_HELPER>` deliberately — the exact Telegram send function must be read from `_lesson_decay_loop` and substituted; this is an explicit instruction to mirror an in-file pattern, not a vague TODO. All other steps are complete.

**Type consistency:** `attribute_strategy`, `compute_strategy_gaps`, `detect_dangerous`, `update_live_mults`, `get_live_size_mult`, `run_reality_check`, `format_alert` are defined in Task 1/3 and consumed with matching signatures in Task 2 (`get_live_size_mult`) and the tests. Constants (`MIN_LIVE_SAMPLE=8`, `DECAY_FACTOR=0.7`, `MULT_FLOOR=0.1`, `RECOVERY_FACTOR=1.15`, `MULT_CAP=1.0`) match the spec. Note: `NY Pullback Trader` is the real blessed name (hour-15 Pullback LONG) and `position_size_mult=1.3` for it — the integration test's 0.65 = 1.3×0.5 is correct.
