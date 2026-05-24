"""
בדיקה מקצה לקצה של הוועדה המלאה:
דאטה חי → 3 סוכנים במקביל → ראש צוות → החלטה.

הרצה: python3 scripts/test_committee.py
"""
import sys
import json
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.binance_client import BinanceClient
from data.indicators import market_summary
from agents.orchestrator import run_committee


def main():
    print("=" * 72)
    print("בדיקת ועדת המסחר המלאה - BTC/USDT 15m")
    print("=" * 72)

    print("\n[שלב 1] משיכת דאטה מ-Binance...")
    client = BinanceClient()
    df = client.get_klines("BTCUSDT", "15m", limit=250)
    print(f"  ✓ נמשכו {len(df)} נרות")
    print(f"  ✓ נר אחרון: {df.iloc[-1]['open_time']}")

    print("\n[שלב 2] חישוב אינדיקטורים ובניית תקציר...")
    summary = market_summary(df)
    print(f"  ✓ מחיר: ${summary['price']:,.2f}")
    print(f"  ✓ RSI: {summary['indicators']['rsi']} | טרנד: {summary['trend']}")
    print(f"  ✓ ATR%: {summary['indicators']['atr_pct']}% | Volume ratio: {summary['indicators']['volume_ratio']}")

    print("\n[שלב 3] הפעלת הוועדה...")
    result = run_committee(summary, verbose=True)

    print("\n" + "=" * 72)
    print("החלטת ראש הצוות:")
    print("=" * 72)
    head = result["head_decision"]
    if head["is_error"]:
        print(f"❌ שגיאה: {head['error']}")
    elif head["parsed"]:
        print(json.dumps(head["parsed"], indent=2, ensure_ascii=False))
    else:
        print("[פלט גולמי - JSON לא נפרסר]")
        print(head["raw"])

    print("\n" + "=" * 72)
    print("סיכום ריצה:")
    print("=" * 72)
    print(f"זמן כולל:     {result['totals']['elapsed_sec']} שניות")
    print(f"עלות כוללת:   ${result['totals']['cost_usd']}")

    out_path = Path(__file__).parent.parent / "logs" / f"committee_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    print(f"\nריצה מלאה נשמרה ב: {out_path.relative_to(Path.cwd())}")


if __name__ == "__main__":
    main()
