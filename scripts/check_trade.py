"""
הזרימה המלאה: מושך דאטה → בונה תקציר → מריץ את הצוות → מציג המלצה.

הרצה:
    cd ~/Desktop/Agents_markering/trading_agent
    python3 scripts/check_trade.py
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.binance_client import BinanceClient
from data.indicators import market_summary
from agents.orchestrator import run_committee
from config import SYMBOL, TIMEFRAME_ANALYSIS, CANDLES_FOR_ANALYSIS
from memory_store import get_stats


def main():
    print("=" * 70)
    print(f"בדיקת עסקה: {SYMBOL} @ {TIMEFRAME_ANALYSIS}")
    print("=" * 70)

    # 1) משיכת דאטה
    print(f"\n[1/4] מושך {CANDLES_FOR_ANALYSIS} נרות מ-Binance...")
    client = BinanceClient()
    df = client.get_klines(SYMBOL, TIMEFRAME_ANALYSIS, limit=CANDLES_FOR_ANALYSIS)
    print(f"    נמשכו {len(df)} נרות. מחיר נוכחי: ${float(df.iloc[-1]['close']):,.2f}")

    # 2) חישוב אינדיקטורים ותקציר
    print(f"\n[2/4] מחשב אינדיקטורים ובונה תקציר נומרי...")
    summary = market_summary(df)
    print(f"    RSI: {summary['indicators']['rsi']}")
    print(f"    טרנד: {summary['trend']}")
    print(f"    ATR%: {summary['indicators']['atr_pct']}")
    print(f"    Volume ratio: {summary['indicators']['volume_ratio']}")

    # 3) הרצת הוועדה
    print(f"\n[3/4] מפעיל את הוועדה הרב-סוכנית...")
    result = run_committee(summary, verbose=True)

    # 4) הצגת תוצאות
    print("\n" + "=" * 70)
    print(f"החלטה סופית (עלות כוללת: ${result['totals']['cost_usd']}, זמן: {result['totals']['elapsed_sec']}s)")
    print("=" * 70)

    head = result["head_decision"]
    if head["is_error"]:
        print(f"\n!! שגיאה אצל ראש הצוות: {head['error']}")
    elif head["parsed"]:
        print(json.dumps(head["parsed"], indent=2, ensure_ascii=False))
    else:
        print("[לא הצלחתי לפרסר JSON של ראש הצוות - הנה הפלט הגולמי:]")
        print(head["raw"])

    # 4.5) הצגת ניתוחים של כל סוכן (לדיבאג)
    print("\n" + "-" * 70)
    print("פירוט ניתוחי הסוכנים:")
    print("-" * 70)
    for name, advisor in result["advisors"].items():
        print(f"\n▸ {name}:")
        if advisor["is_error"]:
            print(f"  שגיאה: {advisor['error']}")
        elif advisor["parsed"]:
            print(json.dumps(advisor["parsed"], indent=2, ensure_ascii=False))
        else:
            print(advisor["raw"][:500])

    # 5) סטטיסטיקות
    stats = get_stats()
    print("\n" + "-" * 70)
    print(f"סטטיסטיקות מצטברות: {stats['closed_trades']} עסקאות סגורות, "
          f"Win Rate: {stats['win_rate_pct']}%, P/L: ${stats['total_pnl_usd']}")
    print("-" * 70)


if __name__ == "__main__":
    main()
