"""
live_monitor - בודק מחיר חי כל X דקות ובודק אם המלצות פתוחות נגעו ב-stop/target.
כשעסקה נסגרת - מריץ ועדה ביקורתית, שומר לקח, ומעדכן חשבון.
"""
import sys
import json
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from data.binance_client import BinanceClient
from agents.orchestrator import run_critique
from memory_store import (
    save_trade, save_lesson, get_stats, update_account_after_trade,
)
from config import SYMBOL, ROUND_TRIP_FEE_PCT

OPEN_RECS_FILE = ROOT / "memory" / "open_recommendations.json"


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


def check_rec_status(rec: dict, current_price: float, candle_high: float, candle_low: float) -> dict:
    """
    בודק אם הההמלצה צריכה להיסגר: stop, target, או timeout.
    """
    is_long = rec["direction"] == "LONG"
    entry = rec["entry"]
    stop = rec["stop"]
    t1 = rec["target_1"]
    t2 = rec.get("target_2")

    # האם stop נפגע
    stop_hit = (candle_low <= stop) if is_long else (candle_high >= stop)
    t1_hit = (candle_high >= t1) if is_long else (candle_low <= t1)
    t2_hit = False
    if t2:
        t2_hit = (candle_high >= t2) if is_long else (candle_low <= t2)

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
        opened = datetime.fromisoformat(rec["opened_at"].replace("Z", "+00:00") if rec["opened_at"].endswith("Z") else rec["opened_at"])
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        elapsed_min = (now - opened).total_seconds() / 60
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
    }


def close_recommendation(rec: dict, exit_info: dict, verbose: bool = True) -> dict:
    """
    סוגר המלצה - שומר כעסקה, מריץ ועדה ביקורתית, מעדכן חשבון, שומר לקח.
    """
    sim = {
        "outcome": exit_info["outcome"],
        "exit_price": exit_info["exit_price"],
        "pnl_pct": exit_info["pnl_pct"],
        "gross_pnl_pct": exit_info["gross_pnl_pct"],
        "minutes_held": exit_info.get("minutes_held", 0),
        "pnl_usd_per_unit": round(rec["entry"] * (exit_info["pnl_pct"] / 100), 2),
        "fee_pct": ROUND_TRIP_FEE_PCT,
    }

    trade_obj = {
        "session": datetime.utcnow().strftime("%Y-%m-%d"),
        "stage": 2,
        "mode": "paper_live",
        "symbol": rec.get("symbol", SYMBOL),
        "rec_id": rec["id"],
        "timestamp_analyzed": rec.get("opened_at_candle"),
        "hunter_setup": {
            "סוג": rec["setup_type"],
            "כיוון": rec["direction"],
            "ציון_איכות": rec.get("setup_score"),
        },
        "decision": {
            "החלטה": rec["direction"],
            "כניסה": rec["entry"],
            "סטופ": rec["stop"],
            "יעד_1": rec["target_1"],
            "יעד_2": rec.get("target_2"),
            "סיבה_להחלטה": rec.get("reason", ""),
        },
        "simulation": sim,
        "status": "closed",
        "pnl_usd": sim["pnl_usd_per_unit"],
        "entered_via_advocate": rec.get("via_advocate", False),
        "live_mode": True,
    }

    if verbose:
        print(f"\n🔍 ועדה ביקורתית...")
    critique = run_critique(trade_obj, get_stats(), verbose=False)
    critic = critique["reviewers"].get("המבקר", {}).get("parsed") or {}
    coach = critique["reviewers"].get("המאמן", {}).get("parsed") or {}
    stat = critique["reviewers"].get("הסטטיסטיקאי", {}).get("parsed") or {}

    trade_obj["post_trade"] = {
        "critic": critic,
        "coach": coach,
        "statistician": stat,
    }

    # שמירת לקח
    new_lesson = (coach or {}).get("לקח_חדש")
    if new_lesson and new_lesson not in (None, "null", "אין לקח חדש"):
        save_lesson({
            "rule": new_lesson,
            "trigger": (coach or {}).get("תנאי_הפעלה"),
            "category": (coach or {}).get("סיווג"),
            "from_outcome": sim["outcome"],
            "live_mode": True,
        })

    save_trade(trade_obj)

    # עדכון חשבון
    acc_update = update_account_after_trade(sim["pnl_pct"], sim["pnl_pct"] > 0)

    return {
        "trade": trade_obj,
        "new_lesson": new_lesson if new_lesson else None,
        "account_update": acc_update,
    }


def poll_open_recommendations(verbose: bool = True) -> list:
    """
    בודק את כל ההמלצות הפתוחות מול מחיר חי. סוגר אלה שנגעו בstop/target.
    מחזיר רשימת events שקרו (סגירות) - לדיווח בטלגרם.
    """
    open_recs = load_open_recs()
    if not open_recs:
        return []

    client = BinanceClient()

    # cache לכל סימבול - מושך את הנר/המחיר פעם אחת
    market_cache = {}

    def _get_market(sym: str):
        if sym in market_cache:
            return market_cache[sym]
        df = client.get_klines(sym, "15m", limit=2)
        latest = df.iloc[-1]
        info = {
            "high": float(latest["high"]),
            "low": float(latest["low"]),
            "price": client.get_current_price(sym),
        }
        market_cache[sym] = info
        return info

    if verbose:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] בודק {len(open_recs)} המלצות פתוחות")

    still_open = []
    closed_events = []

    for rec in open_recs:
        sym = rec.get("symbol") or SYMBOL  # back-compat לרשומות ישנות
        mk = _get_market(sym)
        candle_high, candle_low, current_price = mk["high"], mk["low"], mk["price"]
        if verbose:
            print(f"   [{sym}] מחיר ${current_price:,.2f} | נר low ${candle_low:,.2f}, high ${candle_high:,.2f}")
        status = check_rec_status(rec, current_price, candle_high, candle_low)
        if not status["closed"]:
            if verbose:
                print(f"   ⏳ {rec['id']} עדיין פתוחה ({status['elapsed_min']:.0f} דק' חלפו)")
            still_open.append(rec)
            continue

        # סוגרים
        if verbose:
            print(f"   🎯 {rec['id']} - {status['outcome']} @ ${status['exit_price']:,.2f} "
                  f"({status['pnl_pct']:+.2f}%)")
        result = close_recommendation(rec, status, verbose=verbose)
        closed_events.append({
            "rec": rec,
            "exit": status,
            "lesson": result["new_lesson"],
            "balance_after": result["account_update"]["account"]["current_balance"],
        })

    save_open_recs(still_open)
    return closed_events


if __name__ == "__main__":
    events = poll_open_recommendations(verbose=True)
    if events:
        print(f"\n=== {len(events)} עסקאות נסגרו ===")
        for e in events:
            print(f"  {e['rec']['direction']} {e['rec']['setup_type']}: "
                  f"{e['exit']['outcome']} {e['exit']['pnl_pct']:+.2f}%")
    else:
        print("\nשום עסקה לא הסתיימה בסבב הזה.")
