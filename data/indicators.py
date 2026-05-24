"""
חישוב אינדיקטורים טכניים על דאטה OHLCV
"""
import pandas as pd


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    מקבל DataFrame של נרות ומוסיף עמודות אינדיקטורים נפוצים.
    """
    df = df.copy()

    # EMA - ממוצעים נעים מעריכיים
    df["ema_9"] = df["close"].ewm(span=9, adjust=False).mean()
    df["ema_21"] = df["close"].ewm(span=21, adjust=False).mean()
    df["ema_50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema_200"] = df["close"].ewm(span=200, adjust=False).mean()

    # RSI - אינדיקטור מומנטום
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    df["rsi"] = 100 - (100 / (1 + rs))

    # MACD
    ema_12 = df["close"].ewm(span=12, adjust=False).mean()
    ema_26 = df["close"].ewm(span=26, adjust=False).mean()
    df["macd"] = ema_12 - ema_26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    # ATR - תנודתיות
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14).mean()
    df["atr_pct"] = (df["atr"] / df["close"]) * 100  # ATR כאחוז מהמחיר

    # Bollinger Bands
    sma_20 = df["close"].rolling(20).mean()
    std_20 = df["close"].rolling(20).std()
    df["bb_upper"] = sma_20 + (2 * std_20)
    df["bb_lower"] = sma_20 - (2 * std_20)
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / sma_20

    # Volume MA
    df["volume_ma_20"] = df["volume"].rolling(20).mean()
    df["volume_ratio"] = df["volume"] / df["volume_ma_20"]

    return df


def find_support_resistance(df: pd.DataFrame, lookback: int = 50, top_n: int = 3) -> dict:
    """
    מזהה רמות תמיכה והתנגדות בסיסיות לפי שיאים ושפלים מקומיים.
    """
    recent = df.tail(lookback)

    highs = recent.nlargest(top_n, "high")["high"].tolist()
    lows = recent.nsmallest(top_n, "low")["low"].tolist()

    return {
        "resistance_levels": sorted(set([round(h, 2) for h in highs]), reverse=True),
        "support_levels": sorted(set([round(l, 2) for l in lows])),
    }


def candle_window(df: pd.DataFrame, n: int = 40) -> list:
    """
    מחלץ N נרות אחרונים בפורמט קומפקטי עבור הצייד.
    כולל אינדיקטורים מצומצמים לכל נר כדי לחסוך טוקנים.
    """
    df_ind = add_indicators(df).tail(n)
    window = []
    for _, row in df_ind.iterrows():
        window.append({
            "t": str(row["open_time"])[:16],
            "o": round(float(row["open"]), 2),
            "h": round(float(row["high"]), 2),
            "l": round(float(row["low"]), 2),
            "c": round(float(row["close"]), 2),
            "v_ratio": round(float(row["volume_ratio"]), 2) if pd.notna(row["volume_ratio"]) else None,
            "rsi": round(float(row["rsi"]), 1) if pd.notna(row["rsi"]) else None,
            "ema9": round(float(row["ema_9"]), 0),
            "ema21": round(float(row["ema_21"]), 0),
        })
    return window


def market_summary(df: pd.DataFrame) -> dict:
    """
    מייצר תקציר מצב שוק נומרי שניתן להעביר לסוכן LLM.
    זה הדאטה שעובר ל-Claude - לא תמונה, אלא מספרים מדויקים.
    """
    df_ind = add_indicators(df)
    last = df_ind.iloc[-1]
    prev = df_ind.iloc[-2]

    levels = find_support_resistance(df_ind)

    trend = "עולה"
    if last["ema_9"] < last["ema_21"] < last["ema_50"]:
        trend = "יורד"
    elif last["ema_9"] > last["ema_21"] > last["ema_50"]:
        trend = "עולה"
    else:
        trend = "מעורבב"

    return {
        "timestamp": str(last["open_time"]),
        "price": round(float(last["close"]), 2),
        "candle": {
            "open": round(float(last["open"]), 2),
            "high": round(float(last["high"]), 2),
            "low": round(float(last["low"]), 2),
            "close": round(float(last["close"]), 2),
            "change_pct": round(float((last["close"] - prev["close"]) / prev["close"] * 100), 3),
        },
        "indicators": {
            "ema_9": round(float(last["ema_9"]), 2),
            "ema_21": round(float(last["ema_21"]), 2),
            "ema_50": round(float(last["ema_50"]), 2),
            "ema_200": round(float(last["ema_200"]), 2),
            "rsi": round(float(last["rsi"]), 2),
            "macd": round(float(last["macd"]), 4),
            "macd_hist": round(float(last["macd_hist"]), 4),
            "atr_pct": round(float(last["atr_pct"]), 3),
            "bb_width": round(float(last["bb_width"]), 4),
            "volume_ratio": round(float(last["volume_ratio"]), 2),
        },
        "trend": trend,
        "levels": levels,
    }
