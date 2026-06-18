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
        "mults": {n: state["live_size_mult"][n] for n in all_names},
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
