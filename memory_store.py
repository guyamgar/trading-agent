"""
שכבת זיכרון - שומרת ושולפת עסקאות, לקחים וסטטיסטיקות מקבצי JSON.
"""
import json
import uuid
from datetime import datetime, timedelta
from typing import List, Dict, Optional

from config import MEMORY_DIR, TRADES_FILE, LESSONS_FILE, ACCOUNT_FILE, RISK_PER_TRADE_PCT


def _ensure_files():
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    if not TRADES_FILE.exists():
        TRADES_FILE.write_text(json.dumps({"trades": []}, ensure_ascii=False, indent=2))
    if not LESSONS_FILE.exists():
        LESSONS_FILE.write_text(json.dumps({"lessons": []}, ensure_ascii=False, indent=2))


def load_trades() -> List[Dict]:
    _ensure_files()
    data = json.loads(TRADES_FILE.read_text())
    if isinstance(data, list):
        return data
    return data.get("trades", [])


def load_lessons() -> List[Dict]:
    _ensure_files()
    data = json.loads(LESSONS_FILE.read_text())
    if isinstance(data, list):
        return data
    return data.get("lessons", [])


def save_trade(trade: Dict) -> str:
    """
    שומר עסקה (חדשה או מעודכנת). מחזיר את ה-id.
    """
    _ensure_files()
    trades = load_trades()

    if "id" not in trade:
        trade["id"] = str(uuid.uuid4())[:8]
        trade["created_at"] = datetime.utcnow().isoformat()
    else:
        # עדכון עסקה קיימת
        trades = [t for t in trades if t["id"] != trade["id"]]

    trade["updated_at"] = datetime.utcnow().isoformat()
    trades.append(trade)

    TRADES_FILE.write_text(json.dumps({"trades": trades}, ensure_ascii=False, indent=2))
    return trade["id"]


def get_trade(trade_id: str) -> Optional[Dict]:
    for t in load_trades():
        if t.get("id") == trade_id:
            return t
    return None


def save_lesson(lesson: Dict) -> str:
    """
    שומר לקח חדש (שהפיק המאמן).
    מוסיף שדות סטטיסטיקה: confidence, times_invoked, times_correct, times_wrong.
    """
    _ensure_files()
    lessons = load_lessons()

    lesson["id"] = str(uuid.uuid4())[:8]
    lesson["created_at"] = datetime.utcnow().isoformat()
    lesson["confidence"] = lesson.get("confidence", 1)
    lesson["times_invoked"] = lesson.get("times_invoked", 0)
    lesson["times_correct"] = lesson.get("times_correct", 0)
    lesson["times_wrong"] = lesson.get("times_wrong", 0)
    lesson["overrides_attempted"] = lesson.get("overrides_attempted", 0)
    lesson["overrides_successful"] = lesson.get("overrides_successful", 0)
    lessons.append(lesson)

    LESSONS_FILE.write_text(json.dumps({"lessons": lessons}, ensure_ascii=False, indent=2))
    return lesson["id"]


def delete_lesson(lesson_id: str) -> bool:
    """מוחק לקח מהזיכרון. מחזיר True אם נמחק."""
    lessons = load_lessons()
    before = len(lessons)
    lessons = [l for l in lessons if l.get("id") != lesson_id]
    LESSONS_FILE.write_text(json.dumps({"lessons": lessons}, ensure_ascii=False, indent=2))
    return len(lessons) < before


def adjust_lesson_confidence(lesson_id: str, delta: int) -> Optional[Dict]:
    """משנה confidence של לקח בערך delta (+ או -). מחזיר את הלקח המעודכן."""
    lessons = load_lessons()
    found = None
    for l in lessons:
        if l.get("id") == lesson_id:
            l["confidence"] = max(-10, l.get("confidence", 1) + delta)
            l["updated_at"] = datetime.utcnow().isoformat()
            found = l
            break
    if found:
        LESSONS_FILE.write_text(json.dumps({"lessons": lessons}, ensure_ascii=False, indent=2))
    return found


def update_lesson_stats(lesson_id: str, *, invoked: bool = False,
                        correct: bool = False, wrong: bool = False,
                        override_attempt: bool = False, override_success: bool = False) -> None:
    """
    מעדכן סטטיסטיקות של לקח קיים אחרי קונטר-פקטואל / overrride.
    confidence: +1 על correct, -2 על wrong (ענישה כפולה לטעויות).
    """
    lessons = load_lessons()
    for l in lessons:
        if l.get("id") != lesson_id:
            continue
        if invoked:
            l["times_invoked"] = l.get("times_invoked", 0) + 1
        if correct:
            l["times_correct"] = l.get("times_correct", 0) + 1
            l["confidence"] = l.get("confidence", 1) + 1
        if wrong:
            l["times_wrong"] = l.get("times_wrong", 0) + 1
            l["confidence"] = max(0, l.get("confidence", 1) - 2)
        if override_attempt:
            l["overrides_attempted"] = l.get("overrides_attempted", 0) + 1
        if override_success:
            l["overrides_successful"] = l.get("overrides_successful", 0) + 1
        l["updated_at"] = datetime.utcnow().isoformat()
        break

    LESSONS_FILE.write_text(json.dumps({"lessons": lessons}, ensure_ascii=False, indent=2))


def relevant_lessons(market_summary: Dict, limit: int = 10, min_confidence: int = 0) -> List[Dict]:
    """
    מחזיר לקחים. כברירת מחדל - ה-N האחרונים. אפשר לסנן לפי confidence מינימלי.
    """
    lessons = load_lessons()
    if min_confidence > 0:
        lessons = [l for l in lessons if l.get("confidence", 1) >= min_confidence]
    return lessons[-limit:]


COST_LOG_FILE = MEMORY_DIR / "cost_log.json"


def log_llm_cost(amount_usd: float) -> None:
    """מתעד עלות של קריאת LLM ליום הנוכחי."""
    if amount_usd <= 0:
        return
    today = datetime.utcnow().strftime("%Y-%m-%d")
    data = {}
    if COST_LOG_FILE.exists():
        try:
            data = json.loads(COST_LOG_FILE.read_text())
        except Exception:
            data = {}
    daily = data.get("daily", {})
    daily[today] = round(daily.get(today, 0) + amount_usd, 4)
    data["daily"] = daily
    data["total"] = round(data.get("total", 0) + amount_usd, 4)
    COST_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    COST_LOG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def get_cost_summary() -> Dict:
    """מחזיר סיכום עלויות - היום, אתמול, חודש."""
    if not COST_LOG_FILE.exists():
        return {"today": 0, "yesterday": 0, "this_month": 0, "total": 0}
    try:
        data = json.loads(COST_LOG_FILE.read_text())
    except Exception:
        return {"today": 0, "yesterday": 0, "this_month": 0, "total": 0}
    daily = data.get("daily", {})
    today = datetime.utcnow().strftime("%Y-%m-%d")
    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    month_prefix = today[:7]
    this_month = sum(v for k, v in daily.items() if k.startswith(month_prefix))
    return {
        "today": daily.get(today, 0),
        "yesterday": daily.get(yesterday, 0),
        "this_month": round(this_month, 2),
        "total": data.get("total", 0),
    }


def get_stats() -> Dict:
    """
    מסכם סטטיסטיקות בסיסיות מההיסטוריה.
    """
    trades = load_trades()
    closed = [t for t in trades if t.get("status") == "closed" and "pnl_usd" in t]

    if not closed:
        return {
            "total_trades": len(trades),
            "closed_trades": 0,
            "open_trades": len([t for t in trades if t.get("status") == "open"]),
            "win_rate_pct": 0,
            "total_pnl_usd": 0,
            "profit_factor": 0,
            "ready_for_live": False,
        }

    wins = [t for t in closed if t["pnl_usd"] > 0]
    losses = [t for t in closed if t["pnl_usd"] <= 0]

    gross_profit = sum(t["pnl_usd"] for t in wins)
    gross_loss = abs(sum(t["pnl_usd"] for t in losses))

    win_rate = len(wins) / len(closed) * 100 if closed else 0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    total_pnl = gross_profit - gross_loss
    avg_win = gross_profit / len(wins) if wins else 0
    avg_loss = gross_loss / len(losses) if losses else 0

    return {
        "total_trades": len(trades),
        "closed_trades": len(closed),
        "open_trades": len([t for t in trades if t.get("status") == "open"]),
        "win_rate_pct": round(win_rate, 1),
        "avg_win_usd": round(avg_win, 2),
        "avg_loss_usd": round(avg_loss, 2),
        "total_pnl_usd": round(total_pnl, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else None,
        "ready_for_live": (
            len(closed) >= 50
            and profit_factor >= 1.5
            and total_pnl > 0
        ),
    }


# ─── ניהול חשבון פייפר/לייב ─────────────────────────────────────

def load_account() -> Dict:
    """טוען את מצב החשבון. יוצר ברירת מחדל אם לא קיים."""
    if not ACCOUNT_FILE.exists():
        default = {
            "mode": "paper",
            "stage": 1,
            "stage_description": "פייפר טרייד - להגדיל $1,000 ל-$10,000",
            "starting_balance": 1000.0,
            "current_balance": 1000.0,
            "target_balance": 10000.0,
            "highest_balance": 1000.0,
            "lowest_balance": 1000.0,
            "trades_taken": 0,
            "wins": 0,
            "losses": 0,
            "stage_started_at": datetime.utcnow().isoformat(),
            "milestones_hit": [],
            "stage_history": [],
        }
        ACCOUNT_FILE.write_text(json.dumps(default, ensure_ascii=False, indent=2))
        return default
    return json.loads(ACCOUNT_FILE.read_text())


def save_account(account: Dict) -> None:
    ACCOUNT_FILE.write_text(json.dumps(account, ensure_ascii=False, indent=2))


def compute_position_size_usd(balance: float, risk_pct: float = None) -> float:
    """גודל הסיכון לעסקה - אחוז מהיתרה הנוכחית."""
    pct = risk_pct if risk_pct is not None else RISK_PER_TRADE_PCT
    return round(balance * (pct / 100), 2)


def update_account_after_trade(pnl_pct: float, won: bool) -> Dict:
    """
    מעדכן את החשבון אחרי עסקה.
    הסיכון לעסקה = RISK_PER_TRADE_PCT% מהיתרה.
    הרווח/הפסד הוא יחס למה שסיכנו: pnl_usd = risk × (pnl_pct / stop_distance_pct).

    כדי להפשט - אנחנו עובדים על הנחה שהסטופ נמצא במרחק כזה
    שהפסד = -1% מהיתרה (וטרגט נותן +X% לפי RR).
    כך +0.5% במעבר הוא +0.5% × 1% = +0.5% מהיתרה.

    כלומר: balance_change_pct ≈ pnl_pct (פעולה של מהלך המחיר).

    ליציבות, פשוט: balance × (1 + pnl_pct/100) על הסיכון הקבוע.
    """
    acc = load_account()
    risk_amount = compute_position_size_usd(acc["current_balance"])
    # pnl_pct מייצג את אחוז התנועה - על pos של risk_amount, הרווח/הפסד הם:
    pnl_usd = round(risk_amount * (pnl_pct / 100) * 10, 2)  # × 10 כי הפוזיציה כ-10x הסיכון

    acc["current_balance"] = round(acc["current_balance"] + pnl_usd, 2)
    acc["trades_taken"] += 1
    if won:
        acc["wins"] += 1
    else:
        acc["losses"] += 1
    acc["highest_balance"] = max(acc["highest_balance"], acc["current_balance"])
    acc["lowest_balance"] = min(acc["lowest_balance"], acc["current_balance"])

    # מעקב אבני דרך
    progress_pct = ((acc["current_balance"] - acc["starting_balance"]) /
                    (acc["target_balance"] - acc["starting_balance"]) * 100)
    for milestone in [25, 50, 75, 100]:
        if progress_pct >= milestone and milestone not in acc.get("milestones_hit", []):
            acc.setdefault("milestones_hit", []).append(milestone)

    save_account(acc)
    return {
        "account": acc,
        "pnl_usd": pnl_usd,
        "risk_amount": risk_amount,
        "progress_pct": round(progress_pct, 2),
        "reached_target": acc["current_balance"] >= acc["target_balance"],
    }


def check_advance_readiness() -> Dict:
    """
    בודק אם המערכת מוכנה לעבור לשלב הבא.
    קריטריונים לפי שלב נוכחי:
    - שלב 1 → 2:  30+ עסקאות, PF ≥ 1.5, WR ≥ 50%, DD < 15%
    - שלב 2 → 3:  קריטריונים מקסימליים (כסף אמיתי - דורש ביטחון מלא)
                  50+ עסקאות בשלב 2 (בלייב!), PF ≥ 1.8, WR ≥ 55%, DD < 10%
                  + ניסיון של לפחות 7 ימים בשלב 2
    """
    stats = get_stats()
    acc = load_account()
    closed = stats.get("closed_trades", 0)
    pf = stats.get("profit_factor")
    if pf is None:
        pf = 0
    win_rate = stats.get("win_rate_pct", 0)

    if acc["highest_balance"] > 0:
        drawdown_pct = (acc["highest_balance"] - acc["current_balance"]) / acc["highest_balance"] * 100
    else:
        drawdown_pct = 0

    current_stage = acc.get("stage", 1)

    if current_stage == 1:
        # שלב 1 → 2: רק להוכיח Edge בסיסי
        criteria = {
            "trades_30plus": {"value": closed, "target": 30, "met": closed >= 30},
            "profit_factor_15plus": {"value": pf, "target": 1.5, "met": pf >= 1.5},
            "win_rate_50plus": {"value": win_rate, "target": 50, "met": win_rate >= 50},
            "drawdown_under_15": {"value": round(drawdown_pct, 1), "target": 15,
                                  "met": drawdown_pct < 15},
        }
    else:
        # שלב 2 → 3: קריטריונים מקסימליים לכסף אמיתי
        # סופרים רק עסקאות שנעשו בלייב (stage 2+)
        live_trades = sum(1 for t in load_trades()
                          if t.get("status") == "closed" and t.get("live_mode"))

        # ימים מהתחלת שלב 2
        try:
            stage_started = datetime.fromisoformat(acc.get("stage_started_at", ""))
            days_in_stage = (datetime.utcnow() - stage_started).days
        except Exception:
            days_in_stage = 0

        criteria = {
            "live_trades_50plus": {"value": live_trades, "target": 50,
                                    "met": live_trades >= 50},
            "profit_factor_18plus": {"value": pf, "target": 1.8, "met": pf >= 1.8},
            "win_rate_55plus": {"value": win_rate, "target": 55, "met": win_rate >= 55},
            "drawdown_under_10": {"value": round(drawdown_pct, 1), "target": 10,
                                  "met": drawdown_pct < 10},
            "days_7plus": {"value": days_in_stage, "target": 7, "met": days_in_stage >= 7},
        }

    all_met = all(c["met"] for c in criteria.values())
    return {
        "ready": all_met,
        "stage": current_stage,
        "criteria": criteria,
        "summary": f"{sum(1 for c in criteria.values() if c['met'])}/{len(criteria)} קריטריונים עומדים",
    }


def advance_to_next_stage(new_target: float = None) -> Dict:
    """מקדם את החשבון לשלב הבא."""
    acc = load_account()
    acc.setdefault("stage_history", []).append({
        "stage": acc["stage"],
        "ended_balance": acc["current_balance"],
        "trades_taken": acc["trades_taken"],
        "ended_at": datetime.utcnow().isoformat(),
    })

    acc["stage"] += 1
    if acc["stage"] == 2:
        acc["mode"] = "paper_live"
        acc["stage_description"] = "פייפר טרייד עם נתוני לייב - להגיע ליעד חדש"
        acc["target_balance"] = new_target or (acc["current_balance"] * 2)
    elif acc["stage"] == 3:
        acc["mode"] = "live"
        acc["stage_description"] = "מסחר אמיתי - כסף אמיתי"
        acc["target_balance"] = new_target or (acc["current_balance"] * 2)

    acc["starting_balance"] = acc["current_balance"]
    acc["highest_balance"] = acc["current_balance"]
    acc["lowest_balance"] = acc["current_balance"]
    acc["trades_taken"] = 0
    acc["wins"] = 0
    acc["losses"] = 0
    acc["stage_started_at"] = datetime.utcnow().isoformat()
    acc["milestones_hit"] = []

    save_account(acc)
    return acc
