"""
הגדרות מרכזיות של מערכת הסוכנים
"""
from pathlib import Path

ROOT = Path(__file__).parent

SYMBOL = "BTCUSDT"  # default / back-compat (single-symbol callers)
SYMBOLS = ["BTCUSDT", "ETHUSDT"]  # auto-scanner מסתובב ביניהם
TIMEFRAME_ANALYSIS = "15m"
TIMEFRAME_TREND = "4h"

BINANCE_BASE_URL = "https://api.binance.com"

MEMORY_DIR = ROOT / "memory"
TRADES_FILE = MEMORY_DIR / "trades.json"
LESSONS_FILE = MEMORY_DIR / "lessons.json"
ACCOUNT_FILE = MEMORY_DIR / "account.json"
LOGS_DIR = ROOT / "logs"

RISK_PER_TRADE_PCT = 5.0  # פייפר אגרסיבי - ללמידה מהירה יותר
MIN_RISK_REWARD = 1.5
MAX_DRAWDOWN_PCT = 15.0

# עמלות בורסה - להחילו על כל P/L חישוב
# Binance Spot standard: 0.1% maker/taker. עם BNB: 0.075%
EXCHANGE_FEE_PCT = 0.1            # one-way (כניסה או יציאה)
ROUND_TRIP_FEE_PCT = EXCHANGE_FEE_PCT * 2  # 0.2% סה"כ כניסה + יציאה

# הון נומינלי לחישוב גודל פוזיציה (פייפר טרייד)
DEFAULT_ACCOUNT_SIZE_USD = 10000

CANDLES_FOR_ANALYSIS = 250

# ─── FAST_MODE - להפעיל כשעוברים ללייב (stage 2+) ───
# במצב פייפר (stage 1) הוועדה דנה לפני הכניסה - היא המורה.
# בלייב: הצייד מוצא setup → בדיקת סיכון מהירה (5s) → כניסה מיידית.
# הוועדה המלאה רצה אחרי בדיעבד ולומדת. ככה לא בורחות הזדמנויות.
FAST_MODE = False  # יופעל אוטומטית כשaccount.stage >= 2


# ─── טעינת overrides דינמיים שהמכוון/אוטו-apply שינו ───
def _load_overrides():
    import json
    ov_file = MEMORY_DIR / "overrides.json"
    if ov_file.exists():
        try:
            return json.loads(ov_file.read_text())
        except Exception:
            return {}
    return {}


_overrides = _load_overrides()
for _k, _v in _overrides.items():
    if _k in globals() and isinstance(globals()[_k], (int, float)):
        globals()[_k] = type(globals()[_k])(_v)
