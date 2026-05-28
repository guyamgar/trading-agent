"""
Hold-out backtest - בדיקה האם המערכת מכלילה על דאטה היסטורי שלא ראתה.

המטרה: למנוע overfitting. המערכת לומדת על 7-30 ימים אחרונים. כאן מריצים
את הצוות (Hunter + Committee) על דאטה מ-30-60 ימים אחורה ובודקים אם
הוא מצליח באותה רמה. אם נכשל - יש סיכון לבעיית overfitting.

זול בכוונה: 10 דגימות אקראיות מתוך החלון, לא סריקה צפופה.
"""
import json
import random
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List

import pandas as pd

from data.binance_client import BinanceClient
from data.indicators import add_indicators, market_summary, candle_window
from agents.orchestrator import run_hunter, run_committee
from agents.trade_simulator import simulate_trade
from memory_store import relevant_lessons, load_trades, load_lessons

ROOT = Path(__file__).parent
HOLDOUT_RESULTS_FILE = ROOT / "memory" / "holdout_results.json"

# חלון "מוסתר": 30-60 ימים אחורה. רחוק מספיק מהאימון כדי להיות "לא נראה".
HOLDOUT_DAYS_AGO_MIN = 30
HOLDOUT_DAYS_AGO_MAX = 60

# פרמטרים כמו ב-learn_daily.py
CONTEXT_BEFORE = 250
SIM_FORWARD_CANDLES = 96
MIN_HUNTER_QUALITY = 4

# כמה דגימות לבדוק - 10 איזון בין דיוק לעלות LLM
DEFAULT_N_SAMPLES = 10


def _fetch_holdout_window(symbol: str = "BTCUSDT") -> pd.DataFrame:
    """
    מושך נרות 15m מהחלון המוסתר (30-60 ימים אחורה).
    מחזיר DataFrame מורחב כך שיש מקום ל-CONTEXT לפני וגם SIM_FORWARD אחרי.
    """
    client = BinanceClient()
    end_dt = datetime.utcnow() - timedelta(days=HOLDOUT_DAYS_AGO_MIN)
    start_dt = datetime.utcnow() - timedelta(days=HOLDOUT_DAYS_AGO_MAX)

    end_ms = int(end_dt.timestamp() * 1000)
    start_ms = int(start_dt.timestamp() * 1000)

    # 15m × 30 days ≈ 2880 נרות. Binance limit=1000 לבקשה - נמשוך בשתיים.
    all_frames = []
    cur_start = start_ms
    while cur_start < end_ms:
        df = client.get_klines(symbol, "15m", limit=1000, start_time=cur_start, end_time=end_ms)
        if df.empty:
            break
        all_frames.append(df)
        # הקפצת הסטרט לסוף הנר האחרון + 1ms
        last_close = int(df["close_time"].iloc[-1].timestamp() * 1000) + 1
        if last_close <= cur_start:
            break
        cur_start = last_close

    if not all_frames:
        return pd.DataFrame()
    df = pd.concat(all_frames, ignore_index=True)
    df = df.drop_duplicates(subset=["open_time"]).reset_index(drop=True)
    df = add_indicators(df)
    return df


def _pick_random_anchors(df: pd.DataFrame, n_samples: int) -> List[int]:
    """
    בוחר n מיקומים אקראיים בתוך החלון, כך שיש מקום לקונטקסט לפני
    ו-simulation אחרי.
    """
    earliest = CONTEXT_BEFORE + 5
    latest = len(df) - SIM_FORWARD_CANDLES - 5
    if latest <= earliest:
        return []
    available = list(range(earliest, latest))
    n = min(n_samples, len(available))
    return sorted(random.sample(available, n))


def _eval_sample(df: pd.DataFrame, idx: int, lessons: List[Dict]) -> Dict:
    """
    מריץ את הפייפליין על נקודה אחת:
    1. Hunter על 250 נרות אחרונים
    2. אם נמצא setup ראוי - Committee
    3. אם אושר - simulate forward על SIM_FORWARD_CANDLES נרות הבאים
    מחזיר dict עם תוצאות לסטטיסטיקה.
    """
    df_until = df.iloc[idx - CONTEXT_BEFORE: idx + 1].reset_index(drop=True)
    df_future = df.iloc[idx + 1: idx + 1 + SIM_FORWARD_CANDLES].reset_index(drop=True)

    summary = market_summary(df_until)
    candles_view = candle_window(df_until, n=25)

    hunter = run_hunter(summary, candles_view, lessons=lessons[:5], verbose=False)
    if hunter.get("is_error") or not hunter.get("parsed"):
        return {"status": "hunter_error", "idx": idx, "error": hunter.get("error", "")}

    setups = hunter["parsed"].get("setups", [])
    valid = [s for s in setups if (s.get("ציון_איכות", 0) or 0) >= MIN_HUNTER_QUALITY]
    if not valid:
        return {"status": "no_setup", "idx": idx}

    best = max(valid, key=lambda s: s.get("ציון_איכות", 0))
    committee = run_committee(summary, setup=best, lessons=lessons[:5],
                              history=load_trades(), verbose=False,
                              training_mode=False)
    decision = (committee.get("head_decision") or {}).get("parsed") or {}

    if decision.get("החלטה") in (None, "אין כניסה"):
        return {
            "status": "rejected",
            "idx": idx,
            "setup_type": best.get("סוג"),
            "direction": best.get("כיוון"),
            "reason": (decision.get("סיבה_להחלטה") or "")[:200],
        }

    # מסמלץ קדימה
    sim = simulate_trade(decision, df_future)
    return {
        "status": "executed",
        "idx": idx,
        "setup_type": best.get("סוג"),
        "direction": decision.get("החלטה"),
        "entry": float(decision.get("כניסה", 0)),
        "outcome": sim.get("outcome"),
        "pnl_pct": sim.get("pnl_pct"),
        "minutes_held": sim.get("minutes_held"),
    }


def run_holdout_backtest(symbol: str = "BTCUSDT", n_samples: int = DEFAULT_N_SAMPLES) -> Dict:
    """
    הרץ ראשי. מחזיר סטטיסטיקה שמשווה את ביצועי המערכת על דאטה לא נראה
    מול ביצועיה האחרונים (in-sample).
    """
    print(f"[holdout] מושך חלון מוסתר ({HOLDOUT_DAYS_AGO_MIN}-{HOLDOUT_DAYS_AGO_MAX} ימים אחורה) ל-{symbol}")
    df = _fetch_holdout_window(symbol)
    if df.empty or len(df) < CONTEXT_BEFORE + SIM_FORWARD_CANDLES + 10:
        return {"error": f"לא מספיק דאטה: {len(df)} נרות"}

    anchors = _pick_random_anchors(df, n_samples)
    if not anchors:
        return {"error": "לא נמצאו נקודות אקראיות תקינות"}

    lessons = load_lessons()
    # ממיינים לפי confidence ולוקחים את הטובים ביותר (כמו בלייב)
    lessons_sorted = sorted(lessons, key=lambda l: -(l.get("confidence") or 0))

    print(f"[holdout] מריץ {len(anchors)} דגימות עם {len(lessons)} לקחים נוכחיים")
    samples = []
    for i, idx in enumerate(anchors, 1):
        print(f"[holdout] {i}/{len(anchors)} - אינדקס {idx}")
        try:
            res = _eval_sample(df, idx, lessons_sorted)
        except Exception as e:
            res = {"status": "error", "idx": idx, "error": str(e)[:200]}
        samples.append(res)

    # סטטיסטיקה
    executed = [s for s in samples if s["status"] == "executed"]
    wins = [s for s in executed if (s.get("pnl_pct") or 0) > 0]
    losses = [s for s in executed if (s.get("pnl_pct") or 0) <= 0]
    rejected = [s for s in samples if s["status"] == "rejected"]
    no_setup = [s for s in samples if s["status"] == "no_setup"]
    errors = [s for s in samples if s["status"] in ("hunter_error", "error")]

    total_pnl = sum((s.get("pnl_pct") or 0) for s in executed)
    avg_pnl = total_pnl / max(len(executed), 1)
    wr = len(wins) / max(len(executed), 1) * 100

    # השוואה לביצועים האחרונים (in-sample)
    in_sample = _compute_in_sample_stats()

    result = {
        "symbol": symbol,
        "ran_at": datetime.now().isoformat(),
        "holdout_window_days": f"{HOLDOUT_DAYS_AGO_MIN}-{HOLDOUT_DAYS_AGO_MAX}",
        "n_samples": len(samples),
        "n_executed": len(executed),
        "n_rejected": len(rejected),
        "n_no_setup": len(no_setup),
        "n_errors": len(errors),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(wr, 1),
        "avg_pnl_pct": round(avg_pnl, 3),
        "total_pnl_pct": round(total_pnl, 3),
        "in_sample": in_sample,
        "samples": samples,
    }
    # שמירה ב-history
    _persist(result)
    return result


def _compute_in_sample_stats(days: int = 14) -> Dict:
    """חישוב WR ו-P/L ממוצע על הימים האחרונים - נשמר ל-trades.json."""
    trades = load_trades()
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    recent = [t for t in trades if t.get("status") == "closed" and t.get("created_at", "") >= cutoff]
    if not recent:
        return {"days": days, "n": 0}
    wins = [t for t in recent if (t.get("simulation") or {}).get("pnl_pct", 0) > 0]
    pnls = [(t.get("simulation") or {}).get("pnl_pct", 0) for t in recent]
    return {
        "days": days,
        "n": len(recent),
        "win_rate_pct": round(len(wins) / len(recent) * 100, 1),
        "avg_pnl_pct": round(sum(pnls) / len(pnls), 3),
        "total_pnl_pct": round(sum(pnls), 3),
    }


def _persist(result: Dict):
    history = []
    if HOLDOUT_RESULTS_FILE.exists():
        try:
            history = json.loads(HOLDOUT_RESULTS_FILE.read_text())
        except Exception:
            history = []
    history.append(result)
    history = history[-20:]  # עד 20 ריצות אחרונות
    HOLDOUT_RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    HOLDOUT_RESULTS_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2, default=str))


def load_last_holdout_result() -> Dict:
    if not HOLDOUT_RESULTS_FILE.exists():
        return {}
    try:
        history = json.loads(HOLDOUT_RESULTS_FILE.read_text())
        return history[-1] if history else {}
    except Exception:
        return {}


if __name__ == "__main__":
    r = run_holdout_backtest("BTCUSDT", n_samples=3)
    print(json.dumps(r, indent=2, ensure_ascii=False, default=str))
