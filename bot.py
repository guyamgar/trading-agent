"""
טלגרם בוט - שליטה מהפלאפון על מערכת המסחר.

שימוש:
1. הרץ: python3 bot.py
2. בטלגרם שלח /start לבוט - הוא ירשום את ה-chat_id שלך אוטומטית
3. אחרי האימות, רק אתה תוכל לפקד עליו

פקודות זמינות:
  /start    אתחול ראשוני / רשימת פקודות
  /run      מפעיל סשן למידה מהיר (1 עסקה)
  /status   מה קורה עכשיו בסשן הרץ
  /last     תוצאת העסקה האחרונה
  /stats    Win Rate כללי, P/L, מספר לקחים
  /lessons  5 לקחים אחרונים
  /stop     עוצר סשן רץ
"""
import os
import sys
import time
import subprocess
import threading
from pathlib import Path
from datetime import datetime

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from memory_store import load_trades, load_lessons, get_stats, load_account
from agents.orchestrator import run_tuner
from agents.llm import call_claude
from auto_apply import apply_recommendations
from live_check import check_live_market, load_open_recs
from live_monitor import poll_open_recommendations
from config import (
    SYMBOL, TIMEFRAME_ANALYSIS, RISK_PER_TRADE_PCT,
    MIN_RISK_REWARD, DEFAULT_ACCOUNT_SIZE_USD,
)

load_dotenv(ROOT / ".env")
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    print("❌ אין TELEGRAM_BOT_TOKEN ב-.env")
    sys.exit(1)

BASE_URL = f"https://api.telegram.org/bot{TOKEN}"

AUTH_FILE = ROOT / "memory" / "authorized_chat_id.txt"
SESSION_LOG = ROOT / "logs" / "bot_session.log"
SESSION_LOG.parent.mkdir(exist_ok=True)

# State global - הסשן הרץ כרגע (אם יש)
_active_session = {"process": None, "thread": None, "chat_id": None}


# ─── תקשורת עם טלגרם ────────────────────────────────────────────

def send_message(chat_id: int, text: str, parse_mode: str = "Markdown"):
    """שולח הודעה לטלגרם. מחלק לחלקים אם ארוך מ-4000 תווים.
    אם markdown נכשל - מנסה שוב בלי parse_mode."""
    chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)] or [""]
    for chunk in chunks:
        payload = {"chat_id": chat_id, "text": chunk}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        try:
            r = requests.post(f"{BASE_URL}/sendMessage", json=payload, timeout=15)
            if r.status_code != 200:
                # אם זה כשל markdown - מנסה בלי parse_mode
                desc = r.json().get("description", "")
                print(f"⚠️ Telegram {r.status_code}: {desc[:120]}")
                if parse_mode and ("parse" in desc.lower() or "entity" in desc.lower()):
                    payload.pop("parse_mode", None)
                    r2 = requests.post(f"{BASE_URL}/sendMessage", json=payload, timeout=15)
                    if r2.status_code != 200:
                        print(f"❌ גם בלי markdown נכשל: {r2.text[:120]}")
        except Exception as e:
            print(f"שגיאה בשליחת הודעה: {e}")


def get_updates(offset: int = 0):
    """polling - מקבל הודעות חדשות מטלגרם."""
    try:
        resp = requests.get(
            f"{BASE_URL}/getUpdates",
            params={"offset": offset, "timeout": 30},
            timeout=35,
        )
        return resp.json().get("result", [])
    except Exception as e:
        print(f"שגיאה ב-getUpdates: {e}")
        return []


# ─── אימות chat_id ──────────────────────────────────────────────

def get_authorized_chat_id():
    if AUTH_FILE.exists():
        try:
            return int(AUTH_FILE.read_text().strip())
        except ValueError:
            return None
    return None


def set_authorized_chat_id(chat_id: int):
    AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUTH_FILE.write_text(str(chat_id))


# ─── הפעלת סשן ──────────────────────────────────────────────────

def _watch_session_log(chat_id: int, log_path: Path, stop_event: threading.Event):
    """עוקב אחרי לוג של הסשן ושולח אירועים חשובים לטלגרם."""
    last_pos = 0
    keywords = [
        ("נכנסים!", "📈"),
        ("ועדה דחתה", "⊘"),
        ("הצייד נכשל", "✗"),
        ("פרקליט", "🎭"),
        ("Shadow sim", "👻"),
        ("📊 תוצאה:", "🎯"),
        ("P/L:", "💵"),
        ("לקח חדש", "💡"),
        ("עודכן חשבון", "💰"),
        ("התקדמות ליעד", "🏁"),
        ("הגעת ליעד", "🏆"),
    ]

    while not stop_event.is_set():
        if not log_path.exists():
            time.sleep(1)
            continue

        with log_path.open("r") as f:
            f.seek(last_pos)
            new_content = f.read()
            last_pos = f.tell()

        if new_content:
            for line in new_content.splitlines():
                for kw, emoji in keywords:
                    if kw in line:
                        # הקטנת רעש - מסרים קצרים
                        msg = line.strip()
                        if len(msg) > 500:
                            msg = msg[:500] + "..."
                        send_message(chat_id, f"{emoji} {msg}")
                        break

        time.sleep(2)


def run_session_async(chat_id: int):
    """מפעיל סשן ברקע, שולח עדכונים תוך כדי, ובסיום שולח סיכום."""
    global _active_session
    if _active_session["process"] and _active_session["process"].poll() is None:
        send_message(chat_id, "⚠️ כבר רץ סשן. השתמש ב-/stop כדי לעצור.")
        return

    send_message(chat_id, "🚀 מתחיל סשן... עדכונים חיים בדרך.")

    if SESSION_LOG.exists():
        SESSION_LOG.unlink()

    proc = subprocess.Popen(
        [sys.executable, "-u", str(ROOT / "scripts" / "learn_daily.py")],
        cwd=str(ROOT),
        stdout=open(SESSION_LOG, "w"),
        stderr=subprocess.STDOUT,
    )
    _active_session["process"] = proc
    _active_session["chat_id"] = chat_id

    stop_event = threading.Event()
    watcher = threading.Thread(
        target=_watch_session_log,
        args=(chat_id, SESSION_LOG, stop_event),
        daemon=True,
    )
    watcher.start()

    def wait_and_summarize():
        proc.wait()
        stop_event.set()
        time.sleep(2)
        trades = load_trades()
        closed = [t for t in trades if t.get("status") == "closed"]
        stats = get_stats()
        acc = load_account()
        if closed:
            last = closed[-1]
            sim = last.get("simulation") or {}
            outcome = sim.get("outcome", "?")
            pnl_pct = sim.get("pnl_pct", 0)
            mins = sim.get("minutes_held", 0)
            win_emoji = "🟢" if pnl_pct > 0 else "🔴"
            verdict = "ניצחון" if pnl_pct > 0 else "הפסד"
            progress = ((acc["current_balance"] - acc["starting_balance"]) /
                        max(acc["target_balance"] - acc["starting_balance"], 1) * 100)
            bar_filled = int(max(0, min(progress, 100)) / 5)
            bar = "█" * bar_filled + "░" * (20 - bar_filled)
            msg = f"""✅ *הסשן הסתיים*

{win_emoji} *תוצאה:* {verdict} {pnl_pct:+.2f}% ({outcome}, {mins} דק')

💰 *החשבון שלך:*
${acc['current_balance']:,.2f} / ${acc['target_balance']:,.0f}
`{bar}` {progress:.1f}%
{acc['wins']}W / {acc['losses']}L ({acc['stage_description']})

📊 *סטטיסטיקה כללית:*
• Win Rate: {stats.get('win_rate_pct', 0)}%
• Profit Factor: {stats.get('profit_factor', '?')}
• עסקאות סגורות: {stats.get('closed_trades', 0)}

/account /last /lessons /analyze"""
            if acc["current_balance"] >= acc["target_balance"]:
                msg += f"\n\n🏆 *הגעת ליעד!* שלח /advance למעבר לשלב הבא."
        else:
            msg = "✅ הסשן הסתיים. אין עסקאות סגורות עדיין."
        send_message(chat_id, msg)
        _active_session["process"] = None

        # אוטו-/analyze כל 10 עסקאות סגורות
        try:
            closed_count = stats.get("closed_trades", 0)
            from memory_store import save_account
            last_analyzed = acc.get("last_auto_analyze_at", 0)
            if closed_count >= last_analyzed + 10:
                acc["last_auto_analyze_at"] = closed_count
                save_account(acc)
                send_message(
                    chat_id,
                    f"🧠 *אוטו-מכוון מתחיל* (עברנו {closed_count} עסקאות, {closed_count - last_analyzed} מאז הניתוח הקודם)\n_זה ייקח 1-4 דקות..._",
                )
                cmd_analyze(chat_id)
        except Exception as e:
            send_message(chat_id, f"⚠️ אוטו-מכוון נכשל: {e}")

    threading.Thread(target=wait_and_summarize, daemon=True).start()


def stop_session(chat_id: int):
    proc = _active_session.get("process")
    if proc and proc.poll() is None:
        proc.terminate()
        time.sleep(2)
        if proc.poll() is None:
            proc.kill()
        send_message(chat_id, "🛑 הסשן הופסק.")
        _active_session["process"] = None
    else:
        send_message(chat_id, "אין סשן רץ כרגע.")


# ─── פקודות מידע ─────────────────────────────────────────────────

def cmd_status(chat_id: int):
    proc = _active_session.get("process")
    if not proc or proc.poll() is not None:
        send_message(chat_id, "אין סשן רץ. שלח /run כדי להתחיל.")
        return

    if SESSION_LOG.exists():
        lines = SESSION_LOG.read_text().splitlines()
        last_lines = "\n".join(lines[-12:])
        send_message(chat_id, f"📊 סטטוס סשן רץ:\n\n```\n{last_lines}\n```")
    else:
        send_message(chat_id, "סשן רץ, אבל אין עדיין פלט.")


def cmd_last(chat_id: int):
    trades = load_trades()
    closed = [t for t in trades if t.get("status") == "closed"]
    if not closed:
        send_message(chat_id, "אין עסקאות סגורות עדיין.")
        return

    last = closed[-1]
    sim = last.get("simulation", {})
    setup = last.get("hunter_setup", {})
    decision = last.get("decision", {})
    coach = (last.get("post_trade") or {}).get("coach") or {}

    gross_pct = sim.get("gross_pnl_pct", sim.get("pnl_pct", 0))
    net_pct = sim.get("pnl_pct", 0)
    fee_pct = sim.get("fee_pct", 0.2)

    msg = f"""📋 *עסקה אחרונה*
תאריך: {last.get('session', '?')}
סוג: {setup.get('סוג', '?')} {decision.get('החלטה', '?')}
כניסה: ${decision.get('כניסה', 0):,.2f}
יציאה: ${sim.get('exit_price', 0):,.2f} ({sim.get('outcome', '?')})

📊 *P/L:*
• Gross (לפני עמלות): {gross_pct:+.2f}%
• עמלות Binance: -{fee_pct:.2f}%
• *Net (אחרי עמלות): {net_pct:+.2f}%*

החזקה: {sim.get('minutes_held', 0)} דק'"""

    new_lesson = coach.get("לקח_חדש")
    if new_lesson and new_lesson not in (None, "אין לקח חדש"):
        msg += f"\n\n💡 לקח: {new_lesson[:300]}"

    send_message(chat_id, msg)


def cmd_stats(chat_id: int):
    stats = get_stats()
    lessons = load_lessons()
    trades = load_trades()
    shadow_count = sum(1 for t in trades if t.get("status") == "shadow_rejected")

    msg = f"""📈 *סטטיסטיקה כללית*
סה"כ עסקאות: {stats.get('total_trades', 0)}
סגורות (אמיתיות): {stats.get('closed_trades', 0)}
Shadow rejections: {shadow_count}
Win Rate: {stats.get('win_rate_pct', 0)}%
Avg Win: ${stats.get('avg_win_usd', 0):.2f}
Avg Loss: ${stats.get('avg_loss_usd', 0):.2f}
P/L מצטבר: ${stats.get('total_pnl_usd', 0):+.2f}
Profit Factor: {stats.get('profit_factor', '?')}

📚 לקחים בזיכרון: {len(lessons)}
🎯 מוכן ללייב: {'כן ✅' if stats.get('ready_for_live') else 'עוד לא ❌'}"""

    send_message(chat_id, msg)


def cmd_lessons(chat_id: int):
    lessons = load_lessons()
    if not lessons:
        send_message(chat_id, "אין לקחים בזיכרון.", parse_mode="")
        return

    # 5 אחרונים
    recent = lessons[-5:]
    msg = f"📚 5 לקחים אחרונים (מתוך {len(lessons)}):\n\n"
    for i, l in enumerate(recent, 1):
        conf = l.get("confidence", 1)
        rule = (l.get("rule", "") or "")[:300]
        invoked = l.get("times_invoked", 0)
        msg += f"{i}. [conf={conf} | invoked={invoked}]\n{rule}\n\n"

    # plain text - מונע כשל markdown בגלל תווים מיוחדים בלקחים
    send_message(chat_id, msg, parse_mode="")


def cmd_analyze(chat_id: int):
    send_message(chat_id, "🔍 *המכוון מתחיל לנתח את כל המערכת...*\nזה ייקח 1-3 דקות. אשלח עדכוני חיים תוך כדי.")

    done_event = threading.Event()

    def _heartbeat():
        """שולח עדכון חיים כל 45 שניות כדי שתדע שזה עוד עובד."""
        beats = ["📥 טוען נתונים מהזיכרון...",
                 "🧠 הרהרור על דפוסי עסקאות...",
                 "🔍 מחפש לקחים שצריך לעדכן...",
                 "📊 מחשב סטטיסטיקה...",
                 "✏️ מנסח המלצות..."]
        i = 0
        while not done_event.wait(45):
            send_message(chat_id, f"⏳ עדיין עובד ({(i+1)*45}s)... {beats[i % len(beats)]}")
            i += 1

    def _run():
        trades = load_trades()
        lessons = load_lessons()
        config_snapshot = {
            "SYMBOL": SYMBOL,
            "TIMEFRAME_ANALYSIS": TIMEFRAME_ANALYSIS,
            "RISK_PER_TRADE_PCT": RISK_PER_TRADE_PCT,
            "MIN_RISK_REWARD": MIN_RISK_REWARD,
            "DEFAULT_ACCOUNT_SIZE_USD": DEFAULT_ACCOUNT_SIZE_USD,
            "MIN_HUNTER_QUALITY": 5,
            "SCAN_STEP_CANDLES": 8,
            "SIM_FORWARD_CANDLES": 96,
        }
        # heartbeat רץ בthread מקביל
        hb = threading.Thread(target=_heartbeat, daemon=True)
        hb.start()
        try:
            result = run_tuner(trades, lessons, config_snapshot, verbose=False)
        finally:
            done_event.set()
        if result.get("is_error") or not result.get("parsed"):
            send_message(chat_id, f"❌ המכוון נכשל: {result.get('error', 'לא ידוע')}")
            return

        p = result["parsed"]
        summary = p.get("תקציר_מצב", "")
        recs = p.get("המלצות", [])
        good = p.get("מה_עובד_טוב", [])
        warnings = p.get("אזהרות_כלליות", [])

        msg = f"*🔍 דוח המכוון*\n\n*תקציר:* {summary[:300]}\n\n"

        if recs:
            recs_sorted = sorted(recs, key=lambda r: r.get("עדיפות", 0), reverse=True)
            msg += f"*🎯 {len(recs_sorted)} המלצות (לפי עדיפות):*\n\n"
            for i, r in enumerate(recs_sorted[:8], 1):
                pri = r.get("עדיפות", 0)
                typ = r.get("סוג", "?")
                title = r.get("כותרת", "")[:80]
                action = r.get("פעולה_מדויקת", "")[:200]
                risk = r.get("סיכון_שבירה", "?")
                msg += f"*{i}.* [{pri}/10] {typ}\n_{title}_\n→ {action}\n(סיכון שבירה: {risk})\n\n"

        if good:
            msg += "*✅ מה עובד טוב:*\n"
            for g in good[:5]:
                msg += f"• {g[:200]}\n"
            msg += "\n"

        if warnings:
            msg += "*⚠️ אזהרות:*\n"
            for w in warnings[:5]:
                msg += f"• {w[:200]}\n"

        send_message(chat_id, msg)

        # 🤖 אוטו-יישום - confidence-gated + שופט
        try:
            result = apply_recommendations(p)
            applied = result["applied"]
            rejected = result["rejected"]
            manual = result["manual"]

            # סיכום סופי קצר וברור
            summary_lines = []
            if applied:
                summary_lines.append(f"✅ יושמו: {len(applied)}")
            if rejected:
                summary_lines.append(f"🚫 נדחו: {len(rejected)}")
            if manual:
                summary_lines.append(f"🙋 צריך אישור ידני: {len(manual)}")
            if not summary_lines:
                summary_lines.append("ℹ️ אין שום פעולה")

            header = "📋 *מה עשיתי בפועל:*\n" + " | ".join(summary_lines)
            send_message(chat_id, header)

            if applied:
                send_message(chat_id, "✅ *שינויים שיושמו:*\n\n" + "\n".join(applied), parse_mode="")
            if rejected:
                send_message(chat_id, "🚫 *נדחו (לא יושמו):*\n\n" + "\n".join(rejected), parse_mode="")
            if manual:
                send_message(chat_id, "🙋 *דורש אישור ידני:*\n\n" + "\n".join(manual), parse_mode="")
        except Exception as e:
            send_message(chat_id, f"⚠️ אוטו-apply נכשל: {e}")

    threading.Thread(target=_run, daemon=True).start()


FREETEXT_SYSTEM = """אתה עוזר אישי ידידותי של סוחר קריפטו שלא מבין הרבה במסחר.
המשתמש שלך לא מבין מונחים טכניים. תדבר כמו חבר שמסביר במילים פשוטות.

יש לך גישה ל:
1. סטטיסטיקות עסקאות (Win Rate, P/L, וכו')
2. עסקאות אחרונות (סוגים, תוצאות, לקחים)
3. לקחים שנצברו במערכת

איך אתה עונה:
- **השתמש באנלוגיות מהחיים** (כדורגל, בישול, הימור, מטבע) כשמסבירים דברים טכניים
- מילים פשוטות, לא מונחים מקצועיים
- אם חייב להזכיר מונח טכני - תסביר אותו במשפט
- 2-5 משפטים, לא יותר
- בעברית טבעית
- אם המשתמש מתבלבל - תיתן דוגמה
- אם אין דאטה - "אין לי מספיק נתונים" + הצעה מה לעשות

דוגמאות לסגנון:
❌ "ה-confidence של הלקח 2/2 הוא לא סטטיסטית מובהק"
✅ "הלקח עבד פעמיים מתוך פעמיים - אבל זה כמו להגיד שמטבע 'תמיד נופל על עץ' אחרי שהטלת אותו פעמיים. צריך הרבה יותר נתונים כדי להיות בטוחים"

❌ "Profit Factor של 1.5 משמעו שעבור כל דולר הפסד, הרווחת $1.5"
✅ "על כל $1 שהפסדנו, הרווחנו $1.5 - כמו לקנות 3 כרטיסי הגרלה ב-$1 ולנצח $1.50 על אחד"

אל תוסיף JSON. רק טקסט טבעי, חברותי, פשוט להבנה."""


def cmd_freetext(chat_id: int, text: str):
    """כשהמשתמש כותב משהו שאינו פקודה - עונים בעזרת LLM עם הקשר."""
    trades = load_trades()
    lessons = load_lessons()
    stats = get_stats()
    closed = [t for t in trades if t.get("status") == "closed"]

    last_5 = [{
        "type": (t.get("hunter_setup") or {}).get("סוג"),
        "direction": (t.get("decision") or {}).get("החלטה"),
        "outcome": (t.get("simulation") or {}).get("outcome"),
        "pnl_pct": (t.get("simulation") or {}).get("pnl_pct"),
    } for t in closed[-5:]]

    recent_lessons = [{
        "rule": (l.get("rule", "") or "")[:200],
        "confidence": l.get("confidence", 1),
    } for l in lessons[-5:]]

    context = f"""סטטיסטיקה:
{stats}

5 עסקאות אחרונות:
{last_5}

5 לקחים אחרונים:
{recent_lessons}

שאלת המשתמש: {text}"""

    send_message(chat_id, "🤔 חושב...")
    resp = call_claude(context, FREETEXT_SYSTEM, model="haiku", timeout=60)
    if resp.is_error:
        send_message(chat_id, f"❌ לא הצלחתי לענות: {resp.error_message}")
        return
    answer = (resp.raw_result or "").strip()
    if not answer:
        answer = "לא הצלחתי לגבש תשובה."
    send_message(chat_id, answer)


def cmd_account(chat_id: int):
    acc = load_account()
    progress = ((acc["current_balance"] - acc["starting_balance"]) /
                max(acc["target_balance"] - acc["starting_balance"], 1) * 100)
    bar_filled = int(max(0, min(progress, 100)) / 5)
    bar = "█" * bar_filled + "░" * (20 - bar_filled)

    growth = ((acc["current_balance"] / max(acc["starting_balance"], 1)) - 1) * 100
    risk_per_trade = round(acc["current_balance"] * 0.01, 2)
    wr = (acc["wins"] / max(acc["trades_taken"], 1)) * 100 if acc["trades_taken"] else 0

    msg = f"""💰 *מצב חשבון - שלב {acc['stage']}*

_{acc['stage_description']}_

יתרה: *${acc['current_balance']:,.2f}*
התחלה: ${acc['starting_balance']:,.2f}
יעד: ${acc['target_balance']:,.0f}

`{bar}` {progress:.1f}%
צמיחה: {growth:+.1f}%

🎯 סיכון לעסקה: ${risk_per_trade}
📈 שיא: ${acc['highest_balance']:,.2f}
📉 שפל: ${acc['lowest_balance']:,.2f}

🏆 ניצחונות/הפסדים: {acc['wins']}W / {acc['losses']}L
Win Rate: {wr:.1f}%
סה"כ עסקאות: {acc['trades_taken']}"""

    # מעבר לקריטריונים סטטיסטיים במקום יעד יתרה
    from memory_store import check_advance_readiness
    readiness = check_advance_readiness()
    msg += "\n\n*🎯 קריטריונים למעבר לשלב הבא:*\n"
    labels = {
        "trades_30plus": ("30+ עסקאות", lambda v: f"{v}/30"),
        "profit_factor_15plus": ("PF ≥ 1.5", lambda v: f"{v:.2f}"),
        "win_rate_50plus": ("Win Rate ≥ 50%", lambda v: f"{v}%"),
        "drawdown_under_15": ("Drawdown < 15%", lambda v: f"{v}%"),
    }
    for key, c in readiness["criteria"].items():
        mark = "✅" if c["met"] else "❌"
        label, fmt = labels.get(key, (key, str))
        msg += f"{mark} {label}: {fmt(c['value'])}\n"
    if readiness["ready"]:
        msg += "\n🎉 *מוכן ל-/advance!*"

    send_message(chat_id, msg, parse_mode="")


def cmd_advance(chat_id: int):
    from memory_store import advance_to_next_stage, check_advance_readiness
    acc = load_account()
    if acc["stage"] >= 3:
        send_message(chat_id, "כבר בשלב הסופי (מסחר אמיתי).")
        return

    # קריטריונים סטטיסטיים במקום יעד יתרה
    readiness = check_advance_readiness()
    if not readiness["ready"]:
        lines = ["⚠️ *עוד לא מוכן לשלב הבא*\n"]
        for name, c in readiness["criteria"].items():
            mark = "✅" if c["met"] else "❌"
            label = {
                "trades_30plus": "30+ עסקאות סגורות",
                "profit_factor_15plus": "Profit Factor ≥ 1.5",
                "win_rate_50plus": "Win Rate ≥ 50%",
                "drawdown_under_15": "Drawdown < 15%",
            }.get(name, name)
            lines.append(f"{mark} {label} (כרגע: {c['value']})")
        lines.append(f"\n{readiness['summary']}")
        send_message(chat_id, "\n".join(lines), parse_mode="")
        return

    new_acc = advance_to_next_stage()
    send_message(
        chat_id,
        f"""🎉 *התקדמת לשלב {new_acc['stage']}!*

{new_acc['stage_description']}

יתרה: ${new_acc['current_balance']:,.2f}
יעד חדש: ${new_acc['target_balance']:,.0f}

בהצלחה!""",
    )


def cmd_live_check(chat_id: int):
    """בדיקה חיה - מנתח את השוק עכשיו ומתעד setup אם נמצא."""
    send_message(chat_id, "🔴 *בדיקה חיה...*\nמושך נר אחרון, מנתח, מפעיל ועדה...")

    def _run():
        try:
            result = check_live_market(verbose=False)
            status = result.get("status")

            if status == "no_setup":
                summary = result["summary"]
                msg = f"""⊘ *אין setup ראוי כרגע*

📊 מצב שוק חי:
• מחיר: ${summary['price']:,.2f}
• RSI: {summary['indicators']['rsi']}
• טרנד: {summary['trend']}
• BB Width: {summary['indicators']['bb_width']}

הערכת הצייד: {result.get('hunter_assessment', '')[:300]}"""

            elif status == "rejected":
                msg = f"""⚠️ *הצייד מצא setup אבל הוועדה דחתה*

setup: {result['setup'].get('סוג')} {result['setup'].get('כיוון')}
סיבת דחייה: {result.get('reason', '')[:300]}"""

            elif status == "new_recommendation":
                r = result["rec"]
                msg = f"""🎯 *המלצה חיה חדשה!*

{r['direction']} | {r['setup_type']} (ציון {r['setup_score']})
💵 כניסה: ${r['entry']:,.2f}
🛑 סטופ: ${r['stop']:,.2f}
🎯 יעד: ${r['target_1']:,.2f}
{'🎯 יעד 2: $' + f"{r['target_2']:,.2f}" if r.get('target_2') else ''}

ID: `{r['id']}`
{'🎭 כניסה דרך פרקליט' if r.get('via_advocate') else ''}

⚠️ *זה במצב פסיבי - לא לבצע בפועל בבייננס!*
המערכת תעקוב ותעדכן כשהיא נסגרת."""
            else:
                msg = f"⚠️ סטטוס לא ידוע: {status}\n{result.get('error', '')}"

            send_message(chat_id, msg, parse_mode="")
        except Exception as e:
            send_message(chat_id, f"❌ /live_check נכשל: {e}")

    threading.Thread(target=_run, daemon=True).start()


def cmd_live_status(chat_id: int):
    """מציג את כל ההמלצות הפתוחות + מצב שלב 2."""
    open_recs = load_open_recs()
    acc = load_account()

    if acc.get("stage", 1) < 2:
        send_message(chat_id, "ℹ️ עוד לא בשלב 2 (לייב). שלח /advance להתקדם.")
        return

    msg = f"""📡 *מצב לייב (שלב {acc['stage']})*

💰 יתרה: ${acc['current_balance']:,.2f}
🎯 יעד: ${acc['target_balance']:,.0f}
📊 עסקאות שלב 2: {acc['trades_taken']} ({acc['wins']}W/{acc['losses']}L)

🔴 *המלצות פתוחות: {len(open_recs)}*"""

    if open_recs:
        for r in open_recs:
            opened = r['opened_at'][:16].replace('T', ' ')
            msg += (f"\n\n• {r['direction']} {r['setup_type']} (`{r['id']}`)\n"
                    f"  נפתח: {opened}\n"
                    f"  כניסה ${r['entry']:,.2f} | סטופ ${r['stop']:,.2f} | יעד ${r['target_1']:,.2f}")
    else:
        msg += "\nאין המלצות פתוחות כרגע. שלח /live_check לסריקה חדשה."

    send_message(chat_id, msg, parse_mode="")


def _daily_summary_loop():
    """כל יום ב-23:00 שעון ישראל - שולח תקציר יומי לטלגרם."""
    last_sent_date = None
    while True:
        try:
            time.sleep(60)  # בודק כל דקה
            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            # רץ ב-23:00-23:01 פעם אחת ביום
            if now.hour == 23 and now.minute == 0 and last_sent_date != today_str:
                last_sent_date = today_str
                authorized = get_authorized_chat_id()
                if not authorized:
                    continue

                # מסנן עסקאות היום
                trades = load_trades()
                today_trades = [t for t in trades
                                if t.get("status") == "closed"
                                and t.get("session") == today_str
                                and t.get("live_mode")]

                wins = [t for t in today_trades if (t.get("simulation") or {}).get("pnl_pct", 0) > 0]
                losses = [t for t in today_trades if (t.get("simulation") or {}).get("pnl_pct", 0) <= 0]
                net_pnl = sum((t.get("simulation") or {}).get("pnl_pct", 0) for t in today_trades)

                # לקחים חדשים היום
                lessons = load_lessons()
                today_lessons = [l for l in lessons
                                 if l.get("created_at", "").startswith(today_str)]

                # המלצות פתוחות
                open_recs = load_open_recs()

                acc = load_account()
                stats = get_stats()

                msg = f"""🌙 *תקציר יומי - {today_str}*

💰 *חשבון:*
• יתרה: ${acc['current_balance']:,.2f}
• שינוי היום: {net_pnl:+.2f}%
• מצטבר (שלב {acc['stage']}): ${acc['current_balance'] - acc['starting_balance']:+,.2f}

📊 *פעילות היום:*
• המלצות נסגרו: {len(today_trades)} ({len(wins)}W / {len(losses)}L)
• Win Rate היום: {(len(wins) / max(len(today_trades), 1) * 100):.0f}%
• המלצות עדיין פתוחות: {len(open_recs)}

🧠 *למידה:*
• לקחים חדשים: {len(today_lessons)}

🎯 *סטטוס כללי:*
• עסקאות סגורות בסה"כ: {stats.get('closed_trades', 0)}
• Win Rate כללי: {stats.get('win_rate_pct', 0)}%
• Profit Factor: {stats.get('profit_factor', '?')}"""

                if today_trades:
                    best = max(today_trades, key=lambda t: (t.get("simulation") or {}).get("pnl_pct", 0))
                    worst = min(today_trades, key=lambda t: (t.get("simulation") or {}).get("pnl_pct", 0))
                    msg += f"""

✨ *הטוב/הרע ביותר:*
🏆 ניצחון: {best.get('hunter_setup', {}).get('סוג')} {best.get('decision', {}).get('החלטה')} ({(best.get('simulation') or {}).get('pnl_pct', 0):+.2f}%)
💔 הפסד: {worst.get('hunter_setup', {}).get('סוג')} {worst.get('decision', {}).get('החלטה')} ({(worst.get('simulation') or {}).get('pnl_pct', 0):+.2f}%)"""

                if today_lessons:
                    msg += "\n\n💡 *לקח עיקרי שנלמד:*\n" + (today_lessons[-1].get("rule", "")[:200])

                send_message(authorized, msg, parse_mode="")
        except Exception as e:
            print(f"⚠️ daily_summary: {e}")


def _live_auto_scanner_loop():
    """רץ ברקע - סורק את השוק החי אוטומטית כל 15 דק', פותח המלצות חדשות."""
    MAX_OPEN_RECS = 3  # לא יותר מ-3 המלצות פתוחות בו-זמנית
    # מחכה 60 שניות לפני התחלה ראשונה כדי שהבוט יספיק להעלות
    time.sleep(60)
    while True:
        try:
            authorized = get_authorized_chat_id()
            acc = load_account()
            if not authorized or acc.get("stage", 1) < 2:
                time.sleep(900)
                continue

            # אם יש כבר MAX המלצות פתוחות - מחכים
            open_recs = load_open_recs()
            if len(open_recs) >= MAX_OPEN_RECS:
                print(f"[auto-scan] {len(open_recs)} המלצות פתוחות (max {MAX_OPEN_RECS}) - מדלג")
                time.sleep(900)
                continue

            # סורקים
            print(f"[auto-scan] {datetime.now().strftime('%H:%M:%S')} - בודק שוק חי...")
            result = check_live_market(verbose=False)

            if result.get("status") == "new_recommendation":
                r = result["rec"]
                msg = f"""🤖 *Auto-Scanner מצא המלצה חדשה!*

{r['direction']} | {r['setup_type']} (ציון {r['setup_score']})
💵 כניסה: ${r['entry']:,.2f}
🛑 סטופ: ${r['stop']:,.2f}
🎯 יעד: ${r['target_1']:,.2f}

ID: `{r['id']}`

⚠️ פסיבי - לא לבצע. המערכת תעדכן בסגירה."""
                send_message(authorized, msg, parse_mode="")

            time.sleep(900)  # 15 דקות בין סריקות
        except Exception as e:
            print(f"⚠️ auto_scanner: {e}")
            time.sleep(900)


def _live_monitor_loop():
    """רץ ברקע - בודק המלצות פתוחות כל 5 דקות, שולח התראות בטלגרם."""
    while True:
        try:
            time.sleep(300)  # 5 דקות
            authorized = get_authorized_chat_id()
            if not authorized:
                continue
            # רק אם בשלב 2+
            acc = load_account()
            if acc.get("stage", 1) < 2:
                continue
            events = poll_open_recommendations(verbose=False)
            for e in events:
                rec = e["rec"]
                exit_info = e["exit"]
                emoji = "🟢" if exit_info["pnl_pct"] > 0 else "🔴"
                msg = f"""{emoji} *המלצה נסגרה: {rec['id']}*

{rec['direction']} {rec['setup_type']}
תוצאה: {exit_info['outcome']} @ ${exit_info['exit_price']:,.2f}
P/L נטו: {exit_info['pnl_pct']:+.2f}%
💵 יתרה אחרי: ${e['balance_after']:,.2f}"""
                if e.get("lesson"):
                    msg += f"\n\n💡 לקח חדש: {e['lesson'][:200]}"
                send_message(authorized, msg, parse_mode="")
        except Exception as e:
            print(f"⚠️ live_monitor_loop: {e}")


def cmd_help(chat_id: int):
    msg = """🤖 *Guy Trade*

פקודות:
/run     🚀 הפעל סשן חדש (1 עסקה)
/status  📊 מה קורה כרגע
/last    📋 פרטי העסקה האחרונה
/stats   📈 סטטיסטיקה כללית
/account 💰 מצב חשבון + יעד
/lessons 📚 לקחים אחרונים
/analyze 🔍 המכוון - דוח שיפורים למערכת
/advance 🎯 עבור לשלב הבא (אחרי הגעה ליעד)

🔴 *לייב (שלב 2):*
/live_check 🔴 סרוק שוק חי עכשיו
/live_status 📡 המלצות פתוחות + מצב לייב

/stop    🛑 עצור סשן רץ
/help    ❓ הצג את ההודעה הזו"""
    send_message(chat_id, msg)


# ─── ראוטר פקודות ────────────────────────────────────────────────

COMMANDS = {
    "/run": run_session_async,
    "/status": cmd_status,
    "/last": cmd_last,
    "/stats": cmd_stats,
    "/account": cmd_account,
    "/advance": cmd_advance,
    "/lessons": cmd_lessons,
    "/analyze": cmd_analyze,
    "/live_check": cmd_live_check,
    "/live_status": cmd_live_status,
    "/stop": stop_session,
    "/help": cmd_help,
}


def handle_message(message: dict):
    chat_id = message["chat"]["id"]
    text = (message.get("text") or "").strip()

    if not text:
        return

    # אימות בפעם הראשונה
    authorized = get_authorized_chat_id()
    if authorized is None:
        set_authorized_chat_id(chat_id)
        send_message(
            chat_id,
            f"✅ *אימות הושלם*\nChat ID שלך ({chat_id}) נשמר. רק אתה תוכל לפקד מעכשיו.",
        )
        cmd_help(chat_id)
        return

    if chat_id != authorized:
        send_message(chat_id, "⛔ אין לך הרשאה.")
        return

    cmd = text.split()[0].lower()
    if cmd == "/start":
        cmd_help(chat_id)
    elif cmd in COMMANDS:
        try:
            COMMANDS[cmd](chat_id)
        except Exception as e:
            send_message(chat_id, f"❌ שגיאה בפקודה {cmd}: {e}")
    else:
        # טקסט חופשי - שולחים ל-LLM שעונה לפי הנתונים
        try:
            cmd_freetext(chat_id, text)
        except Exception as e:
            send_message(chat_id, f"❌ שגיאה: {e}\n\nשלח /help לרשימת פקודות.")


# ─── לולאת polling ראשית ───────────────────────────────────────

def main():
    print("=" * 60)
    print("🤖 Guy Trade Bot")
    print("=" * 60)
    print(f"Token: {TOKEN[:20]}...")

    auth = get_authorized_chat_id()
    if auth:
        print(f"Authorized chat_id: {auth}")
    else:
        print("עדיין לא אומת - שלח /start לבוט בטלגרם.")

    # 1. Auto-scanner - סורק שוק כל 15 דק' ומחפש setups
    scanner_thread = threading.Thread(target=_live_auto_scanner_loop, daemon=True)
    scanner_thread.start()
    print("🤖 Auto-scanner פעיל - סורק שוק חי כל 15 דק'.")

    # 2. Monitor - בודק המלצות פתוחות כל 5 דק' (stop/target/timeout)
    monitor_thread = threading.Thread(target=_live_monitor_loop, daemon=True)
    monitor_thread.start()
    print("🔴 Live monitor פעיל - בודק המלצות פתוחות כל 5 דק'.")

    # 3. Daily summary - תקציר יומי ב-23:00
    summary_thread = threading.Thread(target=_daily_summary_loop, daemon=True)
    summary_thread.start()
    print("🌙 Daily summary פעיל - תקציר יומי ב-23:00.")

    print("\nProgressing... Ctrl+C לעצירה.")
    print("=" * 60)

    offset = 0
    while True:
        try:
            updates = get_updates(offset)
            for update in updates:
                offset = update["update_id"] + 1
                if "message" in update:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] "
                          f"{update['message']['chat']['id']} → "
                          f"{update['message'].get('text', '?')}")
                    handle_message(update["message"])
        except KeyboardInterrupt:
            print("\nעוצר את הבוט.")
            break
        except Exception as e:
            print(f"שגיאה בלולאה: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
