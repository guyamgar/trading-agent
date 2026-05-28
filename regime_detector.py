"""
זיהוי שינוי משטר בשוק.

הרעיון: מחשבים "טביעת אצבע" של 30 הימים האחרונים על 4 מטריקות פשוטות,
ומשווים ל-30 הימים שלפניהן. אם מטריקה זזה ביותר מ-2 סטיות תקן —
זה signal שהשוק שינה אופי באופן משמעותי, ולקחים ישנים עלולים להיות לא רלוונטיים.

לא יוצר auto-pause; רק מדווח. ההחלטה מה לעשות נשארת בידי המשתמש (ולעתיד -
ב-Hunter/Committee שיוכלו לקרוא את regime_state.json).
"""
import json
from pathlib import Path
from datetime import datetime
from typing import Dict

import pandas as pd

from data.binance_client import BinanceClient
from data.indicators import add_indicators  # מוסיף rsi, atr_pct, ema_50 וכו'

ROOT = Path(__file__).parent
REGIME_STATE_FILE = ROOT / "memory" / "regime_state.json"

# חלון בסיס: 30 ימי 4h = 180 נרות. חלון נוכחי: 7 ימים = 42 נרות.
BASELINE_CANDLES = 180
RECENT_CANDLES = 42
SIGMA_THRESHOLD = 2.0  # 2σ = שינוי מובהק


def _fingerprint(df: pd.DataFrame) -> Dict:
    """
    מחשב 4 מטריקות שמתארות את אופי השוק:
    1. atr_pct_mean - תנודתיות ממוצעת (% מהמחיר)
    2. rsi_mean - מקום ממוצע ב-RSI (50=ניטרלי, 60+=bull, 40-=bear)
    3. trend_strength - שיפוע EMA50 (כמה אחוז זז המידיאן הנע ב-X נרות)
    4. body_to_range - מאשר אם הנרות נמלאים (טרנדיים) או דוחקים (sideways)
    """
    if df.empty or "atr_pct" not in df.columns:
        return {}

    valid = df.dropna(subset=["atr_pct", "rsi"])
    if len(valid) < 10:
        return {}

    atr_pct_mean = float(valid["atr_pct"].mean())
    rsi_mean = float(valid["rsi"].mean())

    # שיפוע EMA50 - אחוז שינוי מהתחלה לסוף
    ema = valid["ema_50"].dropna()
    if len(ema) >= 2 and ema.iloc[0] > 0:
        trend_strength = float((ema.iloc[-1] - ema.iloc[0]) / ema.iloc[0] * 100)
    else:
        trend_strength = 0.0

    # אחוז גוף לטווח - נרות מלאים = טרנד; נרות עם זנבות = sideways
    body = (valid["close"] - valid["open"]).abs()
    rng = (valid["high"] - valid["low"]).replace(0, 1e-9)
    body_to_range = float((body / rng).mean())

    return {
        "atr_pct_mean": round(atr_pct_mean, 3),
        "rsi_mean": round(rsi_mean, 2),
        "trend_strength_pct": round(trend_strength, 3),
        "body_to_range": round(body_to_range, 3),
    }


def _load_state() -> Dict:
    if not REGIME_STATE_FILE.exists():
        return {}
    try:
        return json.loads(REGIME_STATE_FILE.read_text())
    except Exception:
        return {}


def _save_state(state: Dict):
    REGIME_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now().isoformat()
    REGIME_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def compute_regime_shift(symbol: str = "BTCUSDT") -> Dict:
    """
    משווה את 7 הימים האחרונים ל-30 הימים שקדמו להם.
    מחזיר dict עם:
    - shifts: dict של {metric: z_score} לכל המטריקות
    - max_shift_metric, max_shift_value
    - regime_changed: True/False (האם חרגנו מ-SIGMA_THRESHOLD)
    - explanation: טקסט קצר בעברית
    """
    client = BinanceClient()
    # שני חלונות: בסיס + עכשיו. מושך נרות 4h.
    total_needed = BASELINE_CANDLES + RECENT_CANDLES + 10
    df = client.get_klines(symbol, "4h", limit=total_needed)
    df = add_indicators(df)  # מוסיף rsi, atr_pct, ema_50

    if len(df) < BASELINE_CANDLES + RECENT_CANDLES:
        return {"error": f"לא מספיק דאטה: {len(df)} נרות במקום {BASELINE_CANDLES + RECENT_CANDLES}"}

    df_baseline = df.iloc[-(BASELINE_CANDLES + RECENT_CANDLES):-RECENT_CANDLES]
    df_recent = df.iloc[-RECENT_CANDLES:]

    fp_baseline = _fingerprint(df_baseline)
    fp_recent = _fingerprint(df_recent)
    if not fp_baseline or not fp_recent:
        return {"error": "כשל בחישוב fingerprint"}

    # סטיית תקן לכל מטריקה מחושבת לפי החלון הבסיסי - דורש לחשב על rolling
    shifts = {}
    metrics = ["atr_pct_mean", "rsi_mean", "trend_strength_pct", "body_to_range"]

    # מחשבים rolling fingerprint על חלונות של RECENT_CANDLES בתוך הבסיס
    # ככה נקבל סטיית תקן רלוונטית
    rolling_metrics = {m: [] for m in metrics}
    step = RECENT_CANDLES // 2  # חופפים חצי
    for start in range(0, len(df_baseline) - RECENT_CANDLES, step):
        win = df_baseline.iloc[start:start + RECENT_CANDLES]
        fp = _fingerprint(win)
        if fp:
            for m in metrics:
                rolling_metrics[m].append(fp[m])

    explanations = []
    max_shift_metric = None
    max_shift_value = 0.0

    for m in metrics:
        values = rolling_metrics[m]
        if len(values) < 3:
            shifts[m] = {"z_score": 0, "baseline_mean": fp_baseline[m], "current": fp_recent[m]}
            continue
        series = pd.Series(values)
        mean = float(series.mean())
        std = float(series.std()) or 1e-9
        z = (fp_recent[m] - mean) / std
        shifts[m] = {
            "z_score": round(z, 2),
            "baseline_mean": round(mean, 3),
            "baseline_std": round(std, 3),
            "current": fp_recent[m],
        }
        if abs(z) > abs(max_shift_value):
            max_shift_value = z
            max_shift_metric = m

        if abs(z) >= SIGMA_THRESHOLD:
            label_he = {
                "atr_pct_mean": "תנודתיות",
                "rsi_mean": "RSI ממוצע",
                "trend_strength_pct": "כוח טרנד",
                "body_to_range": "אופי נרות",
            }.get(m, m)
            direction = "↑" if z > 0 else "↓"
            explanations.append(f"{label_he} {direction} ({z:+.1f}σ: {mean:.2f}→{fp_recent[m]:.2f})")

    regime_changed = abs(max_shift_value) >= SIGMA_THRESHOLD

    return {
        "regime_changed": regime_changed,
        "max_shift_metric": max_shift_metric,
        "max_shift_z": round(max_shift_value, 2),
        "shifts": shifts,
        "baseline_fingerprint": fp_baseline,
        "recent_fingerprint": fp_recent,
        "explanation": " | ".join(explanations) if explanations else "השוק יציב",
        "symbol": symbol,
        "computed_at": datetime.now().isoformat(),
    }


def check_and_persist(symbol: str = "BTCUSDT") -> Dict:
    """
    מריץ זיהוי, שומר ב-regime_state.json, ומחזיר את התוצאה.
    אם זה שינוי חדש (לא היה ב-state הקודם) - מסמן 'is_new_alert'.
    """
    result = compute_regime_shift(symbol)
    if "error" in result:
        return result

    prev_state = _load_state()
    prev_changed = prev_state.get("regime_changed", False)
    is_new_alert = result["regime_changed"] and not prev_changed

    state = {
        **prev_state,
        **result,
        "is_new_alert": is_new_alert,
    }

    # מוסיף ל-history
    if result["regime_changed"]:
        state.setdefault("alerts_history", []).append({
            "at": result["computed_at"],
            "metric": result["max_shift_metric"],
            "z": result["max_shift_z"],
            "explanation": result["explanation"],
        })
        # שמירה של עד 50 התראות
        state["alerts_history"] = state["alerts_history"][-50:]

    _save_state(state)
    result["is_new_alert"] = is_new_alert
    return result


if __name__ == "__main__":
    r = check_and_persist("BTCUSDT")
    print(json.dumps(r, indent=2, ensure_ascii=False, default=str))
