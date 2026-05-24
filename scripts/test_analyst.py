"""
בדיקה: מושך נרות חיים → תקציר נומרי → מריץ את המנתח הטכני דרך Claude CLI.
משתמש באוטנטיקציה הקיימת של המשתמש ב-Claude Code (לא צריך מפתח נפרד).

הרצה: python3 scripts/test_analyst.py
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.binance_client import BinanceClient
from data.indicators import market_summary
from agents.base import BaseAgent
from agents.prompts import TECHNICAL_ANALYST


class TechnicalAnalystAgent(BaseAgent):
    role = "המנתח הטכני"
    system_prompt = TECHNICAL_ANALYST
    model = "sonnet"


def main():
    print("=" * 70)
    print("בדיקת המנתח הטכני - BTC/USDT 15m")
    print("=" * 70)

    print("\n[1/3] מושך 250 נרות אחרונים מ-Binance...")
    client = BinanceClient()
    df = client.get_klines("BTCUSDT", "15m", limit=250)
    print(f"    נמשכו {len(df)} נרות.")
    print(f"    טווח: {df.iloc[0]['open_time']} → {df.iloc[-1]['open_time']}")

    print("\n[2/3] מחשב אינדיקטורים ובונה תקציר נומרי...")
    summary = market_summary(df)
    print(f"    מחיר: ${summary['price']:,.2f}")
    print(f"    RSI: {summary['indicators']['rsi']}")
    print(f"    טרנד: {summary['trend']}")
    print(f"    ATR%: {summary['indicators']['atr_pct']}%")

    print("\n[3/3] שולח לסוכן (Claude Sonnet דרך CLI)...")
    agent = TechnicalAnalystAgent()

    prompt = f"""הנה התקציר הנומרי של מצב השוק על BTC/USDT 15m:

```json
{json.dumps(summary, indent=2, ensure_ascii=False)}
```

תנתח לפי ההוראות שלך והחזר JSON בלבד."""

    response = agent.analyze(prompt)

    print(f"\n    עלות הקריאה: ${response.meta['cost_usd']:.4f}")
    print(f"    משך: {response.meta['duration_ms']}ms")

    print("\n" + "=" * 70)
    print("ניתוח המנתח הטכני:")
    print("=" * 70)
    if response.parsed:
        print(json.dumps(response.parsed, indent=2, ensure_ascii=False))
    else:
        print("[לא הצלחתי לפרסר JSON - פלט גולמי:]")
        print(response.raw_text)


if __name__ == "__main__":
    main()
