"""Explorer — deterministic discovery of new (session × setup × direction × regime)
strategy candidates from accumulated closed trades. No LLM/network. Strict thresholds."""
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple

from strategies import STRATEGY_LIBRARY

ROOT = Path(__file__).parent
DISCOVERED_FILE = ROOT / "memory" / "discovered_strategies.json"

MIN_SAMPLE = 15
MIN_WR = 70.0
MIN_EV = 0.25

SESSIONS = [("Asia", 0, 7), ("London", 7, 13), ("NY", 13, 21), ("Night", 21, 24)]


def _session_bucket(hour: int) -> Tuple[str, int, int]:
    for name, a, b in SESSIONS:
        if a <= hour < b:
            return name, a, b
    return "Night", 21, 24


def _already_covered(start: int, end: int, setup: str, direction: str, regime: str) -> bool:
    """True if a blessed STRATEGY_LIBRARY entry already covers this window+setup+direction
    (probed at the bucket mid-hour). Bear-only strategies (required_downtrend=True) are
    treated as covering ONLY bear-regime candidates."""
    mid = (start + end) // 2
    dt = datetime(2026, 1, 1, mid, 30)
    for s in STRATEGY_LIBRARY:
        if s.kind != "blessed" or not s.is_active_at(dt) or not s.matches_setup(setup, direction):
            continue
        if s.required_downtrend and regime != "bear":
            continue  # bear-only strategy does not cover non-bear candidates
        return True
    return False


def discover_candidates(trades: List[Dict]) -> List[Dict]:
    groups: Dict[tuple, List[float]] = {}
    for t in trades:
        if t.get("status") != "closed":
            continue
        pnl = (t.get("simulation") or {}).get("pnl_pct")
        hs = t.get("hunter_setup") or {}
        setup, direction = hs.get("סוג"), hs.get("כיוון")
        regime = t.get("regime", "unknown")
        if pnl is None or not setup or not direction or regime == "unknown":
            continue
        try:
            dt = datetime.strptime(t["timestamp_analyzed"], "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        sess, a, b = _session_bucket(dt.hour)
        groups.setdefault((sess, a, b, setup, direction, regime), []).append(pnl)
    cands = []
    for (sess, a, b, setup, direction, regime), pnls in groups.items():
        n = len(pnls)
        if n < MIN_SAMPLE:
            continue
        wins = sum(1 for p in pnls if p > 0)
        wr = 100 * wins / n
        ev = sum(pnls) / n
        if wr < MIN_WR or ev < MIN_EV:
            continue
        if _already_covered(a, b, setup, direction, regime):
            continue
        cands.append({"session": sess, "start_hour_utc": a, "end_hour_utc": b,
                      "setup": setup, "direction": direction, "required_regime": regime,
                      "n": n, "wr": round(wr, 1), "ev": round(ev, 3)})
    return cands


def _load_discovered_raw() -> List[Dict]:
    if not DISCOVERED_FILE.exists():
        return []
    try:
        return json.loads(DISCOVERED_FILE.read_text())
    except Exception:
        return []


def _cand_key(c: Dict) -> tuple:
    return (c["start_hour_utc"], c["end_hour_utc"], c["setup"], c["direction"], c["required_regime"])


def promote_candidate(cand: Dict) -> Dict:
    """Write a discovered strategy at SMALL size (0.5); RC's live_size_mult grows/shrinks it."""
    name = f"Explorer: {cand['session']} {cand['setup']} {cand['direction']} ({cand['required_regime']})"
    rec = {
        "name": name,
        "description": f"discovered {cand['n']} trades WR {cand['wr']}% EV {cand['ev']}% in {cand['required_regime']}",
        "start_hour_utc": cand["start_hour_utc"], "end_hour_utc": cand["end_hour_utc"],
        "allowed_setups": [cand["setup"]], "allowed_directions": [cand["direction"]],
        "position_size_mult": 0.5, "kind": "blessed", "required_regime": cand["required_regime"],
        "source": "explorer", "hist_wr": cand["wr"], "hist_trades": cand["n"],
    }
    disc = _load_discovered_raw()
    disc.append(rec)
    DISCOVERED_FILE.parent.mkdir(parents=True, exist_ok=True)
    DISCOVERED_FILE.write_text(json.dumps(disc, ensure_ascii=False, indent=2))
    return rec


def run_explorer() -> Dict:
    from memory_store import load_trades
    cands = discover_candidates(load_trades())
    existing_keys = {(d["start_hour_utc"], d["end_hour_utc"], d["allowed_setups"][0],
                      d["allowed_directions"][0], d.get("required_regime"))
                     for d in _load_discovered_raw()
                     if d.get("allowed_setups") and d.get("allowed_directions")}
    promoted = []
    for c in cands:
        if _cand_key(c) in existing_keys:
            continue
        promoted.append(promote_candidate(c))
        existing_keys.add(_cand_key(c))
    return {"promoted": promoted, "candidates": cands}


def format_explorer_alert(summary: Dict) -> str:
    if not summary["promoted"]:
        return "Explorer: אין מועמדים חדשים שעוברים את הסף."
    lines = ["Explorer — אסטרטגיות חדשות קודמו (גודל קטן, RC ישפוט):"]
    for r in summary["promoted"]:
        lines.append(f"  {r['name']} — WR {r['hist_wr']}% על {r['hist_trades']} עסקאות, size x0.5")
    return "\n".join(lines)
