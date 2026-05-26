"""
live_check - בודק את השוק עכשיו (לייב, לא היסטורי) ומאתר setup.
אם יש - מוסיף ל-open_recommendations.json שיעקב אחריו ב-live_monitor.
"""
import sys
import json
import uuid
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from data.binance_client import BinanceClient
from data.indicators import market_summary, candle_window
from agents.orchestrator import run_hunter, run_committee, run_devil_advocate
from memory_store import relevant_lessons, load_trades
from config import SYMBOL, TIMEFRAME_ANALYSIS

OPEN_RECS_FILE = ROOT / "memory" / "open_recommendations.json"
MIN_HUNTER_QUALITY = 4  # הורד מ-5 להגדלת תדירות עסקאות; הוועדה היא הפילטר האמיתי


def is_market_worth_scanning(summary: dict) -> tuple:
    """פילטר Python מהיר וזול - חוסך קריאות לצייד על נרות משעממים."""
    ind = summary["indicators"]
    rsi = ind["rsi"]
    bb_width = ind["bb_width"]
    vol_ratio = ind["volume_ratio"]
    atr_pct = ind["atr_pct"]
    last_change = abs(summary["candle"]["change_pct"])

    triggers = []
    if rsi >= 65 or rsi <= 35:
        triggers.append(f"RSI {rsi:.1f}")
    if bb_width <= 0.012:
        triggers.append(f"squeeze {bb_width:.4f}")
    if vol_ratio >= 1.5 or vol_ratio <= 0.4:
        triggers.append(f"vol {vol_ratio:.2f}")
    if last_change >= 0.4:
        triggers.append(f"strong candle {last_change:.2f}%")
    if atr_pct >= 0.35:
        triggers.append(f"vol high {atr_pct:.2f}%")

    return (bool(triggers), " + ".join(triggers) if triggers else "שקט")


def load_open_recs() -> list:
    if not OPEN_RECS_FILE.exists():
        return []
    try:
        return json.loads(OPEN_RECS_FILE.read_text()).get("open", [])
    except Exception:
        return []


def save_open_recs(recs: list):
    OPEN_RECS_FILE.parent.mkdir(parents=True, exist_ok=True)
    OPEN_RECS_FILE.write_text(json.dumps({"open": recs}, ensure_ascii=False, indent=2))


def check_live_market(verbose: bool = True, symbol: str = None) -> dict:
    """
    סורק את מצב השוק החי עכשיו, מפעיל את הצוות, ומחזיר ניתוח.
    אם נמצא setup ראוי - שומר ב-open_recommendations.json.
    """
    sym = symbol or SYMBOL
    if verbose:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] בודק שוק חי ({sym})...")

    # 1. דאטה חי
    client = BinanceClient()
    df = client.get_klines(sym, TIMEFRAME_ANALYSIS, limit=250)
    summary = market_summary(df)
    summary["symbol"] = sym

    if verbose:
        print(f"   מחיר נוכחי: ${summary['price']:,.2f} | "
              f"RSI {summary['indicators']['rsi']} | "
              f"טרנד {summary['trend']}")

    # 1.5 פילטר Python - חוסך קריאות לצייד על נרות משעממים
    worth_it, reason = is_market_worth_scanning(summary)
    if not worth_it:
        if verbose:
            print(f"   ⊘ פילטר: {reason} - מדלג בלי לקרוא לצייד")
        return {"status": "filtered_quiet", "reason": reason, "summary": summary}

    # 2. צייד
    candles_view = candle_window(df, n=25)
    lessons = relevant_lessons(summary, limit=5)
    hunter = run_hunter(summary, candles_view, lessons=lessons, verbose=False)

    if hunter["is_error"] or not hunter["parsed"]:
        return {"status": "hunter_error", "error": hunter.get("error"), "summary": summary}

    setups = hunter["parsed"].get("setups", [])
    valid = [s for s in setups if (s.get("ציון_איכות", 0) or 0) >= MIN_HUNTER_QUALITY]

    if not valid:
        return {
            "status": "no_setup",
            "summary": summary,
            "hunter_assessment": hunter["parsed"].get("הערכת_שוק_כללית", "")[:300],
        }

    best = max(valid, key=lambda s: s.get("ציון_איכות", 0))
    if verbose:
        print(f"   ✓ Setup: {best['סוג']} {best['כיוון']} (ציון {best['ציון_איכות']})")

    # 3. ועדה
    history = load_trades()
    committee = run_committee(summary, setup=best, lessons=lessons, history=history, verbose=False)
    decision = committee["head_decision"]["parsed"]

    # 4. פרקליט אם דחו
    advocate_overrode = False
    if not decision or decision.get("החלטה") == "אין כניסה":
        if verbose:
            reason = (decision or {}).get("סיבה_להחלטה", "לא ידוע")
            print(f"   ⊘ הוועדה דחתה: {reason[:120]}")

        if lessons and decision:
            advocate = run_devil_advocate(best, decision, lessons, summary, verbose=False)
            ap = advocate.get("parsed") or {}
            if ap.get("תקיפה_מוצלחת") and (ap.get("ביטחון_בערעור") or 0) >= 7:
                advocate_overrode = True
                tighter = ap.get("סטופ_מומלץ_הדוק_יותר")
                entry = (best["אזור_כניסה"]["מחיר_מ"] + best["אזור_כניסה"]["מחיר_עד"]) / 2
                decision = {
                    "החלטה": best["כיוון"],
                    "כניסה": entry,
                    "סטופ": float(tighter) if tighter else best["סטופ_מומלץ"],
                    "יעד_1": best["יעדים_מומלצים"][0],
                    "יעד_2": (best["יעדים_מומלצים"][1]
                              if len(best["יעדים_מומלצים"]) > 1 else None),
                    "ביטחון_1_10": ap.get("ביטחון_בערעור"),
                    "סיבה_להחלטה": f"OVERRIDE: {ap.get('סיבה_מפורטת', '')[:200]}",
                    "_advocate_override": True,
                }
                if verbose:
                    print(f"   🎭 פרקליט עירער ועבר!")
            else:
                return {
                    "status": "rejected",
                    "reason": (decision or {}).get("סיבה_להחלטה", "")[:200],
                    "setup": best,
                    "summary": summary,
                    "advocate": advocate.get("parsed"),
                }

    # 5. שמירה כ-open recommendation
    rec_id = str(uuid.uuid4())[:8]
    rec = {
        "id": rec_id,
        "symbol": sym,
        "opened_at": datetime.utcnow().isoformat(),
        "opened_at_price": summary["price"],
        "opened_at_candle": summary["timestamp"],
        "direction": decision["החלטה"],
        "entry": float(decision["כניסה"]),
        "stop": float(decision["סטופ"]),
        "target_1": float(decision["יעד_1"]),
        "target_2": float(decision.get("יעד_2") or 0) or None,
        "setup_type": best["סוג"],
        "setup_score": best["ציון_איכות"],
        "via_advocate": advocate_overrode,
        "confidence": decision.get("ביטחון_1_10"),
        "reason": decision.get("סיבה_להחלטה", "")[:300],
        "max_wait_minutes": 24 * 60,  # מקסימום יום
    }

    open_recs = load_open_recs()
    open_recs.append(rec)
    save_open_recs(open_recs)

    if verbose:
        print(f"   ✅ המלצה נשמרה: {rec_id}")
        print(f"      {rec['direction']} | entry ${rec['entry']:,.2f} | "
              f"stop ${rec['stop']:,.2f} | target ${rec['target_1']:,.2f}")

    return {
        "status": "new_recommendation",
        "rec": rec,
        "summary": summary,
    }


if __name__ == "__main__":
    result = check_live_market(verbose=True)
    print(f"\n{json.dumps(result, indent=2, ensure_ascii=False, default=str)[:600]}")
