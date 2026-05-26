"""
אימון מאסיבי - מריץ סשני learn_daily ברצף עם התראות בטלגרם.
כל סשן יוצר 1 עסקה היסטורית + לקחים. מאיץ למידה בסדר גודל.

מצבי הפעלה:
    python3 scripts/bulk_train.py            # 20 סשנים (ברירת מחדל ישנה)
    python3 scripts/bulk_train.py 50         # 50 סשנים
    python3 scripts/bulk_train.py --night    # רץ אינסוף עד שנוצר memory/learning_stop.flag
"""
import sys
import os
import subprocess
from pathlib import Path
from datetime import datetime
import json

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from memory_store import load_trades, load_lessons, load_account

LEARN_SCRIPT = ROOT / "scripts" / "learn_daily.py"
load_dotenv(ROOT / ".env")
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID_FILE = ROOT / "memory" / "authorized_chat_id.txt"
CHAT_ID = CHAT_ID_FILE.read_text().strip() if CHAT_ID_FILE.exists() else None
BASE_URL = f"https://api.telegram.org/bot{TOKEN}" if TOKEN else None

STATE_FILE = ROOT / "memory" / "learning_state.json"
STOP_FILE = ROOT / "memory" / "learning_stop.flag"


def send_telegram(text: str):
    if not (BASE_URL and CHAT_ID):
        return
    try:
        requests.post(
            f"{BASE_URL}/sendMessage",
            json={"chat_id": int(CHAT_ID), "text": text},
            timeout=15,
        )
    except Exception as e:
        print(f"⚠️ טלגרם נכשל: {e}")


def get_latest_trade():
    trades = load_trades()
    closed = [t for t in trades if t.get("status") == "closed"]
    return closed[-1] if closed else None


def write_state(**kwargs):
    """מעדכן את memory/learning_state.json - הבוט קורא משם."""
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        existing = {}
        if STATE_FILE.exists():
            try:
                existing = json.loads(STATE_FILE.read_text())
            except Exception:
                existing = {}
        existing.update(kwargs)
        existing["updated_at"] = datetime.now().isoformat()
        STATE_FILE.write_text(json.dumps(existing, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"⚠️ write_state נכשל: {e}")


def should_stop() -> bool:
    return STOP_FILE.exists()


def parse_args():
    """מחזיר (N_SESSIONS, night_mode)."""
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg in ("--night", "-n", "night"):
            return (10**9, True)  # עד שנוצר stop flag
        try:
            return (int(arg), False)
        except ValueError:
            pass
    return (20, False)


def main():
    n_sessions, night_mode = parse_args()
    start_time = datetime.now()

    # ניקוי stop flag ישן אם נשאר
    if STOP_FILE.exists():
        try:
            STOP_FILE.unlink()
        except Exception:
            pass

    title = "🌙 *למידת לילה התחילה*" if night_mode else "🎓 *אימון מאסיבי התחיל*"
    eta = "רץ עד שתשלח /learn_stop" if night_mode else f"יעד: {n_sessions} סשנים"
    send_telegram(
        f"{title}\n"
        f"{eta}\n"
        f"זמן: {start_time.strftime('%H:%M')}\n\n"
        f"תקבל התראה אחרי כל עסקה.\n"
        f"בזמן הזה ה-auto-scanner של הלייב משבית את עצמו."
    )

    initial_count = len([t for t in load_trades() if t.get("status") == "closed"])
    initial_lessons = len(load_lessons())

    write_state(
        active=True,
        night_mode=night_mode,
        pid=os.getpid(),
        started_at=start_time.isoformat(),
        sessions_completed=0,
        sessions_target=n_sessions if not night_mode else None,
        wins=0,
        losses=0,
        new_trades=0,
        new_lessons=0,
        last_message=None,
    )

    successes = 0
    failures = 0
    wins = 0
    stopped_by_user = False

    for i in range(1, n_sessions + 1):
        if should_stop():
            stopped_by_user = True
            print(f"🛑 stop flag זוהה - יוצא אחרי {i-1} סשנים")
            break

        session_start = datetime.now()
        print(f"\n[{session_start.strftime('%H:%M:%S')}] סשן {i}...")
        try:
            result = subprocess.run(
                [sys.executable, "-u", str(LEARN_SCRIPT)],
                cwd=str(ROOT),
                capture_output=True, text=True,
                timeout=1500,
            )
            elapsed = (datetime.now() - session_start).total_seconds() / 60
            if result.returncode == 0:
                successes += 1
                trade = get_latest_trade()
                if trade:
                    sim = trade.get("simulation") or {}
                    decision = trade.get("decision") or {}
                    setup = trade.get("hunter_setup") or {}
                    pnl = sim.get("pnl_pct", 0)
                    coach = (trade.get("post_trade") or {}).get("coach") or {}
                    lesson = coach.get("לקח_חדש")

                    if pnl > 0:
                        wins += 1
                        emoji = "🟢"
                        verdict = "ניצחון"
                    else:
                        emoji = "🔴"
                        verdict = "הפסד"

                    acc = load_account()
                    label = f"סשן {i}" + (f"/{n_sessions}" if not night_mode else "")
                    msg = (
                        f"{emoji} *{label} - {verdict}*\n\n"
                        f"📊 {setup.get('סוג', '?')} {decision.get('החלטה', '?')}\n"
                        f"💵 P/L: {pnl:+.2f}% (${sim.get('pnl_usd_per_unit', 0):+.2f})\n"
                        f"⏱ {elapsed:.1f} דק' (החזקה {sim.get('minutes_held', 0)} דק')\n"
                        f"💰 יתרה: ${acc['current_balance']:,.2f}\n"
                        f"📈 התקדמות: {successes} הצלחות, {wins} ניצחונות"
                    )
                    if lesson and lesson not in (None, "אין לקח חדש", "null"):
                        msg += f"\n\n💡 לקח: {lesson[:200]}"

                    send_telegram(msg)
                    print(f"   ✓ {verdict} {pnl:+.2f}%")
                else:
                    print(f"   ✓ אבל לא נמצאה עסקה חדשה")
            else:
                failures += 1
                print(f"   ✗ נכשל (exit {result.returncode})")
        except subprocess.TimeoutExpired:
            failures += 1
            print(f"   ⏱ timeout")
            send_telegram(f"⚠️ סשן {i} נכשל ב-timeout")
        except Exception as e:
            failures += 1
            print(f"   ⚠️ שגיאה: {e}")

        # עדכון state אחרי כל סשן - לבוט
        current_count = len([t for t in load_trades() if t.get("status") == "closed"])
        write_state(
            sessions_completed=i,
            successes=successes,
            failures=failures,
            wins=wins,
            new_trades=current_count - initial_count,
            new_lessons=len(load_lessons()) - initial_lessons,
        )

        # סיכום ביניים כל 5 סשנים
        if i % 5 == 0:
            new_trades = current_count - initial_count
            new_lessons = len(load_lessons()) - initial_lessons
            elapsed_total = (datetime.now() - start_time).total_seconds() / 60
            wr = (wins / max(successes, 1)) * 100

            send_telegram(
                f"📊 *סיכום ביניים - אחרי {i} סשנים*\n\n"
                f"✅ הצליחו: {successes}\n"
                f"🏆 ניצחונות: {wins} ({wr:.0f}%)\n"
                f"❌ נכשלו: {failures}\n\n"
                f"📈 עסקאות חדשות בזיכרון: {new_trades}\n"
                f"💡 לקחים חדשים: {new_lessons}\n"
                f"⏱ זמן שעבר: {elapsed_total:.0f} דק'"
            )

    total_time = (datetime.now() - start_time).total_seconds() / 60
    final_count = len([t for t in load_trades() if t.get("status") == "closed"])
    final_lessons = len(load_lessons())

    if stopped_by_user:
        title = "🛑 *למידה נעצרה על ידי /learn_stop*"
    elif night_mode:
        title = "🌅 *למידת לילה הסתיימה*"
    else:
        title = "🎉 *האימון הסתיים!*"

    send_telegram(
        f"{title}\n\n"
        f"✅ הצליחו: {successes}\n"
        f"🏆 ניצחונות: {wins}\n"
        f"📈 עסקאות חדשות: {final_count - initial_count}\n"
        f"💡 לקחים חדשים: {final_lessons - initial_lessons}\n"
        f"⏱ זמן כולל: {total_time:.0f} דק'\n\n"
        f"ה-auto-scanner של הלייב חזר לפעולה.\n"
        f"שלח /stats ו-/lessons לראות התקדמות."
    )

    write_state(active=False, sessions_completed=successes, ended_at=datetime.now().isoformat(),
                stopped_by_user=stopped_by_user)

    # ניקוי stop flag
    if STOP_FILE.exists():
        try:
            STOP_FILE.unlink()
        except Exception:
            pass

    print(f"\n✅ הסתיים: {successes} סשנים, {wins} wins, {total_time:.0f} דק'")


if __name__ == "__main__":
    main()
