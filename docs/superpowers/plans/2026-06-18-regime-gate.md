# Regime Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the BTC agent from entering LONG into confirmed downtrends (and SHORT into confirmed uptrends) by adding a backtest-safe regime gate to the shared committee, then validate it against the real trade data.

**Architecture:** A pure function `regime_gate_veto()` reads the `market_summary` already computed at decision time (no network, no look-ahead) and returns a veto reason when a trade fights a confirmed trend. It is wired into `run_committee` — the single entry point used by BOTH live (`live_check.py`) and backtest (`scripts/learn_daily.py`) — as a pre-LLM early-return, mirroring the existing anti-strategy veto. A validation script re-checks the gate against all 283 closed trades.

**Tech Stack:** Python 3.9, pandas, requests (Binance public API via `data/binance_client.py`). No test framework installed → tests are runnable assert-scripts (`python3 tests/test_*.py`), matching the project's existing `scripts/test_*.py` convention.

## Global Constraints

- Working dir / project root: `/Users/guyamgar/Desktop/Agents_markering/trading_agent` (referred to below as `$ROOT`). All paths are relative to it unless absolute.
- **Not a git repo** (`Is a git repository: false`). Run `git init` once in `$ROOT` before starting if you want the commit steps to work; otherwise treat each "Commit" step as a checkpoint and skip it.
- Backtest-safety is sacred: the gate MUST read only `market_summary` (derived from candles up to the decision candle). It must NEVER call `regime_detector.compute_regime_shift` or any live fetch in the decision path (that would inject look-ahead into backtests).
- The gate is a hard rule like the existing anti-strategy veto: it returns `"החלטה": "אין כניסה"` with flag `_regime_rejected: True`, identical in shape to the anti path at `agents/orchestrator.py:182-218`.
- Hebrew keys are part of the data contract — copy them verbatim: setup direction = `"כיוון"` (values `"LONG"`/`"SHORT"`), `market_summary["trend"]` values = `"עולה"` (up) / `"יורד"` (down) / `"מעורבב"` (mixed).
- Scope: regime gate + `minutes_held` bug fix + immediate-data validation only. Kelly sizing, session×setup anti-strategies, and blocking experimental are explicitly OUT of scope (deferred per the spec).

---

### Task 1: Regime-gate pure function

**Files:**
- Create: `regime_gate.py`
- Test: `tests/test_regime_gate.py`

**Interfaces:**
- Produces: `regime_gate_veto(direction: str, market_summary: dict, require_ema_cross: bool = True) -> Optional[str]` — returns a veto reason string when the entry fights a confirmed trend, else `None`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_regime_gate.py`:

```python
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from regime_gate import regime_gate_veto

def _ms(trend, ema50, ema200):
    return {"trend": trend, "indicators": {"ema_50": ema50, "ema_200": ema200}}

def test_long_vetoed_in_confirmed_downtrend():
    assert regime_gate_veto("LONG", _ms("יורד", 100.0, 110.0)) is not None

def test_long_allowed_in_uptrend():
    assert regime_gate_veto("LONG", _ms("עולה", 110.0, 100.0)) is None

def test_long_allowed_in_mixed():
    assert regime_gate_veto("LONG", _ms("מעורבב", 100.0, 110.0)) is None

def test_long_not_vetoed_when_ema_not_crossed():
    # trend says down but EMA50 still above EMA200 → unconfirmed → no veto
    assert regime_gate_veto("LONG", _ms("יורד", 110.0, 100.0), require_ema_cross=True) is None

def test_short_vetoed_in_confirmed_uptrend():
    assert regime_gate_veto("SHORT", _ms("עולה", 110.0, 100.0)) is not None

def test_short_allowed_in_downtrend():
    assert regime_gate_veto("SHORT", _ms("יורד", 100.0, 110.0)) is None

def test_empty_direction_is_safe():
    assert regime_gate_veto("", _ms("יורד", 100.0, 110.0)) is None

if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print(f"PASS {fn.__name__}")
    print(f"\nALL {len(fns)} TESTS PASSED")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd $ROOT && python3 tests/test_regime_gate.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'regime_gate'`

- [ ] **Step 3: Write minimal implementation**

Create `regime_gate.py`:

```python
"""Regime gate: veto directional entries that fight a confirmed trend.

Pure function. Reads ONLY the market_summary already computed at decision time
(EMA-alignment trend + EMA50/EMA200), so it is identical in live and backtest
and introduces no look-ahead. Do NOT call live fetches from here.
"""
from typing import Optional


def regime_gate_veto(direction: str, market_summary: dict,
                     require_ema_cross: bool = True) -> Optional[str]:
    """Return a veto reason if this entry fights a confirmed trend, else None.

    LONG is vetoed in a confirmed downtrend; SHORT in a confirmed uptrend.
    'Confirmed' = the EMA9/21/50 alignment trend agrees AND (when
    require_ema_cross) the longer EMA50-vs-EMA200 relationship agrees too.
    """
    if not direction:
        return None
    d = direction.upper()
    ms = market_summary or {}
    trend = ms.get("trend")
    ind = ms.get("indicators") or {}
    ema50 = ind.get("ema_50")
    ema200 = ind.get("ema_200")
    have_emas = ema50 is not None and ema200 is not None

    if d == "LONG" and trend == "יורד":
        if (not require_ema_cross) or (have_emas and ema50 < ema200):
            return (f"REGIME GATE: LONG חסום בירידה מאוששת "
                    f"(trend=יורד, EMA50={ema50} < EMA200={ema200})")
    if d == "SHORT" and trend == "עולה":
        if (not require_ema_cross) or (have_emas and ema50 > ema200):
            return (f"REGIME GATE: SHORT חסום בעלייה מאוששת "
                    f"(trend=עולה, EMA50={ema50} > EMA200={ema200})")
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd $ROOT && python3 tests/test_regime_gate.py`
Expected: `ALL 7 TESTS PASSED`

- [ ] **Step 5: Commit**

```bash
cd $ROOT && git add regime_gate.py tests/test_regime_gate.py
git commit -m "feat: add backtest-safe regime_gate_veto pure function"
```

---

### Task 2: Wire the regime gate into run_committee

**Files:**
- Modify: `config.py` (add two flags after line 39)
- Modify: `agents/orchestrator.py` (insert gate after the `strategy_context` block, ~line 121, before the `extra` dict at line 123)
- Test: `tests/test_committee_regime_gate.py`

**Interfaces:**
- Consumes: `regime_gate_veto()` from Task 1; `config.REGIME_GATE_ENABLED`, `config.REGIME_LONG_VETO_REQUIRES_EMA_CROSS`.
- Produces: `run_committee(...)` returns, when vetoed, `result["head_decision"]["parsed"]["החלטה"] == "אין כניסה"` and `result["head_decision"]["parsed"]["_regime_rejected"] is True`, WITHOUT making any LLM/network call.

- [ ] **Step 1: Add config flags**

In `config.py`, immediately after line 39 (`FAST_MODE = False ...`), add:

```python

# ─── Regime gate (2026-06-18): block entries that fight a confirmed trend ───
# Diagnosis: live WR collapsed to 19% because the long-biased strategy set
# took 17 LONGs into a -9.58% downtrend. The gate vetoes LONG in confirmed
# downtrends / SHORT in confirmed uptrends, using only decision-time data.
REGIME_GATE_ENABLED = True
REGIME_LONG_VETO_REQUIRES_EMA_CROSS = True  # require EMA50/EMA200 to confirm the trend
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_committee_regime_gate.py`:

```python
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd $ROOT && python3 tests/test_committee_regime_gate.py`
Expected: FAIL — `AssertionError` (the committee currently tries to run advisors / does not return the `_regime_rejected` veto).

- [ ] **Step 4: Implement the gate wiring**

In `agents/orchestrator.py`, find the end of the `strategy_context` block (after the `except Exception as e: print(...)` around line 121, BEFORE `extra: Optional[Dict] = {}` at line 123) and insert:

```python
    # ─── Regime gate: veto entries that fight a confirmed trend (pre-LLM, backtest-safe) ───
    from config import REGIME_GATE_ENABLED, REGIME_LONG_VETO_REQUIRES_EMA_CROSS
    if REGIME_GATE_ENABLED and setup:
        from regime_gate import regime_gate_veto
        _regime_veto = regime_gate_veto(
            setup.get("כיוון", ""), market_summary,
            require_ema_cross=REGIME_LONG_VETO_REQUIRES_EMA_CROSS,
        )
        if _regime_veto:
            if verbose:
                print(f"  🚫 REGIME GATE veto: {_regime_veto}")
            return {
                "timestamp": market_summary.get("timestamp"),
                "market_summary": market_summary,
                "setup": setup,
                "advisors": {},
                "strategy_context": strategy_context,
                "head_decision": {
                    "parsed": {
                        "החלטה": "אין כניסה",
                        "סיבה_להחלטה": _regime_veto,
                        "כניסה": 0, "סטופ": 0, "יעד_1": 0, "יעד_2": 0,
                        "גודל_פוזיציה_USD": 0, "ביטחון_1_10": 0,
                        "_regime_rejected": True,
                    },
                    "raw": "{}", "is_error": False, "error": None,
                    "elapsed_sec": 0.0, "cost_usd": 0.0,
                },
                "totals": {"elapsed_sec": 0.0, "cost_usd": 0.0},
            }
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd $ROOT && python3 tests/test_committee_regime_gate.py`
Expected: `PASS test_long_into_downtrend_is_vetoed_without_llm` then `GATE VETO TEST PASSED ...`

- [ ] **Step 6: Commit**

```bash
cd $ROOT && git add config.py agents/orchestrator.py tests/test_committee_regime_gate.py
git commit -m "feat: wire regime gate into run_committee (pre-LLM veto, live+backtest)"
```

---

### Task 3: Fix the `minutes_held=0` recording bug

**Files:**
- Modify: `live_monitor.py` (`check_rec_status`, lines 37-90)
- Test: `tests/test_minutes_held.py`

**Interfaces:**
- Produces: `check_rec_status(...)` return dict now always includes `"minutes_held"` (float, minutes since `rec["opened_at"]`) on every closed outcome.

- [ ] **Step 1: Write the failing test**

Create `tests/test_minutes_held.py`:

```python
import os, sys
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live_monitor import check_rec_status

def _rec(direction="LONG"):
    opened = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    return {"direction": direction, "entry": 100.0, "stop": 98.0,
            "target_1": 103.0, "target_2": 105.0, "opened_at": opened,
            "max_wait_minutes": 24 * 60}

def test_minutes_held_present_on_target_hit():
    # candle high reaches target_1 (103) for a LONG → closes target_1
    res = check_rec_status(_rec("LONG"), current_price=103.0, candle_high=103.5, candle_low=101.0)
    assert res["closed"] is True, res
    assert "minutes_held" in res, "minutes_held missing (the bug)"
    assert res["minutes_held"] >= 100, res  # ~120 min

def test_minutes_held_present_on_stop_hit():
    res = check_rec_status(_rec("LONG"), current_price=97.0, candle_high=99.0, candle_low=97.0)
    assert res["outcome"] == "stop", res
    assert res["minutes_held"] >= 100, res

if __name__ == "__main__":
    test_minutes_held_present_on_target_hit(); print("PASS test_minutes_held_present_on_target_hit")
    test_minutes_held_present_on_stop_hit(); print("PASS test_minutes_held_present_on_stop_hit")
    print("\nALL TESTS PASSED")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd $ROOT && python3 tests/test_minutes_held.py`
Expected: FAIL — `AssertionError: minutes_held missing (the bug)`

- [ ] **Step 3: Implement the fix**

In `live_monitor.py`, replace the body of `check_rec_status` from line 47 (`# האם stop נפגע`) through the `return` at lines 84-90 with the version below. The change: compute `elapsed_min` once up front (from `opened_at`) and include `minutes_held` in every closed return.

```python
    # האם stop נפגע
    stop_hit = (candle_low <= stop) if is_long else (candle_high >= stop)
    t1_hit = (candle_high >= t1) if is_long else (candle_low <= t1)
    t2_hit = False
    if t2:
        t2_hit = (candle_high >= t2) if is_long else (candle_low <= t2)

    # זמן החזקה - מחושב לכל תוצאה (תיקון באג: בעבר חושב רק ב-timeout) ──
    opened = datetime.fromisoformat(
        rec["opened_at"].replace("Z", "+00:00") if rec["opened_at"].endswith("Z") else rec["opened_at"])
    if opened.tzinfo is None:
        opened = opened.replace(tzinfo=timezone.utc)
    elapsed_min = (datetime.now(timezone.utc) - opened).total_seconds() / 60

    # שמרני - אם גם stop וגם target באותו נר → stop
    if stop_hit:
        exit_price = stop
        outcome = "stop"
    elif t2_hit:
        exit_price = t2
        outcome = "target_2"
    elif t1_hit:
        exit_price = t1
        outcome = "target_1"
    else:
        # check timeout
        if elapsed_min >= rec.get("max_wait_minutes", 24 * 60):
            exit_price = current_price
            outcome = "timeout"
        else:
            return {"closed": False, "elapsed_min": round(elapsed_min, 1)}

    # חישוב P/L
    if is_long:
        gross_pnl_pct = (exit_price - entry) / entry * 100
    else:
        gross_pnl_pct = (entry - exit_price) / entry * 100
    net_pnl_pct = gross_pnl_pct - ROUND_TRIP_FEE_PCT

    return {
        "closed": True,
        "outcome": outcome,
        "exit_price": exit_price,
        "gross_pnl_pct": round(gross_pnl_pct, 3),
        "pnl_pct": round(net_pnl_pct, 3),
        "minutes_held": round(elapsed_min, 1),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd $ROOT && python3 tests/test_minutes_held.py`
Expected: `ALL TESTS PASSED`

- [ ] **Step 5: Commit**

```bash
cd $ROOT && git add live_monitor.py tests/test_minutes_held.py
git commit -m "fix: record minutes_held on all live exits (was 0 for every live trade)"
```

---

### Task 4: Validation script — gate precision against real trades

**Files:**
- Create: `scripts/validate_regime_gate.py`

**Interfaces:**
- Consumes: `regime_gate_veto()` (Task 1), `data.binance_client.BinanceClient`, `data.indicators.market_summary`.
- Produces: a printed report — for live vs paper, split by direction, how many trades the gate would veto and the actual WR/PnL of vetoed vs passed trades. Deliverable is the report; "test" = run it and read the numbers.

- [ ] **Step 1: Write the validation script**

Create `scripts/validate_regime_gate.py`:

```python
"""Validate the regime gate against historical trades (spec §6 #1-#2).
For each closed trade, fetch the 250 15m candles ending at its decision candle,
recompute market_summary, apply regime_gate_veto, and tally vetoed-vs-passed
WR/PnL. GOAL: vetoed trades should be mostly LOSERS (gate catches the bad ones)
and the gate must NOT veto the winning range-period LONGs (no over-blocking).
"""
import json, os, sys, time
from datetime import datetime, timezone
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from regime_gate import regime_gate_veto
from data.binance_client import BinanceClient
from data.indicators import market_summary

TRADES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory", "trades.json")

def ms_epoch(s):
    return int(datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp() * 1000)

def main():
    trades = [t for t in json.load(open(TRADES))["trades"]
              if t.get("status") == "closed" and t.get("simulation", {}).get("pnl_pct") is not None]
    client = BinanceClient()
    buckets = defaultdict(lambda: {"vetoed": [], "passed": []})
    for t in trades:
        hs = t["hunter_setup"]; direction = hs.get("כיוון")
        entry = float(t["decision"].get("כניסה") or 0)
        if not direction or entry <= 0:
            continue
        sym = "BTCUSDT" if entry > 10000 else "ETHUSDT"
        end = ms_epoch(t["timestamp_analyzed"]) + 15 * 60 * 1000  # include decision candle
        df = client.get_klines(sym, "15m", limit=250, end_time=end)
        time.sleep(0.08)
        if len(df) < 50:
            continue
        summary = market_summary(df)
        veto = regime_gate_veto(direction, summary)
        live = "live" if t.get("live_mode") is True else "paper"
        key = (live, direction)
        pnl = t["simulation"]["pnl_pct"]
        buckets[key]["vetoed" if veto else "passed"].append(pnl)

    def stat(lst):
        if not lst:
            return "n=0"
        w = sum(1 for p in lst if p > 0)
        return f"n={len(lst):>3}  WR={100*w/len(lst):>5.1f}%  totPnl={sum(lst):>+7.2f}%"

    print("=== REGIME GATE VALIDATION (vetoed should be losers; passed should keep the winners) ===")
    for key in sorted(buckets):
        live, direction = key
        b = buckets[key]
        print(f"\n[{live} {direction}]")
        print(f"  VETOED by gate : {stat(b['vetoed'])}")
        print(f"  PASSED by gate : {stat(b['passed'])}")

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the validation and read the result**

Run: `cd $ROOT && python3 scripts/validate_regime_gate.py`
Expected shape: a per-bucket report. The gate is working if, for `[live LONG]`, the VETOED line shows a low WR / negative totPnl (the gate is catching the losing downtrend longs), while for `[paper LONG]` the PASSED line retains a high WR (the gate did not kill the profitable range-period longs). Record the actual numbers — they feed spec §6 success criteria #1 (live flips toward ≥0) and #4 (gate precision).

- [ ] **Step 3: Commit**

```bash
cd $ROOT && git add scripts/validate_regime_gate.py
git commit -m "feat: add regime-gate validation script (gate precision vs real trades)"
```

---

## Follow-on validation (operational, not code)

These complete spec §6 but require running the live system over time — they are NOT code tasks:

- **§6 #2 full multi-regime backtest:** run `scripts/learn_daily.py` with the gate enabled over Binance history covering ≥3 regimes (an up leg, a range leg, a down/crash leg); require net-positive EV in EACH regime bucket, especially the down bucket ≥ breakeven.
- **§6 #3 forward live sample:** accumulate ≥30 new live trades with the gate active across ≥2 regimes; require positive net EV before any `$27K` reset.
- **Do not** start the `$27K` lite-evaluation reset until #1-#4 pass.

---

## Self-Review

**Spec coverage:**
- §3 Regime gate (integration point, backtest-safe signal, veto rule, rejection behavior) → Tasks 1 + 2. ✓
- §4 `minutes_held` fix → Task 3. ✓
- §5 testing (unit, live regression, over-blocking, backtest-safety) → unit tests in Tasks 1-3; live-regression + over-blocking in Task 4; backtest-safety enforced by Global Constraints + the pure-function design. ✓
- §6 success criteria #1-#2 → Task 4 produces the numbers; #2-full/#3/#4 → "Follow-on validation". ✓
- §2 non-goals (Kelly, session×setup anti-strategies, block-experimental, $27K freeze) → none implemented; $27K freeze stated in Follow-on. ✓
- §3.3 optional 4h escalation (`REGIME_4H_TREND_VETO_PCT`) → intentionally deferred (look-ahead risk in backtest); not implemented in v1. Noted here so it is not a silent gap.

**Placeholder scan:** No TBD/TODO; every code step has complete code and exact run commands. ✓

**Type consistency:** `regime_gate_veto(direction, market_summary, require_ema_cross=True)` is defined in Task 1 and called identically in Task 2 (`run_committee`) and Task 4 (validation). The veto return contract (`_regime_rejected: True`, `"החלטה": "אין כניסה"`) is consistent between Task 2's implementation and its test. `check_rec_status` return adds `minutes_held` consistently in Task 3's fix and test. ✓
