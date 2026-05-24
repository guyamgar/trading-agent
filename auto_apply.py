"""
מיישם אוטומטית את המלצות המכוון - כל מה שבטוח עובר אוטומטית.
פרמטרים מסוכנים נשמרים למייל ידני.
"""
import json
from pathlib import Path
from typing import Dict, List, Tuple
from datetime import datetime

from memory_store import (
    save_lesson, delete_lesson, adjust_lesson_confidence,
    load_lessons, load_trades,
)
from agents.orchestrator import run_judge

ROOT = Path(__file__).parent
OVERRIDES_FILE = ROOT / "memory" / "overrides.json"
APPLY_HISTORY = ROOT / "memory" / "apply_history.json"


# פרמטרים שמותר לכוון אוטומטית עם גבולות בטוחים
SAFE_PARAM_BOUNDS = {
    "MIN_HUNTER_QUALITY": (4, 8),
    "MIN_RISK_REWARD": (1.0, 2.5),
    "RISK_PER_TRADE_PCT": (0.5, 2.0),
    "SCAN_STEP_CANDLES": (4, 16),
}


def load_overrides() -> Dict:
    if OVERRIDES_FILE.exists():
        return json.loads(OVERRIDES_FILE.read_text())
    return {}


def save_overrides(overrides: Dict):
    OVERRIDES_FILE.parent.mkdir(parents=True, exist_ok=True)
    OVERRIDES_FILE.write_text(json.dumps(overrides, ensure_ascii=False, indent=2))


def log_apply_action(action: Dict):
    """שומר את ההיסטוריה של הפעולות שהמערכת ביצעה אוטומטית."""
    APPLY_HISTORY.parent.mkdir(parents=True, exist_ok=True)
    history = []
    if APPLY_HISTORY.exists():
        try:
            history = json.loads(APPLY_HISTORY.read_text())
        except Exception:
            history = []
    action["applied_at"] = datetime.utcnow().isoformat()
    history.append(action)
    APPLY_HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=2))


def _judge_review(rec: Dict) -> Dict:
    """מפעיל את השופט על המלצה אחת. מחזיר {'approved': bool, 'reason': str}."""
    # סטטיסטיקה תומכת - תלוי בסוג ההמלצה
    supporting = {}
    typ = rec.get("סוג", "")
    lesson_id = _extract_lesson_id(rec.get("פעולה_מדויקת", ""))

    if typ in ("LESSON_DELETE", "LESSON_DEMOTE", "LESSON_PROMOTE") and lesson_id:
        for l in load_lessons():
            if l.get("id") == lesson_id:
                supporting["lesson"] = {
                    "id": l.get("id"),
                    "rule": (l.get("rule") or "")[:200],
                    "confidence": l.get("confidence"),
                    "invoked": l.get("times_invoked", 0),
                    "correct": l.get("times_correct", 0),
                    "wrong": l.get("times_wrong", 0),
                }
                break

    elif typ == "PARAMETER_TUNE":
        # סטטיסטיקת ביצועים אחרונה
        closed = [t for t in load_trades() if t.get("status") == "closed"][-20:]
        wins = sum(1 for t in closed if (t.get("simulation") or {}).get("pnl_pct", 0) > 0)
        supporting["recent_performance"] = {
            "last_20_trades": len(closed),
            "wins": wins,
            "win_rate_pct": round(wins / max(len(closed), 1) * 100, 1),
            "total_pnl_pct": round(sum((t.get("simulation") or {}).get("pnl_pct", 0) for t in closed), 2),
        }

    result = run_judge(rec, supporting, verbose=False)
    parsed = result.get("parsed") or {}
    return {
        "approved": parsed.get("החלטה") == "אשר",
        "reason": parsed.get("סיבה_מדויקת", "?")[:200],
        "confidence": parsed.get("ביטחון_בהחלטה", 0),
    }


def apply_recommendations(parsed_report: Dict) -> Dict[str, List[str]]:
    """
    מיישם המלצות מדוח המכוון לפי confidence gating:
    - עדיפות 8-10: auto-apply (אין שופט)
    - עדיפות 5-7: השופט מחליט
    - עדיפות <5: דחיה אוטומטית
    מחזיר dict: {applied, rejected, manual}
    """
    applied = []
    rejected = []
    manual = []

    recs = parsed_report.get("המלצות", [])
    for r in recs:
        priority = r.get("עדיפות") or 0

        # סינון לפי priority
        if priority < 5:
            rejected.append(f"🚫 נדחה אוטו (עדיפות {priority}/10 נמוכה): {r.get('כותרת', '')[:80]}")
            continue

        # אם 5-7 → שופט. אם 8+ → ישר ליישום.
        if 5 <= priority <= 7:
            verdict = _judge_review(r)
            if not verdict["approved"]:
                rejected.append(
                    f"🚫 השופט דחה (עדיפות {priority}, ביטחון {verdict['confidence']}): {r.get('כותרת', '')[:60]}\n"
                    f"     למה: {verdict['reason'][:120]}"
                )
                log_apply_action({"type": r.get("סוג"), "judge_rejected": True,
                                  "reason": verdict["reason"], "rec_title": r.get("כותרת")})
                continue

        # אם הגענו לכאן - או priority >= 8 (auto), או השופט אישר
        typ = r.get("סוג", "")
        title = r.get("כותרת", "")[:80]
        action_text = r.get("פעולה_מדויקת", "")
        priority = r.get("עדיפות", 0)

        try:
            if typ == "LESSON_DELETE":
                # מנסה למצוא ID בפעולה
                lesson_id = _extract_lesson_id(action_text)
                if lesson_id and delete_lesson(lesson_id):
                    applied.append(f"🗑 נמחק לקח {lesson_id}: {title}")
                    log_apply_action({"type": typ, "lesson_id": lesson_id, "title": title})
                else:
                    rejected.append(f"❓ LESSON_DELETE - לא נמצא ID: {title}")

            elif typ == "LESSON_DEMOTE":
                lesson_id = _extract_lesson_id(action_text)
                if lesson_id:
                    updated = adjust_lesson_confidence(lesson_id, -2)
                    if updated:
                        applied.append(f"⬇️ הורד confidence של {lesson_id}: {title}")
                        log_apply_action({"type": typ, "lesson_id": lesson_id})

            elif typ == "LESSON_PROMOTE":
                lesson_id = _extract_lesson_id(action_text)
                if lesson_id:
                    updated = adjust_lesson_confidence(lesson_id, +2)
                    if updated:
                        applied.append(f"⬆️ הועלה confidence של {lesson_id}: {title}")
                        log_apply_action({"type": typ, "lesson_id": lesson_id})

            elif typ == "NEW_LESSON":
                rule = action_text or title
                if rule and len(rule) > 20:
                    new_id = save_lesson({
                        "rule": rule[:500],
                        "trigger": r.get("נימוק", "")[:200],
                        "category": "auto_from_tuner",
                        "from_outcome": "tuner_recommendation",
                        "confidence": 2,  # יוצא עם confidence התחלתי טוב
                    })
                    applied.append(f"➕ נוסף לקח חדש {new_id}: {rule[:100]}")
                    log_apply_action({"type": typ, "lesson_id": new_id, "rule": rule[:200]})

            elif typ == "PARAMETER_TUNE":
                # פרמטרים: בודק שזה בטוח, שומר ב-overrides
                applied_param = _try_apply_param(action_text)
                if applied_param:
                    applied.append(f"🔧 פרמטר {applied_param['name']}: {applied_param['old']} → {applied_param['new']}")
                    log_apply_action({"type": typ, **applied_param})
                else:
                    rejected.append(f"⚠️ פרמטר {title} - מחוץ לגבולות בטוחים, לא יושם")

            elif typ in ("AGENT_REFINE", "NEW_AGENT"):
                manual.append(f"🙋 {typ}: {title} (עדיפות {priority}) - דורש אישור ידני")

            else:
                rejected.append(f"❓ סוג לא מוכר {typ}: {title}")

        except Exception as e:
            rejected.append(f"❌ {typ} נכשל: {e}")

    return {"applied": applied, "rejected": rejected, "manual": manual}


def _extract_lesson_id(text: str) -> str:
    """מחפש lesson ID (8 תווים hex) בטקסט."""
    import re
    m = re.search(r"\b[a-f0-9]{8}\b", text)
    return m.group(0) if m else ""


def _try_apply_param(action_text: str) -> Dict:
    """מנסה לפרסר 'PARAM: 5 → 6' או 'PARAM=5->6' ולשמור ב-overrides."""
    import re
    # תבנית: NAME: old → new   או   NAME: old -> new
    m = re.search(r"([A-Z_]+)[:= ]+([-\d.]+)\s*[-→>]+\s*([-\d.]+)", action_text)
    if not m:
        return None

    name = m.group(1)
    try:
        old_val = float(m.group(2))
        new_val = float(m.group(3))
    except ValueError:
        return None

    if name not in SAFE_PARAM_BOUNDS:
        return None

    lo, hi = SAFE_PARAM_BOUNDS[name]
    if not (lo <= new_val <= hi):
        return None

    overrides = load_overrides()
    overrides[name] = new_val
    save_overrides(overrides)

    return {"name": name, "old": old_val, "new": new_val}
