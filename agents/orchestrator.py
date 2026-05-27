"""
האורקסטרטור - מריץ את הצוות הרב-סוכני על מצב שוק נתון.
זרימה:
1. שלושה סוכנים רצים במקביל: טכני, סיכון, הקשר
2. ראש הצוות מקבל את שלושת הפלטים ומחליט
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List, Optional

from .llm import call_claude
from . import prompts


def run_hunter(market_summary: dict, recent_candles_summary: list,
               lessons: Optional[List[Dict]] = None, verbose: bool = True) -> Dict[str, Any]:
    """
    מריץ את הצייד - מאתר setups בדאטה הנוכחי.
    """
    user_msg = f"""מצב השוק הנוכחי:
```json
{json.dumps(market_summary, indent=2, ensure_ascii=False)}
```

רצף של 30-50 הנרות האחרונים (בסדר כרונולוגי):
```json
{json.dumps(recent_candles_summary, indent=2, ensure_ascii=False)}
```
"""
    if lessons:
        user_msg += f"\n\nלקחים שנלמדו מעסקאות קודמות (להתחשב):\n```json\n{json.dumps(lessons, indent=2, ensure_ascii=False)}\n```"

    user_msg += "\n\nסרוק ואתר setups. החזר JSON לפי הפורמט שלך."

    start = time.time()
    response = call_claude(user_msg, prompts.HUNTER, model="haiku", timeout=180)
    elapsed = time.time() - start

    if verbose:
        status = "✗" if response.is_error else "✓"
        setups_count = len((response.parsed or {}).get("setups", [])) if response.parsed else 0
        print(f"  {status} הצייד: {elapsed:.1f}s, ${response.cost_usd:.4f}, מצא {setups_count} setups")

    return {
        "role": "הצייד",
        "elapsed_sec": round(elapsed, 1),
        "cost_usd": response.cost_usd,
        "is_error": response.is_error,
        "error": response.error_message,
        "raw": response.raw_result,
        "parsed": response.parsed,
    }


def run_advisor(role_name: str, system_prompt: str, market_summary: dict,
                extra_context: dict = None, model: str = "sonnet") -> Dict[str, Any]:
    """
    מריץ סוכן אחד עם תקציר השוק כקלט.
    """
    user_msg = f"""מצב השוק הנוכחי:
```json
{json.dumps(market_summary, indent=2, ensure_ascii=False)}
```"""

    if extra_context:
        user_msg += f"\n\nמידע נוסף:\n```json\n{json.dumps(extra_context, indent=2, ensure_ascii=False)}\n```"

    user_msg += "\n\nהחזר JSON בלבד לפי הפורמט שהוגדר בהוראות שלך."

    start = time.time()
    response = call_claude(user_msg, system_prompt, model=model, timeout=150)
    elapsed = time.time() - start

    return {
        "role": role_name,
        "elapsed_sec": round(elapsed, 1),
        "cost_usd": response.cost_usd,
        "is_error": response.is_error,
        "error": response.error_message,
        "raw": response.raw_result,
        "parsed": response.parsed,
    }


def run_committee(market_summary: dict, setup: Optional[dict] = None,
                  lessons: Optional[List[Dict]] = None,
                  history: Optional[List[Dict]] = None,
                  verbose: bool = True,
                  training_mode: bool = False,
                  account_balance_usd: Optional[float] = None) -> Dict[str, Any]:
    """
    מריץ את הצוות המייעץ + ראש הצוות.
    אם הגיע setup מהצייד - הוא נשלח לכל המומחים כקלט נוסף.
    אם הגיעו לקחים - גם הם נמסרים לוועדה.
    אם הגיעה היסטוריית עסקאות - החוקר ההיסטורי רץ במקביל.
    אם הועבר account_balance_usd - מנהל הסיכונים יחשב גודל פוזיציה ממנו במקום מ-$10K.
    """
    # תופסים את היתרה האמיתית - אם לא הועברה, נמשוך מהזיכרון.
    # ככה Risk Manager לא יקצה פוזיציות עצומות על חשבון של $500.
    if account_balance_usd is None:
        try:
            from memory_store import load_account
            account_balance_usd = float(load_account().get("current_balance", 1000) or 1000)
        except Exception:
            account_balance_usd = 1000.0

    extra: Optional[Dict] = {}
    if setup:
        extra["setup_from_hunter"] = setup
    if lessons:
        extra["lessons_from_past_trades"] = lessons
    extra["account_balance_usd"] = account_balance_usd
    extra["risk_per_trade_pct"] = 5.0  # פייפר אגרסיבי

    advisors = [
        ("המנתח הטכני", prompts.TECHNICAL_ANALYST, "sonnet"),
        ("מנהל הסיכונים", prompts.RISK_MANAGER, "sonnet"),
        ("קורא ההקשר", prompts.CONTEXT_READER, "sonnet"),
    ]
    run_researcher = bool(setup and history)
    workers_count = len(advisors) + (1 if run_researcher else 0)

    if verbose:
        print(f"\n[Committee] מפעיל {workers_count} סוכנים במקביל...")

    advisor_results: Dict[str, Dict] = {}
    overall_start = time.time()

    with ThreadPoolExecutor(max_workers=workers_count) as pool:
        futures = {
            pool.submit(run_advisor, name, sp, market_summary, extra, model): name
            for name, sp, model in advisors
        }
        if run_researcher:
            futures[pool.submit(run_historical_researcher, setup, history, False)] = "החוקר ההיסטורי"

        for fut in as_completed(futures):
            name = futures[fut]
            result = fut.result()
            advisor_results[name] = result
            if verbose:
                status = "✗" if result["is_error"] else "✓"
                print(f"  {status} {name}: {result['elapsed_sec']}s, ${result['cost_usd']:.4f}")

    advisor_elapsed = time.time() - overall_start

    if verbose:
        print(f"\n[Committee] מפעיל ראש צוות...")

    head_input = {
        "setup_from_hunter": setup,
        "market": market_summary,
        "advisors": {
            name: result["parsed"] or {"error": result["error"]}
            for name, result in advisor_results.items()
        },
    }
    if lessons:
        head_input["lessons_from_past_trades"] = lessons

    training_hint = ""
    if training_mode:
        training_hint = """

🎓 **מצב אימון לילי פעיל - הוראות נוספות:**
אנחנו במצב למידה אגרסיבי. המטרה: לאסוף יותר עסקאות סגורות שמהן ה-coach ייצור לקחים.
- תהיה יותר נדיב: setup עם 1-2 yellow lights אבל המתמטיקה תקינה → אישור
- "להמתין" של קורא ההקשר ≠ וטו אוטומטי; שקול אותו כאזהרה רגילה
- חוקר היסטורי עם מדגם < 8 → התעלם מהווטו שלו
- חמש אורות אדומים שונים = דחייה (לא שלושה)

**אבל ה-fees הם קדושים:**
- T1 גרוס < 0.4% מהכניסה = דחייה תמיד (גם באימון; טרייד מתמטית מפסיד)
- וטו של מנהל הסיכונים על RR_net < 1.3 = דחייה תמיד

המטרה: יותר עסקאות שילמדו אותנו, לא יותר עסקאות שיפסידו לנו כסף."""

    head_user_msg = f"""שמעת את שלושת המומחים. הנה הסיכום:
```json
{json.dumps(head_input, indent=2, ensure_ascii=False)}
```

קבל החלטה והחזר JSON לפי הפורמט שלך.{training_hint}"""

    head_start = time.time()
    head_response = call_claude(head_user_msg, prompts.HEAD_TRADER, model="sonnet")
    head_elapsed = time.time() - head_start

    # ─── ולידציה קריטית: הוועדה אסור שתחזיר החלטה עם מספרים שבורים ───
    # באג ידוע: אם ראש הצוות החליט שT1 פסול עמלות, הוא לעיתים שם 0 במקום מחיר תקין.
    # במורד הזרם המוניטור רואה target_1=0 ומפענח כ"היעד התממש" → רישום -100% הפסד.
    # פה אנחנו תופסים את זה ומבטלים את העסקה (אין כניסה) במקום לאשר שטויות.
    if head_response.parsed and not head_response.is_error:
        decision = head_response.parsed
        action = decision.get("החלטה")
        if action in ("LONG", "SHORT"):
            entry = decision.get("כניסה") or 0
            stop = decision.get("סטופ") or 0
            t1 = decision.get("יעד_1") or 0
            t2 = decision.get("יעד_2") or 0

            # סף עמלות קשיח: T1 חייב להיות לפחות 0.4% מהכניסה כדי לכסות עמלות (0.2%) + רווח אמיתי
            MIN_T1_DISTANCE_PCT = 0.4

            def _passes_fee_check(target_price: float, entry_price: float) -> bool:
                """True רק אם המרחק מהכניסה ≥ 0.4%"""
                if entry_price <= 0 or target_price <= 0:
                    return False
                return abs(target_price - entry_price) / entry_price * 100 >= MIN_T1_DISTANCE_PCT

            invalid_reason = None
            if entry <= 0:
                invalid_reason = f"כניסה לא תקינה ({entry})"
            elif stop <= 0:
                invalid_reason = f"סטופ לא תקין ({stop})"
            elif t1 <= 0:
                # אם T1 פסול אבל T2 תקין - מאמצים את T2 כיעד היחיד, אך רק אם T2 עובר את חוק העמלות
                if t2 > 0:
                    side_ok = (action == "LONG" and t2 > entry) or (action == "SHORT" and t2 < entry)
                    fee_ok = _passes_fee_check(t2, entry)
                    if side_ok and fee_ok:
                        decision["יעד_1"] = t2
                        decision["יעד_2"] = None
                        decision["סיבה_להחלטה"] = "[VALIDATOR: T1=0 הוחלף ב-T2 (עבר חוק עמלות)] " + str(decision.get("סיבה_להחלטה", ""))[:280]
                    elif not side_ok:
                        invalid_reason = f"גם T2 לא תקין (action={action}, entry={entry}, T2={t2})"
                    else:  # side_ok=True אבל fee_ok=False
                        t2_dist_pct = abs(t2 - entry) / entry * 100
                        invalid_reason = f"T2 לא עובר עמלות: מרחק {t2_dist_pct:.2f}% < {MIN_T1_DISTANCE_PCT}% מינימום"
                else:
                    invalid_reason = f"T1={t1} ו-T2={t2} - אין יעד תקין"
            elif action == "LONG" and t1 <= entry:
                invalid_reason = f"LONG אבל T1 ({t1}) ≤ כניסה ({entry})"
            elif action == "SHORT" and t1 >= entry:
                invalid_reason = f"SHORT אבל T1 ({t1}) ≥ כניסה ({entry})"
            elif action == "LONG" and stop >= entry:
                invalid_reason = f"LONG אבל סטופ ({stop}) ≥ כניסה ({entry})"
            elif action == "SHORT" and stop <= entry:
                invalid_reason = f"SHORT אבל סטופ ({stop}) ≤ כניסה ({entry})"
            elif not _passes_fee_check(t1, entry):
                # חגורת בטחון: אפילו אם T1 בכיוון הנכון, חייב לעבור 0.4% עמלות
                t1_dist_pct = abs(t1 - entry) / entry * 100
                invalid_reason = f"T1 לא עובר עמלות: מרחק {t1_dist_pct:.2f}% < {MIN_T1_DISTANCE_PCT}% מינימום"

            if invalid_reason:
                if verbose:
                    print(f"  🚨 VALIDATOR: דחיית החלטה פגומה — {invalid_reason}")
                # מבטלים - לא לוקחים עסקה עם מספרים שבורים
                head_response.parsed = {
                    "החלטה": "אין כניסה",
                    "סיבה_להחלטה": f"VALIDATOR REJECT: {invalid_reason}. החלטה מקורית: {str(decision)[:300]}",
                    "כניסה": 0, "סטופ": 0, "יעד_1": 0, "יעד_2": 0,
                    "גודל_פוזיציה_USD": 0, "ביטחון_1_10": 0,
                    "_validator_rejected": True,
                    "_validator_reason": invalid_reason,
                }

    if verbose:
        status = "✗" if head_response.is_error else "✓"
        print(f"  {status} ראש הצוות: {head_elapsed:.1f}s, ${head_response.cost_usd:.4f}")

    total_cost = sum(r["cost_usd"] for r in advisor_results.values()) + head_response.cost_usd
    total_elapsed = advisor_elapsed + head_elapsed

    return {
        "timestamp": market_summary.get("timestamp"),
        "market_summary": market_summary,
        "setup": setup,
        "advisors": advisor_results,
        "head_decision": {
            "parsed": head_response.parsed,
            "raw": head_response.raw_result,
            "is_error": head_response.is_error,
            "error": head_response.error_message,
            "elapsed_sec": round(head_elapsed, 1),
            "cost_usd": head_response.cost_usd,
        },
        "totals": {
            "elapsed_sec": round(total_elapsed, 1),
            "cost_usd": round(total_cost, 4),
        },
    }


def run_historical_researcher(setup: dict, history: List[Dict],
                              verbose: bool = True) -> Dict[str, Any]:
    """
    החוקר ההיסטורי - מחפש setups דומים בעבר ומדווח על Win Rate נטו ו-EV.
    """
    # נקטין את ההיסטוריה לשדות הרלוונטיים בלבד כדי לחסוך טוקנים
    compact = []
    for t in history[-60:]:  # 60 אחרונים מקסימום
        hs = t.get("hunter_setup") or {}
        sim = t.get("simulation") or t.get("shadow_simulation") or {}
        rejected = t.get("status") == "shadow_rejected"
        compact.append({
            "setup_type": hs.get("סוג"),
            "direction": hs.get("כיוון"),
            "score": hs.get("ציון_איכות"),
            "executed": not rejected,
            "outcome": sim.get("outcome"),
            "net_pnl_pct": sim.get("pnl_pct"),
            "gross_pnl_pct": sim.get("gross_pnl_pct"),
        })

    user_msg = f"""ה-setup הנוכחי שמוצע:
```json
{json.dumps(setup, indent=2, ensure_ascii=False)}
```

ההיסטוריה שלנו ({len(compact)} עסקאות) - closed וגם shadow rejected:
```json
{json.dumps(compact, indent=2, ensure_ascii=False)}
```

סנן דומים, חשב סטטיסטיקה, החזר JSON לפי הפורמט שלך."""

    start = time.time()
    response = call_claude(user_msg, prompts.HISTORICAL_RESEARCHER, model="sonnet", timeout=150)
    elapsed = time.time() - start

    if verbose:
        status = "✗" if response.is_error else "✓"
        found = (response.parsed or {}).get("setups_דומים_שנמצאו", "?")
        verdict = (response.parsed or {}).get("מסקנה", "?")
        print(f"  {status} החוקר ההיסטורי: {elapsed:.1f}s, ${response.cost_usd:.4f} → {found} דומים, {verdict}")

    return {
        "role": "החוקר ההיסטורי",
        "elapsed_sec": round(elapsed, 1),
        "cost_usd": response.cost_usd,
        "is_error": response.is_error,
        "error": response.error_message,
        "raw": response.raw_result,
        "parsed": response.parsed,
    }


def run_devil_advocate(setup: dict, head_decision: dict, lessons: List[Dict],
                       market_summary: dict, verbose: bool = True) -> Dict[str, Any]:
    """
    מריץ את פרקליט השטן - מערער על החלטות "אין כניסה" שמסתמכות על לקחים.
    """
    user_msg = f"""ראש הצוות החליט לדחות את ה-setup. הנה כל המידע:

setup שהצייד הציע:
```json
{json.dumps(setup, indent=2, ensure_ascii=False)}
```

החלטת ראש הצוות:
```json
{json.dumps(head_decision, indent=2, ensure_ascii=False)}
```

מצב השוק:
```json
{json.dumps(market_summary, indent=2, ensure_ascii=False)}
```

לקחים זמינים בזיכרון (עם confidence):
```json
{json.dumps(lessons, indent=2, ensure_ascii=False)}
```

תקוף את ההחלטה אם יש לך סיבה אמיתית. אם אין - תאשר את הדחייה. החזר JSON."""

    start = time.time()
    response = call_claude(user_msg, prompts.DEVIL_ADVOCATE, model="sonnet", timeout=150)
    elapsed = time.time() - start

    if verbose:
        status = "✗" if response.is_error else "✓"
        verdict = "לא רלוונטי"
        if response.parsed:
            verdict = "מערער!" if response.parsed.get("תקיפה_מוצלחת") else "מאשר דחייה"
        print(f"  {status} פרקליט השטן: {elapsed:.1f}s, ${response.cost_usd:.4f} → {verdict}")

    return {
        "role": "פרקליט השטן",
        "elapsed_sec": round(elapsed, 1),
        "cost_usd": response.cost_usd,
        "is_error": response.is_error,
        "error": response.error_message,
        "raw": response.raw_result,
        "parsed": response.parsed,
    }


def run_critique(trade_record: dict, history_stats: dict,
                 verbose: bool = True) -> Dict[str, Any]:
    """
    הוועדה הביקורתית אחרי שעסקה הסתיימה.
    3 סוכנים במקביל: המבקר, המאמן, הסטטיסטיקאי.

    trade_record: עסקה מלאה (setup + committee_decision + simulation)
    history_stats: סטטיסטיקה מצטברת לפני העסקה הזו
    """
    review_agents = [
        ("המבקר", prompts.CRITIC, "sonnet"),
        ("המאמן", prompts.COACH, "sonnet"),
        ("הסטטיסטיקאי", prompts.STATISTICIAN, "haiku"),
    ]

    user_msg = f"""העסקה שהסתיימה (כולל ניתוח לפני, החלטה, ותוצאה):
```json
{json.dumps(trade_record, indent=2, ensure_ascii=False, default=str)}
```

סטטיסטיקה היסטורית עד לפני העסקה הזו:
```json
{json.dumps(history_stats, indent=2, ensure_ascii=False)}
```

תפעל לפי ההוראות שלך והחזר JSON בלבד."""

    if verbose:
        print(f"\n[Critique] מפעיל ועדה ביקורתית - 3 סוכנים במקביל...")

    results: Dict[str, Dict] = {}
    overall_start = time.time()

    def _run_one(name: str, sp: str, mdl: str):
        s = time.time()
        resp = call_claude(user_msg, sp, model=mdl, timeout=180)
        return name, {
            "role": name,
            "model": mdl,
            "elapsed_sec": round(time.time() - s, 1),
            "cost_usd": resp.cost_usd,
            "is_error": resp.is_error,
            "error": resp.error_message,
            "raw": resp.raw_result,
            "parsed": resp.parsed,
        }

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(_run_one, n, sp, m) for n, sp, m in review_agents]
        for fut in as_completed(futures):
            name, result = fut.result()
            results[name] = result
            if verbose:
                status = "✗" if result["is_error"] else "✓"
                print(f"  {status} {name} ({result['model']}): {result['elapsed_sec']}s, ${result['cost_usd']:.4f}")

    total_elapsed = time.time() - overall_start
    total_cost = sum(r["cost_usd"] for r in results.values())

    return {
        "reviewers": results,
        "totals": {
            "elapsed_sec": round(total_elapsed, 1),
            "cost_usd": round(total_cost, 4),
        },
    }


def run_tuner(trades: list, lessons: list, config_snapshot: dict, verbose: bool = True) -> Dict[str, Any]:
    """
    המכוון - סוכן מטא שמסתכל על המערכת כולה ומציע שיפורים.
    מקבל את כל הנתונים, מחזיר המלצות מסודרות לפי עדיפות.
    """
    # תקצור הנתונים כדי לחסוך טוקנים - לא צריך את כל הפרטים של כל עסקה
    closed = [t for t in trades if t.get("status") == "closed"]
    shadow = [t for t in trades if t.get("status") == "shadow_rejected"]

    closed_summary = [{
        "id": t.get("id"),
        "type": (t.get("hunter_setup") or {}).get("סוג"),
        "direction": (t.get("decision") or {}).get("החלטה"),
        "outcome": (t.get("simulation") or {}).get("outcome"),
        "pnl_pct": (t.get("simulation") or {}).get("pnl_pct"),
    } for t in closed[-15:]]   # קוצץ ל-15 עסקאות אחרונות

    shadow_summary = [{
        "id": t.get("id"),
        "pnl": t.get("would_have_pnl_pct") or t.get("shadow_pnl_pct"),
        "right": t.get("rejection_was_right") or t.get("committee_was_right"),
    } for t in shadow[-10:]]   # קוצץ ל-10 shadow

    # קודם לקחים עם confidence גבוה, ואז חדשים - מקסימום 15
    lessons_sorted = sorted(lessons, key=lambda l: -(l.get("confidence") or 0))[:15]
    lessons_summary = [{
        "id": l.get("id"),
        "rule": (l.get("rule", "") or "")[:120],   # קוצץ ל-120 תווים
        "conf": l.get("confidence", 1),
        "invk": l.get("times_invoked", 0),
        "ok": l.get("times_correct", 0),
        "bad": l.get("times_wrong", 0),
    } for l in lessons_sorted]

    payload = {
        "stats": {
            "closed_trades": len(closed),
            "shadow_rejections": len(shadow),
            "total_lessons": len(lessons),
            "wins": sum(1 for t in closed if (t.get("simulation") or {}).get("pnl_pct", 0) > 0),
            "shadow_committee_right": sum(1 for t in shadow if t.get("rejection_was_right") or t.get("committee_was_right")),
        },
        "config": config_snapshot,
        "closed_trades": closed_summary,
        "shadow_rejections": shadow_summary,
        "lessons": lessons_summary,
    }

    user_msg = f"""כל הנתונים על המערכת:

```json
{json.dumps(payload, indent=2, ensure_ascii=False)}
```

תנתח, מצא דפוסים, ותציע המלצות שיפור לפי הסדר שלך. החזר JSON."""

    start = time.time()
    response = call_claude(user_msg, prompts.TUNER, model="haiku", timeout=300)
    elapsed = time.time() - start

    if verbose:
        status = "✗" if response.is_error else "✓"
        rec_count = len((response.parsed or {}).get("המלצות", []))
        print(f"  {status} המכוון: {elapsed:.1f}s, ${response.cost_usd:.4f}, {rec_count} המלצות")

    return {
        "role": "המכוון",
        "elapsed_sec": round(elapsed, 1),
        "cost_usd": response.cost_usd,
        "is_error": response.is_error,
        "error": response.error_message,
        "raw": response.raw_result,
        "parsed": response.parsed,
    }


def run_judge(recommendation: dict, supporting_data: dict, verbose: bool = True) -> Dict[str, Any]:
    """
    השופט - מבצע ביקורת חיצונית על המלצה אחת מהמכוון.
    לא תלוי בוועדה - רואה רק נתונים סטטיסטיים.
    """
    user_msg = f"""המלצה לבדיקה:
```json
{json.dumps(recommendation, indent=2, ensure_ascii=False)}
```

נתונים תומכים (סטטיסטיקה רלוונטית):
```json
{json.dumps(supporting_data, indent=2, ensure_ascii=False)}
```

האם הנתונים תומכים בהמלצה? החזר JSON לפי הפורמט שלך."""

    start = time.time()
    response = call_claude(user_msg, prompts.JUDGE, model="haiku", timeout=90)
    elapsed = time.time() - start

    if verbose:
        status = "✗" if response.is_error else "✓"
        verdict = "?"
        if response.parsed:
            verdict = response.parsed.get("החלטה", "?")
        print(f"  {status} השופט: {elapsed:.1f}s, ${response.cost_usd:.4f} → {verdict}")

    return {
        "role": "השופט",
        "elapsed_sec": round(elapsed, 1),
        "cost_usd": response.cost_usd,
        "is_error": response.is_error,
        "error": response.error_message,
        "raw": response.raw_result,
        "parsed": response.parsed,
    }
