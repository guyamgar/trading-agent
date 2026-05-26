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
    SYMBOL, SYMBOLS, TIMEFRAME_ANALYSIS, RISK_PER_TRADE_PCT,
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
    next_stage_name = {1: "שלב 2 (live data)", 2: "שלב 3 (כסף אמיתי!)"}.get(readiness["stage"], "?")
    msg += f"\n\n*🎯 קריטריונים למעבר ל{next_stage_name}:*\n"
    labels = {
        "trades_30plus": ("30+ עסקאות", lambda v: f"{v}/30"),
        "profit_factor_15plus": ("PF ≥ 1.5", lambda v: f"{v:.2f}"),
        "win_rate_50plus": ("Win Rate ≥ 50%", lambda v: f"{v}%"),
        "drawdown_under_15": ("Drawdown < 15%", lambda v: f"{v}%"),
        "live_trades_50plus": ("50+ עסקאות בלייב", lambda v: f"{v}/50"),
        "profit_factor_18plus": ("PF ≥ 1.8 (קשוח!)", lambda v: f"{v:.2f}"),
        "win_rate_55plus": ("Win Rate ≥ 55%", lambda v: f"{v}%"),
        "drawdown_under_10": ("Drawdown < 10%", lambda v: f"{v}%"),
        "days_7plus": ("7+ ימים בשלב 2", lambda v: f"{v} ימים"),
    }
    for key, c in readiness["criteria"].items():
        mark = "✅" if c["met"] else "❌"
        label, fmt = labels.get(key, (key, str))
        msg += f"{mark} {label}: {fmt(c['value'])}\n"
    if readiness["ready"]:
        if readiness["stage"] == 2:
            msg += "\n⚠️ מוכן ללייב אמיתי - **אבל מומלץ להמשיך עוד כדי לצבור יותר ביטחון**"
        else:
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

            sym_label = result.get("summary", {}).get("symbol") or SYMBOL

            if status == "no_setup":
                summary = result["summary"]
                msg = f"""⊘ *אין setup ראוי כרגע* — {sym_label}

📊 מצב שוק חי:
• מחיר: ${summary['price']:,.2f}
• RSI: {summary['indicators']['rsi']}
• טרנד: {summary['trend']}
• BB Width: {summary['indicators']['bb_width']}

הערכת הצייד: {result.get('hunter_assessment', '')[:300]}"""

            elif status == "rejected":
                msg = f"""⚠️ *הצייד מצא setup אבל הוועדה דחתה* — {sym_label}

setup: {result['setup'].get('סוג')} {result['setup'].get('כיוון')}
סיבת דחייה: {result.get('reason', '')[:300]}"""

            elif status == "filtered_quiet":
                summary = result["summary"]
                msg = f"""😴 *השוק שקט* — {sym_label}

לא הגיע לטריגרים שיצדיקו קריאה לצייד (חיסכון בעלויות).
• מחיר: ${summary['price']:,.2f}
• RSI: {summary['indicators']['rsi']}
• סיבה: {result.get('reason', '')}"""

            elif status == "hunter_error":
                msg = f"⚠️ *הצייד נכשל* — {sym_label}\n{result.get('error', '')[:300]}"

            elif status == "new_recommendation":
                r = result["rec"]
                msg = f"""🎯 *המלצה חיה חדשה!*

{r.get('symbol', SYMBOL)} | {r['direction']} | {r['setup_type']} (ציון {r['setup_score']})
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
    """כל יום ב-05:00 בוקר שעון ישראל - שולח תקציר 24 שעות אחרונות + השוואה ליום קודם."""
    from datetime import timedelta
    last_sent_date = None
    while True:
        try:
            time.sleep(60)
            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")

            # 05:30 - גיבוי יומי לGit (אחרי הסיכום)
            if now.hour == 5 and now.minute == 30:
                try:
                    backup_script = ROOT / "scripts" / "backup_to_git.sh"
                    if backup_script.exists():
                        proc = subprocess.run(
                            ["/bin/bash", str(backup_script)],
                            cwd=str(ROOT), capture_output=True, text=True, timeout=120,
                        )
                        auth_chat = get_authorized_chat_id()
                        if auth_chat and proc.returncode == 0:
                            send_message(auth_chat, f"💾 גיבוי יומי לGitHub הושלם", parse_mode="")
                except Exception as e:
                    print(f"backup error: {e}")

            # 05:00 - תקציר בוקר
            if now.hour == 5 and now.minute == 0 and last_sent_date != today_str:
                last_sent_date = today_str
                authorized = get_authorized_chat_id()
                if not authorized:
                    continue

                trades = load_trades()
                lessons = load_lessons()
                acc = load_account()
                stats = get_stats()
                open_recs = load_open_recs()

                # מסנן 24 שעות אחרונות (closed עם live_mode)
                cutoff_24h = now - timedelta(hours=24)
                cutoff_48h = now - timedelta(hours=48)

                def trade_time(t):
                    ts = (t.get("updated_at") or t.get("created_at") or "")
                    try:
                        return datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
                    except Exception:
                        return now

                last_24h = [t for t in trades
                            if t.get("status") == "closed" and t.get("live_mode")
                            and trade_time(t) >= cutoff_24h]
                prior_24h = [t for t in trades
                             if t.get("status") == "closed" and t.get("live_mode")
                             and cutoff_48h <= trade_time(t) < cutoff_24h]

                wins_today = [t for t in last_24h if (t.get("simulation") or {}).get("pnl_pct", 0) > 0]
                losses_today = [t for t in last_24h if (t.get("simulation") or {}).get("pnl_pct", 0) <= 0]
                pnl_today = sum((t.get("simulation") or {}).get("pnl_pct", 0) for t in last_24h)

                wins_prior = [t for t in prior_24h if (t.get("simulation") or {}).get("pnl_pct", 0) > 0]
                pnl_prior = sum((t.get("simulation") or {}).get("pnl_pct", 0) for t in prior_24h)

                wr_today = len(wins_today) / max(len(last_24h), 1) * 100
                wr_prior = len(wins_prior) / max(len(prior_24h), 1) * 100

                # לקחים חדשים ב-24 שעות
                today_lessons = [l for l in lessons
                                 if l.get("created_at", "") >= cutoff_24h.isoformat()]

                # השוואות
                def diff_arrow(curr, prev):
                    if prev == 0:
                        return ""
                    if curr > prev:
                        return f"📈 (+{curr - prev:.1f})"
                    elif curr < prev:
                        return f"📉 ({curr - prev:.1f})"
                    return "➖"

                # בניית הודעה
                msg = f"""☀️ *בוקר טוב! סיכום הלילה*
📅 {today_str}

💰 *החשבון שלך:*
• יתרה: ${acc['current_balance']:,.2f}
• שינוי 24h: {pnl_today:+.2f}%
• מצטבר בשלב {acc['stage']}: ${acc['current_balance'] - acc['starting_balance']:+,.2f}

📊 *פעילות 24 שעות אחרונות:*
• המלצות שנסגרו: {len(last_24h)} (אתמול: {len(prior_24h)})
• ניצחונות: {len(wins_today)} (אתמול: {len(wins_prior)})
• הפסדים: {len(losses_today)} (אתמול: {len(prior_24h) - len(wins_prior)})
• Win Rate: {wr_today:.0f}% {diff_arrow(wr_today, wr_prior)} (אתמול {wr_prior:.0f}%)
• P/L: {pnl_today:+.2f}% (אתמול {pnl_prior:+.2f}%)

🧠 *למידה:*
• לקחים חדשים: {len(today_lessons)}
• סה"כ לקחים: {len(lessons)}

🎯 *סטטוס כללי:*
• סה"כ עסקאות סגורות: {stats.get('closed_trades', 0)}
• Win Rate כללי: {stats.get('win_rate_pct', 0)}%
• Profit Factor: {stats.get('profit_factor', '?')}
• המלצות פתוחות כעת: {len(open_recs)}"""

                if last_24h:
                    best = max(last_24h, key=lambda t: (t.get("simulation") or {}).get("pnl_pct", 0))
                    worst = min(last_24h, key=lambda t: (t.get("simulation") or {}).get("pnl_pct", 0))
                    msg += f"""

⭐ *הטוב והרע מהלילה:*
🏆 {best.get('hunter_setup', {}).get('סוג', '?')} {best.get('decision', {}).get('החלטה', '?')}: {(best.get('simulation') or {}).get('pnl_pct', 0):+.2f}%
💔 {worst.get('hunter_setup', {}).get('סוג', '?')} {worst.get('decision', {}).get('החלטה', '?')}: {(worst.get('simulation') or {}).get('pnl_pct', 0):+.2f}%"""

                if today_lessons:
                    msg += f"\n\n💡 *לקח עיקרי מהלילה:*\n{today_lessons[-1].get('rule', '')[:250]}"

                # ניתוח שעות פעילות מההיסטוריה
                from collections import defaultdict
                all_closed_live = [t for t in trades
                                   if t.get("status") == "closed" and t.get("live_mode")]
                hour_stats = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0.0})
                for t in all_closed_live:
                    ts = t.get("timestamp_analyzed") or t.get("created_at", "")
                    try:
                        hour = int(ts[11:13]) if len(ts) >= 13 else None
                    except (ValueError, IndexError):
                        hour = None
                    if hour is None:
                        continue
                    pnl = (t.get("simulation") or {}).get("pnl_pct", 0)
                    hour_stats[hour]["count"] += 1
                    hour_stats[hour]["pnl"] += pnl
                    if pnl > 0:
                        hour_stats[hour]["wins"] += 1

                if hour_stats:
                    # 3 השעות עם הכי הרבה עסקאות
                    top_hours = sorted(hour_stats.items(),
                                       key=lambda x: -x[1]["count"])[:3]
                    msg += "\n\n⏰ *שעות הפעילות הכי גבוהה (מצטבר):*"
                    for hr, s in top_hours:
                        wr = s["wins"] / s["count"] * 100
                        emoji = "🟢" if wr >= 60 else "🟡" if wr >= 45 else "🔴"
                        msg += f"\n• {hr:02d}:00-{(hr+1)%24:02d}:00 - {s['count']} עסקאות, WR {wr:.0f}% {emoji}"

                    # השעה הכי רווחית (PF נטו)
                    best_hour = max(hour_stats.items(), key=lambda x: x[1]["pnl"])
                    if best_hour[1]["count"] >= 3 and best_hour[1]["pnl"] > 0:
                        msg += f"\n\n💎 שעה הכי רווחית: {best_hour[0]:02d}:00 ({best_hour[1]['pnl']:+.2f}% מצטבר, {best_hour[1]['count']} עסקאות)"

                if open_recs:
                    msg += "\n\n🔴 *פתוחות עכשיו:*"
                    for r in open_recs[:3]:
                        msg += f"\n• {r['direction']} {r['setup_type']} - כניסה ${r['entry']:,.2f}"

                # התקדמות ליעד
                progress = ((acc["current_balance"] - acc["starting_balance"]) /
                            max(acc["target_balance"] - acc["starting_balance"], 1) * 100)
                progress = max(0, min(100, progress))
                bar_filled = int(progress / 5)
                bar = "█" * bar_filled + "░" * (20 - bar_filled)
                msg += f"\n\n🎯 *התקדמות ליעד ${acc['target_balance']:,.0f}:*\n`{bar}` {progress:.1f}%"

                # ניצול חבילת Claude Max ($200)
                from memory_store import get_cost_summary
                costs = get_cost_summary()
                MONTHLY_BUDGET = 200.0
                pct_today = costs["today"] / (MONTHLY_BUDGET / 30) * 100  # יחס ליום ממוצע
                pct_month = costs["this_month"] / MONTHLY_BUDGET * 100
                budget_emoji = "🟢" if pct_month < 50 else "🟡" if pct_month < 80 else "🔴"

                msg += f"""

💸 *ניצול חבילת Claude ($200/חודש):*
• היום: ${costs['today']:.2f} ({pct_today:.0f}% מתקציב יומי ממוצע)
• אתמול: ${costs['yesterday']:.2f}
• חודש זה: ${costs['this_month']:.2f} ({pct_month:.1f}% מ-$200) {budget_emoji}
• יתרה צפויה: ${MONTHLY_BUDGET - costs['this_month']:.2f}"""

                msg += "\n\n☕ יום טוב!"

                send_message(authorized, msg, parse_mode="")
        except Exception as e:
            print(f"⚠️ daily_summary: {e}")


SYSTEM_STATE_FILE = ROOT / "memory" / "system_state.json"


def update_system_state(**kwargs):
    """מעדכן את קובץ הסטטוס של המערכת - מה ה-threads עושים עכשיו."""
    state = {}
    if SYSTEM_STATE_FILE.exists():
        try:
            import json as _json
            state = _json.loads(SYSTEM_STATE_FILE.read_text())
        except Exception:
            state = {}
    state.update(kwargs)
    state["updated_at"] = datetime.now().isoformat()
    try:
        import json as _json
        SYSTEM_STATE_FILE.write_text(_json.dumps(state, ensure_ascii=False, indent=2))
    except Exception:
        pass


def load_system_state() -> dict:
    if not SYSTEM_STATE_FILE.exists():
        return {}
    try:
        import json as _json
        return _json.loads(SYSTEM_STATE_FILE.read_text())
    except Exception:
        return {}


# ─── מצב למידה לילית (training mode) ──────────────────────────────
# כשהדגל פעיל: ה-auto-scanner החי נעצר, ובמקומו רץ לולאת learn_daily
# שעושה למידה היסטורית - עסקה אחרי עסקה, עם התראות בטלגרם.

TRAINING_MODE_FILE = ROOT / "memory" / "training_mode.json"
LEARN_SCRIPT = ROOT / "scripts" / "learn_daily.py"


def get_training_state() -> dict:
    if not TRAINING_MODE_FILE.exists():
        return {"enabled": False}
    try:
        import json as _json
        return _json.loads(TRAINING_MODE_FILE.read_text())
    except Exception:
        return {"enabled": False}


def set_training_state(**kwargs):
    state = get_training_state()
    state.update(kwargs)
    state["updated_at"] = datetime.now().isoformat()
    try:
        import json as _json
        TRAINING_MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
        TRAINING_MODE_FILE.write_text(_json.dumps(state, ensure_ascii=False, indent=2))
    except Exception:
        pass


def is_training_mode() -> bool:
    return bool(get_training_state().get("enabled"))


def _is_backup_window() -> bool:
    """05:25-05:35 - חלון הגיבוי היומי לGit. עוצרים סשני אימון כדי שלא יכתבו במקביל."""
    now = datetime.now()
    if now.hour == 5 and 25 <= now.minute <= 35:
        return True
    return False


def _training_loop():
    """רץ ברקע - כשמצב למידה פעיל, מריץ סשני learn_daily ברצף עד שיכבו."""
    import subprocess
    import signal
    import os
    time.sleep(75)  # אחרי שה-scanner מתעורר, כדי לא להתחיל ביחד
    while True:
        try:
            if not is_training_mode():
                time.sleep(15)
                continue

            authorized = get_authorized_chat_id()
            if not authorized:
                time.sleep(60)
                continue

            # מתחמקים מחלון הגיבוי - לא רוצים JSON corrupt
            if _is_backup_window():
                print("[train] בחלון גיבוי (05:25-05:35) - מחכה דקה")
                time.sleep(60)
                continue

            from memory_store import load_trades, load_lessons

            state = get_training_state()
            session_num = state.get("sessions_done", 0) + state.get("sessions_failed", 0) + 1
            session_start = datetime.now()
            print(f"[train] סשן {session_num} מתחיל ב-{session_start.strftime('%H:%M:%S')}")
            set_training_state(current_session_started=session_start.isoformat(), current_session_num=session_num)

            initial_closed = len([t for t in load_trades() if t.get("status") == "closed"])

            # זרם stdout/stderr לקובץ ולא לזיכרון - חוסך RAM ומאפשר לראות לוג
            session_log_path = ROOT / "logs" / "training_sessions.log"
            session_log_path.parent.mkdir(parents=True, exist_ok=True)
            result = None
            proc = None
            try:
                with session_log_path.open("a") as log_f:
                    log_f.write(f"\n\n===== סשן {session_num} @ {session_start.isoformat()} =====\n")
                    log_f.flush()
                    # start_new_session=True → הקבוצת תהליכים שלנו. ב-timeout הורגים את כל הקבוצה
                    # ולא רק את התהליך הישיר → לא יוצרים זומבי-Claude יתום
                    proc = subprocess.Popen(
                        [sys.executable, "-u", str(LEARN_SCRIPT)],
                        cwd=str(ROOT),
                        stdout=log_f, stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                    try:
                        returncode = proc.wait(timeout=1500)
                    except subprocess.TimeoutExpired:
                        # הורג את כל קבוצת התהליכים - כולל ילדי Claude CLI
                        try:
                            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                            time.sleep(3)
                            if proc.poll() is None:
                                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        try:
                            proc.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            pass
                        returncode = -1
                    log_f.write(f"===== exit={returncode} =====\n")
                elapsed_min = (datetime.now() - session_start).total_seconds() / 60
                success = (returncode == 0)
            except Exception as e:
                print(f"[train] שגיאה בהפעלת תהליך: {e}")
                elapsed_min = (datetime.now() - session_start).total_seconds() / 60
                success = False

            # קריאה חוזרת של training_state - אולי המשתמש כיבה/הפעיל מחדש בזמן הריצה
            current_state = get_training_state()
            still_on = bool(current_state.get("enabled"))

            if success:
                trades = load_trades()
                closed = [t for t in trades if t.get("status") == "closed"]
                new_trade = None
                if len(closed) > initial_closed:
                    new_trade = closed[-1]

                sessions_done = current_state.get("sessions_done", 0) + 1
                wins = current_state.get("wins", 0)
                losses = current_state.get("losses", 0)

                if new_trade:
                    sim = new_trade.get("simulation") or {}
                    pnl = sim.get("pnl_pct", 0)
                    setup = new_trade.get("hunter_setup") or {}
                    decision = new_trade.get("decision") or {}
                    coach = (new_trade.get("post_trade") or {}).get("coach") or {}
                    lesson = coach.get("לקח_חדש")
                    sym = new_trade.get("symbol", "BTCUSDT")

                    if pnl > 0:
                        wins += 1
                        emoji, verdict = "🟢", "ניצחון"
                    else:
                        losses += 1
                        emoji, verdict = "🔴", "הפסד"

                    acc = load_account()
                    msg = (
                        f"{emoji} *למידה - סשן {session_num} - {verdict}*\n\n"
                        f"📊 {sym} {setup.get('סוג', '?')} {decision.get('החלטה', '?')}\n"
                        f"💵 P/L: {pnl:+.2f}% (${sim.get('pnl_usd_per_unit', 0):+.2f})\n"
                        f"⏱ {elapsed_min:.1f} דק' (החזקה {sim.get('minutes_held', 0)} דק')\n"
                        f"💰 יתרה: ${acc['current_balance']:,.2f}\n"
                        f"📈 סה\"כ: {sessions_done} סשנים, {wins}W/{losses}L"
                    )
                    if lesson and lesson not in (None, "אין לקח חדש", "null"):
                        msg += f"\n\n💡 לקח: {lesson[:200]}"
                    send_message(authorized, msg, parse_mode="")

                set_training_state(
                    sessions_done=sessions_done,
                    wins=wins,
                    losses=losses,
                    current_session_started=None,
                    current_session_num=None,
                )
                print(f"[train] סשן {session_num} ✓ ({elapsed_min:.1f} דק')")
            else:
                sessions_failed = current_state.get("sessions_failed", 0) + 1
                set_training_state(
                    sessions_failed=sessions_failed,
                    current_session_started=None,
                    current_session_num=None,
                )
                # קוראים את סוף קובץ הלוג כדי להבין למה נכשל
                err_tail = ""
                try:
                    session_log_path = ROOT / "logs" / "training_sessions.log"
                    if session_log_path.exists():
                        with session_log_path.open("rb") as f:
                            f.seek(0, 2)
                            size = f.tell()
                            f.seek(max(0, size - 600))
                            err_tail = f.read().decode("utf-8", errors="ignore")[-300:]
                except Exception:
                    pass
                if not err_tail:
                    err_tail = "timeout או שגיאה לא ידועה"
                print(f"[train] סשן {session_num} ✗ ({elapsed_min:.1f} דק') {err_tail[:100]}")
                # התראה רק אחת ל-3 כשלונות רצופים כדי לא להציף
                if sessions_failed % 3 == 0:
                    send_message(
                        authorized,
                        f"⚠️ למידה: {sessions_failed} כשלונות רצופים. אחרון: {err_tail[:150]}",
                        parse_mode="",
                    )

            # אם המשתמש כיבה בזמן הריצה - שלח אישור
            if not still_on:
                summary = get_training_state()
                send_message(
                    authorized,
                    f"⏸ *למידה נעצרה*\n\n"
                    f"✅ סשנים שהושלמו: {summary.get('sessions_done', 0)}\n"
                    f"🏆 ניצחונות: {summary.get('wins', 0)}\n"
                    f"💔 הפסדים: {summary.get('losses', 0)}\n"
                    f"❌ כשלונות: {summary.get('sessions_failed', 0)}\n\n"
                    f"חזרה ל-Auto-Scanner חי (BTC + ETH).",
                    parse_mode="",
                )

            # מנוחה קצרצרה לפני סשן הבא
            time.sleep(5)
        except Exception as e:
            print(f"⚠️ training_loop: {e}")
            time.sleep(30)


def cmd_learn_start(chat_id: int):
    """מפעיל מצב למידה - אימון רצוף על דאטה היסטורי, ה-auto-scanner של הלייב נפסק."""
    if is_training_mode():
        st = get_training_state()
        send_message(
            chat_id,
            f"⚠️ למידה כבר פעילה!\n"
            f"סשנים שהושלמו: {st.get('sessions_done', 0)}\n"
            f"ניצחונות: {st.get('wins', 0)} | הפסדים: {st.get('losses', 0)}\n\n"
            f"שלח /learn_stop כדי לעצור.",
            parse_mode="",
        )
        return

    set_training_state(
        enabled=True,
        started_at=datetime.now().isoformat(),
        sessions_done=0,
        sessions_failed=0,
        wins=0,
        losses=0,
    )
    send_message(
        chat_id,
        f"🌙 *למידת לילה התחילה*\n\n"
        f"רצה ברצף עד שתשלח /learn_stop\n"
        f"ה-auto-scanner של הלייב מושהה כל הזמן הזה.\n\n"
        f"תקבל התראה אחרי כל עסקה + עדכוני התקדמות.",
    )


def cmd_learn_stop(chat_id: int):
    """עוצר את למידת הלילה ומחזיר את ה-auto-scanner לפעולה."""
    if not is_training_mode():
        send_message(chat_id, "ℹ️ למידה לא פעילה כרגע. ה-auto-scanner של הלייב פעיל.", parse_mode="")
        return

    set_training_state(enabled=False, stopped_at=datetime.now().isoformat())
    st = get_training_state()
    send_message(
        chat_id,
        f"🛑 *מבקש עצירה*\n\n"
        f"הסשן הנוכחי (אם רץ) יושלם ואז ייעצר.\n"
        f"זה עלול לקחת עד 25 דק' (אורך סשן מקסימלי).\n\n"
        f"עד כה: {st.get('sessions_done', 0)} סשנים, "
        f"{st.get('wins', 0)}W/{st.get('losses', 0)}L\n\n"
        f"כשתסתיים תקבל סיכום, וה-auto-scanner של הלייב יחזור.",
    )


def _live_auto_scanner_loop():
    """רץ ברקע - סורק את השוק החי אוטומטית, מתחלף בין סימבולים (BTC/ETH)."""
    MAX_OPEN_RECS = 3  # לא יותר מ-3 המלצות פתוחות בו-זמנית
    # מחכה 60 שניות לפני התחלה ראשונה כדי שהבוט יספיק להעלות
    time.sleep(60)
    sym_idx = 0
    # מרווח בין סריקות: 15 דקות חלקי מספר הסימבולים, כך שכל סימבול נסרק כל 15 דק'
    interval = max(60, 900 // max(len(SYMBOLS), 1))
    while True:
        try:
            authorized = get_authorized_chat_id()
            acc = load_account()
            if not authorized or acc.get("stage", 1) < 2:
                time.sleep(interval)
                continue

            # אם מצב למידה פעיל - לא סורקים שוק חי
            if is_training_mode():
                update_system_state(scanner_status="paused_for_training")
                time.sleep(interval)
                continue

            # אם יש כבר MAX המלצות פתוחות - מחכים
            open_recs = load_open_recs()
            if len(open_recs) >= MAX_OPEN_RECS:
                print(f"[auto-scan] {len(open_recs)} המלצות פתוחות (max {MAX_OPEN_RECS}) - מדלג")
                time.sleep(interval)
                continue

            sym = SYMBOLS[sym_idx % len(SYMBOLS)]
            sym_idx += 1

            # סורקים
            print(f"[auto-scan] {datetime.now().strftime('%H:%M:%S')} - בודק שוק חי ({sym})...")
            update_system_state(
                scanner_status="running",
                scanner_started=datetime.now().isoformat(),
                scanner_symbol=sym,
            )
            result = check_live_market(verbose=False, symbol=sym)
            update_system_state(
                scanner_status="idle",
                last_scan_at=datetime.now().isoformat(),
                last_scan_symbol=sym,
                last_scan_result=result.get("status"),
                last_scan_price=result.get("summary", {}).get("price"),
            )

            if result.get("status") == "new_recommendation":
                r = result["rec"]
                msg = f"""🤖 *Auto-Scanner מצא המלצה חדשה!*

{r.get('symbol', sym)} | {r['direction']} | {r['setup_type']} (ציון {r['setup_score']})
💵 כניסה: ${r['entry']:,.2f}
🛑 סטופ: ${r['stop']:,.2f}
🎯 יעד: ${r['target_1']:,.2f}

ID: `{r['id']}`

⚠️ פסיבי - לא לבצע. המערכת תעדכן בסגירה."""
                send_message(authorized, msg, parse_mode="")

            time.sleep(interval)
        except Exception as e:
            print(f"⚠️ auto_scanner: {e}")
            time.sleep(interval)


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
            update_system_state(monitor_status="checking", monitor_started=datetime.now().isoformat())
            events = poll_open_recommendations(verbose=False)
            update_system_state(
                monitor_status="idle",
                last_monitor_at=datetime.now().isoformat(),
                last_monitor_closed=len(events),
                open_recs_count=len(load_open_recs()),
            )
            for e in events:
                rec = e["rec"]
                exit_info = e["exit"]
                emoji = "🟢" if exit_info["pnl_pct"] > 0 else "🔴"
                msg = f"""{emoji} *המלצה נסגרה: {rec['id']}*

{rec.get('symbol', SYMBOL)} | {rec['direction']} {rec['setup_type']}
תוצאה: {exit_info['outcome']} @ ${exit_info['exit_price']:,.2f}
P/L נטו: {exit_info['pnl_pct']:+.2f}%
💵 יתרה אחרי: ${e['balance_after']:,.2f}"""
                if e.get("lesson"):
                    msg += f"\n\n💡 לקח חדש: {e['lesson'][:200]}"
                send_message(authorized, msg, parse_mode="")
        except Exception as e:
            print(f"⚠️ live_monitor_loop: {e}")


def cmd_pulse(chat_id: int):
    """מציג מה כל ה-threads עושים עכשיו - חלון לזמן אמת."""
    from datetime import datetime as _dt
    state = load_system_state()
    open_recs = load_open_recs()
    acc = load_account()

    def fmt_ago(iso_str):
        if not iso_str:
            return "אף פעם"
        try:
            t = _dt.fromisoformat(iso_str)
            mins = (datetime.now() - t).total_seconds() / 60
            if mins < 1:
                return f"לפני {int(mins*60)} שניות"
            if mins < 60:
                return f"לפני {int(mins)} דק'"
            return f"לפני {int(mins/60)} שעות"
        except Exception:
            return iso_str[:16]

    scanner_st = state.get("scanner_status", "ממתין להפעלה ראשונה")
    monitor_st = state.get("monitor_status", "ממתין להפעלה ראשונה (5 דק' מהעלאה)")
    last_scan = fmt_ago(state.get("last_scan_at")) if state.get("last_scan_at") else "עוד לא רץ"
    last_mon = fmt_ago(state.get("last_monitor_at")) if state.get("last_monitor_at") else "עוד לא רץ"
    last_result = state.get("last_scan_result")
    last_price = state.get("last_scan_price")
    train_st = get_training_state()

    # תרגום מצב לעברית
    scanner_he = {
        "running": "🟢 סורק עכשיו (בודק שוק)",
        "idle": "🟡 ממתין לסריקה הבאה",
        "paused_for_training": "⏸ מושהה - מצב למידה דלוק",
    }.get(scanner_st, scanner_st)
    monitor_he = {
        "checking": "🟢 בודק עכשיו",
        "idle": "🟡 ממתין לבדיקה הבאה",
    }.get(monitor_st, monitor_st)

    # תיאור תוצאת הסריקה האחרונה
    result_meaning = {
        "filtered_quiet": "🟡 השוק שקט - דילגנו בלי לקרוא לצייד",
        "no_setup": "🟡 הצייד בדק - לא מצא setup ראוי",
        "rejected": "🟡 הצייד מצא אבל הוועדה דחתה",
        "new_recommendation": "🟢 נמצאה הזדמנות חדשה!",
        "hunter_error": "🔴 הצייד נכשל",
    }.get(last_result, last_result or "אין נתונים")

    msg = f"""💓 *פעימת לב המערכת*

🤖 *Auto-Scanner*
מצב: {scanner_he}
סריקה אחרונה: {last_scan}
תוצאה: {result_meaning}
{f'מחיר אז: ${last_price:,.2f}' if last_price else ''}

🔴 *Live Monitor*
מצב: {monitor_he}
בדיקה אחרונה: {last_mon}

📊 *מצב נוכחי*
מקום בשלב: {acc.get('stage', 1)} ({acc.get('mode', '?')})
יתרה: ${acc.get('current_balance', 0):,.2f}
המלצות פתוחות: {len(open_recs)}"""

    if train_st.get("enabled"):
        cur_num = train_st.get("current_session_num")
        cur_started = train_st.get("current_session_started")
        cur_line = ""
        if cur_num and cur_started:
            cur_line = f"\nסשן נוכחי: {cur_num} ({fmt_ago(cur_started)})"
        msg += (
            f"\n\n🌙 *מצב למידה דלוק*"
            f"\nסשנים שהושלמו: {train_st.get('sessions_done', 0)}"
            f" ({train_st.get('wins', 0)}W/{train_st.get('losses', 0)}L)"
            f"\nכשלונות: {train_st.get('sessions_failed', 0)}{cur_line}"
            f"\nשלח /learn_stop כדי לעצור."
        )

    if open_recs:
        msg += "\n\n*🎯 פתוחות עכשיו:*"
        for r in open_recs:
            opened = r['opened_at'][:16].replace('T', ' ')
            msg += f"\n• {r['direction']} {r['setup_type']} (`{r['id']}`)\n  {opened}, כניסה ${r['entry']:,.2f}"

    send_message(chat_id, msg, parse_mode="")


def cmd_help(chat_id: int):
    msg = """🤖 *Guy Trade - תפריט פקודות*

🔴 *מצב לייב (שלב 2 - אקטיבי!):*
/pulse — מה הבוט עושה ברגע זה (חינמי)
/live\\_status — המלצות פתוחות
/live\\_check — סריקה חיה ידנית עכשיו

💰 *חשבון וביצועים:*
/account — יתרה, יעד, התקדמות
/stats — Win Rate, P/L, Profit Factor
/last — פרטי העסקה האחרונה
/lessons — 5 לקחים אחרונים

🧠 *למידה ושיפור:*
/analyze — המכוון מנתח ומציע שיפורים
/advance — עבור לשלב הבא (כשהקריטריונים מתקיימים)

🌙 *למידת לילה (זמנים מתים):*
/learn\\_start — אימון רצוף על דאטה היסטורי (משבית auto-scan)
/learn\\_stop — עוצר למידה ומחזיר auto-scan ללייב

🎯 *פייפר היסטורי ידני:*
/run — סשן למידה יחיד

⚙️ *מערכת:*
/stop — עצור פעולה רצה
/help — תפריט זה

💬 *אפשר גם לכתוב שאלות חופשיות* כמו "כמה הרווחנו?" - הבוט עונה באנלוגיות פשוטות.

🤖 *רץ אוטומטית ברקע:*
• Auto-Scanner סורק כל 15 דק'
• Live Monitor בודק פתוחות כל 5 דק'
• תקציר יומי ב-23:00
• גיבוי GitHub ב-23:30"""
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
    "/livecheck": cmd_live_check,        # alias בלי קו תחתון
    "/live": cmd_live_check,             # alias קצר
    "/live_status": cmd_live_status,
    "/livestatus": cmd_live_status,      # alias בלי קו תחתון
    "/pulse": cmd_pulse,                  # מה קורה עכשיו ברקע
    "/learn_start": cmd_learn_start,      # מפעיל למידת לילה (משבית auto-scan)
    "/learnstart": cmd_learn_start,
    "/learn_stop": cmd_learn_stop,        # עוצר למידה ומחזיר auto-scan
    "/learnstop": cmd_learn_stop,
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
        # אם זה נראה כמו פקודה (מתחיל ב-/) - נסה למצוא טעות הקלדה
        if text.startswith("/"):
            import difflib
            close = difflib.get_close_matches(cmd, list(COMMANDS.keys()), n=1, cutoff=0.6)
            if close:
                send_message(
                    chat_id,
                    f"❓ לא הכרתי את `{cmd}`.\nאולי התכוונת ל-{close[0]}? תשלח שוב.",
                    parse_mode="",
                )
                return
            send_message(chat_id, f"❓ לא הכרתי את `{cmd}`. שלח /help לרשימה מלאה.", parse_mode="")
            return

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

    # 4. Training loop - רץ רק כשמצב למידה דלוק (/learn_start)
    training_thread = threading.Thread(target=_training_loop, daemon=True)
    training_thread.start()
    print("🎓 Training loop פעיל - ממתין ל-/learn_start.")

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
