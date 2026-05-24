"""
סשן למידה יומי - 5 עסקאות נייר על דאטה היסטורי, פייפליין מלא.

לכל עסקה:
1. הצייד סורק 50 נרות ומאתר setups
2. אם נמצא setup טוב - הוועדה מנתחת
3. אם ראש הצוות אישר - מסמלץ את העסקה קדימה על הדאטה האמיתי
4. הוועדה הביקורתית מנתחת את התוצאה
5. הלקח נשמר לזיכרון
6. דיווח מלא למשתמש

הרצה:
    cd ~/Desktop/Agents_markering/trading_agent
    python3 scripts/learn_daily.py
"""
import sys
import random
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.binance_client import BinanceClient
from data.indicators import market_summary, candle_window
from agents.orchestrator import run_committee, run_hunter, run_critique, run_devil_advocate
from agents.trade_simulator import simulate_trade
from config import SYMBOL, TIMEFRAME_ANALYSIS
from memory_store import (
    save_trade, save_lesson, relevant_lessons, get_stats,
    update_lesson_stats, update_account_after_trade, load_account,
    load_trades,
)


SESSION_TRADES_TARGET = 1  # סשן מהיר - עסקה אחת + ועדה + לקח
SESSION_MAX_SCANS = 25           # תקרה לסשן - גם בלי 3 כניסות
LOOKBACK_CANDLES = 700           # ~7 ימי 15m
CONTEXT_BEFORE = 250             # נרות לתקציר השוק (כמו ה-live)
SIM_FORWARD_CANDLES = 96         # מקסימום 24 שעות החזקה
MIN_HUNTER_QUALITY = 5           # ציון מינימלי לטיפול בסטאפ


def pick_simulation_anchors(total_candles: int, n: int) -> list:
    """
    בוחר נקודות עיגון לסימולציה - חייב להיות מספיק נרות לפני ואחרי.
    """
    min_idx = CONTEXT_BEFORE + 50
    max_idx = total_candles - SIM_FORWARD_CANDLES - 5
    if max_idx <= min_idx:
        return []

    spacing = (max_idx - min_idx) // n
    anchors = []
    for i in range(n):
        center = min_idx + i * spacing + random.randint(-20, 20)
        center = max(min_idx, min(max_idx, center))
        anchors.append(center)
    return anchors


def run_one_trade(df, anchor_idx: int, trade_num: int, total_trades: int) -> dict:
    """
    מריץ עסקה אחת מלאה מהצייד עד הוועדה הביקורתית.
    מחזיר את האובייקט המלא ששמור בזיכרון.
    """
    print(f"\n{'=' * 70}")
    print(f"עסקה {trade_num}/{total_trades} - נקודת עיגון: נר #{anchor_idx}")
    print(f"{'=' * 70}")

    # סלייס דאטה כך שזה כאילו זה היה ה'נוכחי'
    df_until_now = df.iloc[anchor_idx - CONTEXT_BEFORE: anchor_idx + 1].reset_index(drop=True)
    df_future = df.iloc[anchor_idx + 1: anchor_idx + 1 + SIM_FORWARD_CANDLES].reset_index(drop=True)

    summary = market_summary(df_until_now)
    print(f"זמן בדיקה: {summary['timestamp']}, מחיר: ${summary['price']:,.2f}")
    print(f"RSI: {summary['indicators']['rsi']}, טרנד: {summary['trend']}")

    # 1) צייד
    print(f"\n[1/5] הצייד סורק...")
    candles_view = candle_window(df_until_now, n=25)
    lessons = relevant_lessons(summary, limit=5)
    hunter = run_hunter(summary, candles_view, lessons=lessons, verbose=False)

    if hunter["is_error"] or not hunter["parsed"]:
        print(f"  ✗ הצייד נכשל: {hunter['error']}")
        return _save_skipped(trade_num, anchor_idx, summary, hunter, reason="hunter_failed")

    setups = hunter["parsed"].get("setups", [])
    valid = [s for s in setups if (s.get("ציון_איכות", 0) or 0) >= MIN_HUNTER_QUALITY]

    if not valid:
        print(f"  ⊘ הצייד לא מצא setup ראוי (מצא {len(setups)} סטאפים, אף אחד מעל {MIN_HUNTER_QUALITY})")
        print(f"     הערכת הצייד: {hunter['parsed'].get('הערכת_שוק_כללית', '')[:200]}")
        return _save_skipped(trade_num, anchor_idx, summary, hunter, reason="no_quality_setup")

    best = max(valid, key=lambda s: s.get("ציון_איכות", 0))
    print(f"  ✓ נבחר setup: {best['סוג']} {best['כיוון']} (ציון {best['ציון_איכות']})")
    print(f"     סיבה: {best['סיבה'][:150]}")

    # 2) ועדה
    print(f"\n[2/5] הוועדה מנתחת את ה-setup...")
    committee = run_committee(summary, setup=best, lessons=lessons, history=load_trades(), verbose=False)
    decision = committee["head_decision"]["parsed"]

    advocate_result = None
    advocate_overrode = False

    if not decision or decision.get("החלטה") == "אין כניסה":
        reason = (decision or {}).get("סיבה_להחלטה", "לא ידוע")
        print(f"  ⊘ ראש הצוות החליט: אין כניסה")
        print(f"     סיבה: {reason[:200]}")

        # 2.5) פרקליט השטן - מנסה לערער על דחייה שמסתמכת על לקחים
        if lessons and decision:
            print(f"\n[2.5/5] פרקליט השטן בודק...")
            advocate_result = run_devil_advocate(
                setup=best,
                head_decision=decision,
                lessons=lessons,
                market_summary=summary,
                verbose=True,
            )
            ap = advocate_result.get("parsed") or {}
            if (ap.get("תקיפה_מוצלחת")
                    and (ap.get("ביטחון_בערעור") or 0) >= 7):
                advocate_overrode = True
                tighter_stop = ap.get("סטופ_מומלץ_הדוק_יותר")
                # בונים החלטה אלטרנטיבית מה-setup של הצייד
                hunter_entry = (best["אזור_כניסה"]["מחיר_מ"] + best["אזור_כניסה"]["מחיר_עד"]) / 2
                decision = {
                    "החלטה": best["כיוון"],
                    "כניסה": hunter_entry,
                    "סטופ": float(tighter_stop) if tighter_stop else best["סטופ_מומלץ"],
                    "יעד_1": best["יעדים_מומלצים"][0],
                    "יעד_2": best["יעדים_מומלצים"][1] if len(best["יעדים_מומלצים"]) > 1 else None,
                    "ביטחון_1_10": ap.get("ביטחון_בערעור"),
                    "סיבה_להחלטה": f"OVERRIDE ע\"י פרקליט השטן: {ap.get('סיבה_מפורטת', '')[:200]}",
                    "_advocate_override": True,
                }
                print(f"  🎭 פרקליט ניצח! נכנסים בכל זאת. כלל שנעקף: {ap.get('כלל_שעוערר')}")
            else:
                # פרקליט נכשל או לא ערער - shadow simulation בלבד
                shadow = _shadow_simulate(best, df_future) if best else None
                if shadow:
                    print(f"  👻 Shadow simulation: {shadow['outcome']} | "
                          f"P/L תיאורטי: {shadow['pnl_pct']:+.2f}%")
                return _save_skipped(
                    trade_num, anchor_idx, summary, hunter,
                    committee=committee, reason="committee_rejected",
                    advocate=advocate_result, shadow=shadow,
                )

    print(f"  ✓ ראש הצוות אישר {decision['החלטה']} - כניסה ${decision['כניסה']:,.2f}, סטופ ${decision['סטופ']:,.2f}, יעד ${decision['יעד_1']:,.2f}")

    # 3) סימולציה
    print(f"\n[3/5] מסמלץ את העסקה קדימה על דאטה אמיתי...")
    sim = simulate_trade(
        candles_after=df_future,
        direction=decision["החלטה"],
        entry_price=float(decision["כניסה"]),
        stop=float(decision["סטופ"]),
        target_1=float(decision["יעד_1"]),
        target_2=float(decision.get("יעד_2") or 0) or None,
        max_candles=SIM_FORWARD_CANDLES,
    )

    print(f"  תוצאה: {sim['outcome']} | P/L: {sim['pnl_pct']:+.2f}% | החזקה: {sim['minutes_held']} דק'")

    # 4) ועדה ביקורתית
    print(f"\n[4/5] הוועדה הביקורתית מנתחת את התוצאה...")
    trade_obj = {
        "trade_num": trade_num,
        "session": datetime.utcnow().strftime("%Y-%m-%d"),
        "anchor_idx": anchor_idx,
        "timestamp_analyzed": summary["timestamp"],
        "hunter_setup": best,
        "decision": decision,
        "advisors_summary": {
            name: result["parsed"] for name, result in committee["advisors"].items()
        },
        "simulation": sim,
        "status": "closed",
        "pnl_usd": sim["pnl_usd_per_unit"],
        "entered_via_advocate": advocate_overrode,
    }
    if advocate_overrode and advocate_result:
        trade_obj["advocate"] = advocate_result.get("parsed")

    history_stats = get_stats()
    critique = run_critique(trade_obj, history_stats, verbose=False)

    critic_result = critique["reviewers"].get("המבקר", {})
    coach_result = critique["reviewers"].get("המאמן", {})
    stat_result = critique["reviewers"].get("הסטטיסטיקאי", {})

    trade_obj["post_trade"] = {
        "critic": critic_result.get("parsed"),
        "coach": coach_result.get("parsed"),
        "statistician": stat_result.get("parsed"),
        "cost_usd": critique["totals"]["cost_usd"],
        "elapsed_sec": critique["totals"]["elapsed_sec"],
    }

    # 5) שמירת לקח
    print(f"\n[5/5] שומר לזיכרון...")
    lesson_payload = coach_result.get("parsed") or {}
    new_lesson = lesson_payload.get("לקח_חדש")
    if new_lesson and new_lesson != "אין לקח חדש" and new_lesson is not None:
        lesson_id = save_lesson({
            "trade_num": trade_num,
            "rule": new_lesson,
            "trigger": lesson_payload.get("תנאי_הפעלה"),
            "category": lesson_payload.get("סיווג"),
            "from_outcome": sim["outcome"],
        })
        print(f"  ✓ לקח חדש נשמר: {lesson_id}")
        print(f"     '{new_lesson[:150]}'")
    else:
        print(f"  ⊘ אין לקח חדש להפיק מהעסקה הזו")

    trade_id = save_trade(trade_obj)
    print(f"  ✓ עסקה נשמרה: {trade_id}")

    # סיכום קצר למשתמש
    print(f"\n📋 דיווח לך:")
    print(f"   אסטרטגיה: {best['סוג']} {best['כיוון']}")
    print(f"   כניסה: ${decision['כניסה']:,.2f}")
    print(f"   יציאה: ${sim['exit_price']:,.2f} ({sim['outcome']})")
    print(f"   רווח/הפסד: {sim['pnl_pct']:+.2f}% ב-{sim['minutes_held']} דקות")
    if new_lesson and new_lesson != "אין לקח חדש":
        print(f"   לקח: {new_lesson[:200]}")

    return trade_obj


def _shadow_simulate(setup: dict, df_future) -> dict:
    """
    מסמלץ את ה-setup של הצייד פסיבית - מה היה קורה אם היו נכנסים בכל זאת.
    משתמש במחירים שהצייד הציע ישירות.
    """
    entry = (setup["אזור_כניסה"]["מחיר_מ"] + setup["אזור_כניסה"]["מחיר_עד"]) / 2
    stop = setup["סטופ_מומלץ"]
    targets = setup.get("יעדים_מומלצים", [])
    target_1 = targets[0] if len(targets) >= 1 else None
    target_2 = targets[1] if len(targets) >= 2 else None

    if not (entry and stop and target_1):
        return None

    return simulate_trade(
        candles_after=df_future,
        direction=setup["כיוון"],
        entry_price=float(entry),
        stop=float(stop),
        target_1=float(target_1),
        target_2=float(target_2) if target_2 else None,
        max_candles=SIM_FORWARD_CANDLES,
    )


def _save_skipped(trade_num, anchor_idx, summary, hunter, committee=None,
                  reason: str = "", advocate=None, shadow=None) -> dict:
    obj = {
        "trade_num": trade_num,
        "session": datetime.utcnow().strftime("%Y-%m-%d"),
        "anchor_idx": anchor_idx,
        "timestamp_analyzed": summary["timestamp"],
        "status": "skipped",
        "skip_reason": reason,
        "hunter": hunter["parsed"],
    }
    if committee:
        obj["committee_decision"] = committee["head_decision"]["parsed"]
    if advocate:
        obj["advocate"] = advocate.get("parsed")
    if shadow:
        obj["shadow"] = shadow
        # פסיקת לקח: shadow win → דחייה הייתה שגויה. shadow loss → דחייה הייתה צודקת.
        obj["shadow_verdict"] = "rejection_was_wrong" if shadow.get("outcome") in ("target_1", "target_2") else "rejection_was_right"
    save_trade(obj)
    return obj


SCAN_STEP_CANDLES = 8              # מתקדמים 8 נרות (שעתיים) בין סריקות
MAX_SCANS_BETWEEN_TRADES = 60      # תקרת בטיחות - לא יותר מ-60 סריקות כדי להגיע לעסקה אחת


def is_market_worth_scanning(summary: dict) -> tuple:
    """
    פילטר פייתון מהיר וזול לפני קריאת הצייד.
    סוחר מנוסה לא מסתכל על השוק כל שעה - הוא מסתכל רק כשמשהו קורה.
    מחזיר (worth_scanning: bool, reason: str)
    """
    ind = summary["indicators"]
    rsi = ind["rsi"]
    bb_width = ind["bb_width"]
    vol_ratio = ind["volume_ratio"]
    atr_pct = ind["atr_pct"]
    last_change = abs(summary["candle"]["change_pct"])

    triggers = []

    if rsi >= 65 or rsi <= 35:
        triggers.append(f"RSI קיצוני ({rsi:.1f})")

    if bb_width <= 0.012:
        triggers.append(f"BB squeeze ({bb_width:.4f})")

    if vol_ratio >= 1.5 or vol_ratio <= 0.4:
        triggers.append(f"נפח חריג ({vol_ratio:.2f})")

    if last_change >= 0.4:
        triggers.append(f"נר חזק ({last_change:.2f}%)")

    if atr_pct >= 0.35:
        triggers.append(f"תנודתיות גבוהה ({atr_pct:.2f}%)")

    if triggers:
        return True, " + ".join(triggers)
    return False, f"שקט (RSI {rsi:.0f}, BB {bb_width:.3f}, vol {vol_ratio:.1f})"


def run_sequential_session(df, target_trades: int) -> list:
    """
    סריקה רציפה: מתחילים מנקודת התחלה, סורקים כל SCAN_STEP_CANDLES נרות.
    כשהוועדה מאשרת - נכנסים, מסמלצים, ועדה ביקורתית, ואז ממשיכים מהנר שאחרי היציאה.
    כך הלקח מעסקה N כבר ב-lessons.json לפני שמתחילים את עסקה N+1.
    """
    completed_trades = []
    skipped_scans = []
    trade_num = 0
    idx = CONTEXT_BEFORE + 50
    end_limit = len(df) - SIM_FORWARD_CANDLES - 5

    scans_since_last_trade = 0

    while trade_num < target_trades and idx < end_limit:
        df_until_now = df.iloc[idx - CONTEXT_BEFORE: idx + 1].reset_index(drop=True)
        df_future = df.iloc[idx + 1: idx + 1 + SIM_FORWARD_CANDLES].reset_index(drop=True)

        summary = market_summary(df_until_now)
        print(f"\n{'─' * 70}")
        print(f"סריקה @ idx={idx} | {summary['timestamp']} | ${summary['price']:,.2f} | RSI {summary['indicators']['rsi']} | טרנד {summary['trend']}")

        worth_it, reason = is_market_worth_scanning(summary)
        if not worth_it:
            print(f"  ⊘ פילטר פייתון: {reason} — דילוג ללא קריאה לצייד")
            idx += SCAN_STEP_CANDLES
            scans_since_last_trade += 1
            continue
        print(f"  ► טריגרים: {reason} — מפעיל צייד")

        candles_view = candle_window(df_until_now, n=25)
        lessons = relevant_lessons(summary, limit=5)
        hunter = run_hunter(summary, candles_view, lessons=lessons, verbose=False)

        if hunter["is_error"] or not hunter["parsed"]:
            print(f"  ✗ הצייד נכשל ({hunter.get('error', 'unknown')}), מתקדם.")
            idx += SCAN_STEP_CANDLES
            scans_since_last_trade += 1
            continue

        setups = hunter["parsed"].get("setups", [])
        valid = [s for s in setups if (s.get("ציון_איכות", 0) or 0) >= MIN_HUNTER_QUALITY]

        if not valid:
            print(f"  ⊘ הצייד מצא {len(setups)} setups, אף אחד באיכות ≥{MIN_HUNTER_QUALITY}")
            idx += SCAN_STEP_CANDLES
            scans_since_last_trade += 1
            if scans_since_last_trade > MAX_SCANS_BETWEEN_TRADES:
                print(f"\n  ! עברו {scans_since_last_trade} סריקות בלי כניסה - עוצר כדי לא לבזבז.")
                break
            continue

        best = max(valid, key=lambda s: s.get("ציון_איכות", 0))
        print(f"  ✓ Setup: {best['סוג']} {best['כיוון']} (ציון {best['ציון_איכות']}) — מפעיל ועדה...")

        committee = run_committee(summary, setup=best, lessons=lessons, history=load_trades(), verbose=False)
        decision = committee["head_decision"]["parsed"]

        if not decision or decision.get("החלטה") not in ("LONG", "SHORT"):
            reason = (decision or {}).get("סיבה_להחלטה", "no decision parsed")
            print(f"  ⊘ ועדה דחתה: {reason[:140]}")

            # פרקליט השטן - מערער על הדחייה אם יש סיבה
            print(f"  🎭 פרקליט השטן בודק את הדחייה...")
            advocate = run_devil_advocate(best, decision or {}, lessons, summary, verbose=False)
            advocate_parsed = advocate.get("parsed") or {}
            override_succeeded = advocate_parsed.get("תקיפה_מוצלחת", False)
            advocate_confidence = advocate_parsed.get("ביטחון_בערעור", 0)

            # סימולציית Shadow - מה היה קורה אם היו נכנסים?
            entry_zone = best.get("אזור_כניסה", {})
            shadow_entry = float(entry_zone.get("מחיר_עד") or entry_zone.get("מחיר_מ") or summary["price"])
            shadow_stop = float(best.get("סטופ_מומלץ") or 0)
            targets = best.get("יעדים_מומלצים") or [0]
            shadow_t1 = float(targets[0]) if targets else 0
            shadow_t2 = float(targets[1]) if len(targets) > 1 else None

            shadow_sim = simulate_trade(
                candles_after=df_future,
                direction=best["כיוון"],
                entry_price=shadow_entry,
                stop=shadow_stop,
                target_1=shadow_t1,
                target_2=shadow_t2,
                max_candles=SIM_FORWARD_CANDLES,
            )

            would_have_pnl = shadow_sim.get("pnl_pct", 0)
            committee_was_right = would_have_pnl <= 0
            verdict_emoji = "✓ הוועדה צדקה" if committee_was_right else "✗ הוועדה טעתה"
            print(f"  👻 Shadow sim: {shadow_sim['outcome']} | P/L תאורטי: {would_have_pnl:+.2f}% — {verdict_emoji}")

            if override_succeeded:
                advocate_msg = "צדק!" if not committee_was_right else "טעה"
                print(f"  🎭 הפרקליט עירער (ביטחון {advocate_confidence}/10) — {advocate_msg}")
            else:
                print(f"  🎭 הפרקליט הסכים עם הדחייה")

            # עדכון confidence של הלקחים שעמדו בפני הוועדה
            for lesson in lessons:
                lid = lesson.get("id")
                if not lid:
                    continue
                update_lesson_stats(
                    lid,
                    invoked=True,
                    correct=committee_was_right,
                    wrong=not committee_was_right,
                )

            # שמירת רשומת shadow למחקר
            shadow_obj = {
                "session": datetime.utcnow().strftime("%Y-%m-%d"),
                "scan_idx": idx,
                "timestamp_analyzed": summary["timestamp"],
                "status": "shadow_rejected",
                "hunter_setup": best,
                "rejected_decision": decision or {},
                "advocate": advocate_parsed,
                "shadow_simulation": shadow_sim,
                "committee_was_right": committee_was_right,
                "would_have_pnl_pct": would_have_pnl,
                "lessons_at_decision": [{"id": l.get("id"), "rule": l.get("rule", "")[:120]} for l in lessons],
            }
            save_trade(shadow_obj)

            skipped_scans.append({
                "idx": idx,
                "timestamp": summary["timestamp"],
                "reason": "committee_rejected",
                "shadow_pnl_pct": would_have_pnl,
                "committee_was_right": committee_was_right,
            })
            idx += SCAN_STEP_CANDLES
            scans_since_last_trade += 1
            continue

        trade_num += 1
        scans_since_last_trade = 0

        print(f"\n{'=' * 70}")
        print(f"  ✅ עסקה #{trade_num}/{target_trades} - נכנסים!")
        print(f"  {decision['החלטה']} | כניסה ${decision['כניסה']:,.2f} | סטופ ${decision['סטופ']:,.2f} | יעד ${decision['יעד_1']:,.2f}")
        print(f"{'=' * 70}")

        sim = simulate_trade(
            candles_after=df_future,
            direction=decision["החלטה"],
            entry_price=float(decision["כניסה"]),
            stop=float(decision["סטופ"]),
            target_1=float(decision["יעד_1"]),
            target_2=float(decision.get("יעד_2") or 0) or None,
            max_candles=SIM_FORWARD_CANDLES,
        )

        print(f"\n  📊 תוצאה: {sim['outcome']} | exit ${sim['exit_price']:,.2f} ({sim.get('exit_reason', '?')})")
        print(f"  P/L: {sim['pnl_pct']:+.2f}% | החזקה: {sim['minutes_held']} דק' ({sim.get('candles_held', sim.get('bars_held', 0))} נרות)")

        trade_obj = {
            "trade_num": trade_num,
            "session": datetime.utcnow().strftime("%Y-%m-%d"),
            "scan_idx": idx,
            "timestamp_analyzed": summary["timestamp"],
            "hunter_setup": best,
            "decision": decision,
            "advisors_summary": {name: r["parsed"] for name, r in committee["advisors"].items()},
            "simulation": sim,
            "status": "closed",
            "pnl_usd": sim["pnl_usd_per_unit"],
        }

        history_stats = get_stats()
        print(f"\n  🔍 ועדה ביקורתית...")
        critique = run_critique(trade_obj, history_stats, verbose=False)

        critic = critique["reviewers"].get("המבקר", {}).get("parsed") or {}
        coach = critique["reviewers"].get("המאמן", {}).get("parsed") or {}
        stat = critique["reviewers"].get("הסטטיסטיקאי", {}).get("parsed") or {}

        trade_obj["post_trade"] = {
            "critic": critic,
            "coach": coach,
            "statistician": stat,
        }

        print(f"     המבקר: ניתוח היה {critic.get('ניתוח_היה_נכון', '?')} | {critic.get('מזל_או_עבודה', '?')}")
        new_lesson = (coach or {}).get("לקח_חדש")
        if new_lesson and new_lesson not in (None, "null", "אין לקח חדש"):
            save_lesson({
                "trade_num": trade_num,
                "rule": new_lesson,
                "trigger": (coach or {}).get("תנאי_הפעלה"),
                "category": (coach or {}).get("סיווג"),
                "from_outcome": sim["outcome"],
            })
            print(f"     💡 לקח חדש: {new_lesson[:150]}")
        else:
            print(f"     ⊘ אין לקח חדש")

        save_trade(trade_obj)
        completed_trades.append(trade_obj)

        # עדכון חשבון
        won = sim.get("pnl_pct", 0) > 0
        acc_update = update_account_after_trade(sim["pnl_pct"], won)
        acc = acc_update["account"]
        balance_emoji = "💚" if won else "💔"
        print(f"\n  {balance_emoji} עודכן חשבון: יתרה ${acc['current_balance']:,.2f} "
              f"(שינוי ${acc_update['pnl_usd']:+.2f}, סיכון לעסקה ${acc_update['risk_amount']:,.2f})")
        print(f"  🎯 התקדמות ליעד ${acc['target_balance']:,.0f}: {acc_update['progress_pct']:.1f}%")
        if acc_update["reached_target"]:
            print(f"  🏆 *** הגעת ליעד! ${acc['target_balance']:,.0f} הושג ***")

        bars_held = sim.get("candles_held", sim.get("bars_held", 1))
        idx += bars_held + 1
        print(f"\n  ► קופץ לאחרי היציאה (idx={idx}). הלקח כבר בזיכרון לעסקה הבאה.")

    return completed_trades, skipped_scans


def main():
    acc = load_account()
    print("=" * 70)
    print(f"סשן למידה יומי - {SYMBOL} @ {TIMEFRAME_ANALYSIS}")
    print(f"יעד עסקאות: {SESSION_TRADES_TARGET}")
    print(f"💰 חשבון: ${acc['current_balance']:,.2f} / ${acc['target_balance']:,.0f} "
          f"({acc['stage_description']})")
    print("=" * 70)

    print(f"\nמושך {LOOKBACK_CANDLES} נרות אחרונים...")
    client = BinanceClient()
    df = client.get_klines(SYMBOL, TIMEFRAME_ANALYSIS, limit=LOOKBACK_CANDLES)
    print(f"  נמשכו {len(df)} נרות. טווח: {df.iloc[0]['open_time']} → {df.iloc[-1]['open_time']}")

    completed, skipped = run_sequential_session(df, SESSION_TRADES_TARGET)

    print("\n" + "=" * 70)
    print("סיכום הסשן")
    print("=" * 70)
    print(f"  עסקאות שהוצאו לפועל: {len(completed)} / {SESSION_TRADES_TARGET}")
    print(f"  סריקות שלא הניבו כניסה: {len(skipped)}")

    if completed:
        wins = [t for t in completed if t.get("pnl_usd", 0) > 0]
        losses = [t for t in completed if t.get("pnl_usd", 0) <= 0]
        total_pnl = sum(t.get("pnl_usd", 0) for t in completed)
        print(f"  ניצחונות: {len(wins)}, הפסדים: {len(losses)}")
        print(f"  Win Rate בסשן: {len(wins) / len(completed) * 100:.1f}%")
        print(f"  P/L מצטבר (ליחידה): ${total_pnl:+.2f}")

    # סטטיסטיקת shadow - הוועדה מדויקת בדחיות?
    shadow_rejections = [s for s in skipped if "shadow_pnl_pct" in s]
    if shadow_rejections:
        committee_correct = sum(1 for s in shadow_rejections if s.get("committee_was_right"))
        avoided_loss = sum(s["shadow_pnl_pct"] for s in shadow_rejections if s["shadow_pnl_pct"] < 0)
        missed_profit = sum(s["shadow_pnl_pct"] for s in shadow_rejections if s["shadow_pnl_pct"] > 0)
        accuracy = committee_correct / len(shadow_rejections) * 100
        print(f"\n  👻 דיוק הוועדה בדחיות: {committee_correct}/{len(shadow_rejections)} = {accuracy:.0f}%")
        print(f"     הפסדים שנמנעו: {avoided_loss:+.2f}% | רווחים שפוספסו: {missed_profit:+.2f}%")

    print("\nלצפייה בכל ההיסטוריה: cat memory/trades.json | head -200")
    print("לצפייה בלקחים: cat memory/lessons.json")


if __name__ == "__main__":
    main()
