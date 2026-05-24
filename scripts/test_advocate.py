"""
בדיקה ממוקדת של מנגנון פרקליט השטן + סימולציית Shadow.
בלי לחכות שהצייד ימצא setup - יוצרים מצב סינתטי ובודקים שהזרימה עובדת.
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.binance_client import BinanceClient
from data.indicators import market_summary
from agents.orchestrator import run_devil_advocate
from agents.trade_simulator import simulate_trade
from memory_store import save_trade, update_lesson_stats, load_lessons


def main():
    print("=" * 70)
    print("בדיקה ממוקדת: פרקליט השטן + Shadow Simulation")
    print("=" * 70)

    # נתונים אמיתיים מ-Binance לבסיס
    print("\n[1/4] מושך נתונים אמיתיים...")
    client = BinanceClient()
    df = client.get_klines("BTCUSDT", "15m", limit=300)
    summary = market_summary(df.head(250))
    df_future = df.iloc[250:300].reset_index(drop=True)
    print(f"    מחיר: ${summary['price']:,.2f}, RSI: {summary['indicators']['rsi']}")

    # setup סינתטי לבדיקה
    print("\n[2/4] יוצר setup סינתטי לבדיקה...")
    current_price = summary["price"]
    fake_setup = {
        "סוג": "Pullback",
        "כיוון": "LONG",
        "אזור_כניסה": {"מחיר_מ": current_price - 50, "מחיר_עד": current_price},
        "סטופ_מומלץ": current_price - 200,
        "יעדים_מומלצים": [current_price + 300, current_price + 500],
        "ציון_איכות": 7,
        "סיבה": "בדיקה - מצב Pullback קלאסי לטרנד עולה",
        "תוקף_עד_מחיר": f"בטל אם מתחת ל-{current_price - 250}",
    }
    print(f"    Setup: LONG @ ${fake_setup['אזור_כניסה']['מחיר_עד']:,.2f}, "
          f"סטופ ${fake_setup['סטופ_מומלץ']:,.2f}, יעד ${fake_setup['יעדים_מומלצים'][0]:,.2f}")

    # החלטת ועדה סינתטית "אין כניסה" שמסתמכת על לקח
    print("\n[3/4] מדמה החלטת ועדה לדחות...")
    fake_decision = {
        "החלטה": "אין כניסה",
        "סיבה_להחלטה": "וטו לפי כלל a1b2c3d4 - BBW < 0.01 = אסור LONG. הסטאפ עומד בקריטריון של הכלל ולכן נדחה.",
        "ביטחון_1_10": 8,
    }

    # 3 לקחים סינתטיים (כדי שהפרקליט יוכל לבדוק)
    lessons = load_lessons()[:5]  # 5 לקחים אחרונים מהזיכרון האמיתי
    if not lessons:
        # אם אין לקחים בזיכרון, ניצור סינתטיים
        lessons = [
            {
                "id": "a1b2c3d4",
                "rule": "כש-BBW < 0.01 - אסור להיכנס LONG (סחיטה קיצונית)",
                "trigger": "BB Width מתחת ל-0.01",
                "confidence": 1,
                "times_invoked": 0,
            }
        ]
    print(f"    {len(lessons)} לקחים זמינים בזיכרון")

    # מפעיל את הפרקליט
    print("\n[4/4] מפעיל פרקליט השטן (Claude Sonnet)...")
    advocate = run_devil_advocate(fake_setup, fake_decision, lessons, summary, verbose=True)

    advocate_parsed = advocate.get("parsed") or {}
    print("\n--- תגובת הפרקליט ---")
    print(json.dumps(advocate_parsed, indent=2, ensure_ascii=False))

    # סימולציית Shadow
    print("\n[5/5] סימולציית Shadow - מה היה קורה אם נכנסנו?")
    shadow_sim = simulate_trade(
        candles_after=df_future,
        direction=fake_setup["כיוון"],
        entry_price=float(fake_setup["אזור_כניסה"]["מחיר_עד"]),
        stop=float(fake_setup["סטופ_מומלץ"]),
        target_1=float(fake_setup["יעדים_מומלצים"][0]),
        target_2=float(fake_setup["יעדים_מומלצים"][1]),
        max_candles=50,
    )
    print(f"    תוצאה: {shadow_sim['outcome']}")
    print(f"    P/L תאורטי: {shadow_sim['pnl_pct']:+.2f}%")
    print(f"    יציאה: ${shadow_sim['exit_price']:,.2f}")

    committee_was_right = shadow_sim["pnl_pct"] <= 0
    print(f"\n--- ניתוח ---")
    print(f"הוועדה {'צדקה ✓' if committee_was_right else 'טעתה ✗'}")
    advocate_was_right = advocate_parsed.get("תקיפה_מוצלחת") != committee_was_right
    print(f"הפרקליט {'צדק ✓' if advocate_was_right else 'טעה ✗'}")

    # עדכון סטטיסטיקות לקחים
    print("\n[6/6] עדכון confidence של הלקחים שעמדו בפני הוועדה...")
    for lesson in lessons:
        lid = lesson.get("id")
        if lid:
            update_lesson_stats(
                lid,
                invoked=True,
                correct=committee_was_right,
                wrong=not committee_was_right,
            )
            print(f"    {lid}: invoked=True, {'+1 confidence' if committee_was_right else '-2 confidence'}")

    print("\n✅ הבדיקה הסתיימה - כל המנגנון פעל ללא שגיאות!")


if __name__ == "__main__":
    main()
