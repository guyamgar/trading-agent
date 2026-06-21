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
