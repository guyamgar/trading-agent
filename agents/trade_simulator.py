"""
סימולטור עסקאות - מקבל החלטת כניסה ומריץ אותה קדימה על דאטה היסטורי אמיתי.
מחזיר את התוצאה: hit target / hit stop / time-out, ומחיר היציאה הסופי.
"""
from typing import Dict, Optional
import pandas as pd


def simulate_trade(
    candles_after: pd.DataFrame,
    direction: str,
    entry_price: float,
    stop: float,
    target_1: float,
    target_2: Optional[float] = None,
    max_candles: int = 96,
) -> Dict:
    """
    מקבל DataFrame של נרות אחרי הכניסה ומסמלץ את העסקה.

    direction: "LONG" / "SHORT"
    candles_after: נרות אחרי נר הכניסה (לא כולל אותו)
    max_candles: עד כמה נרות להחזיק לפני סגירה אוטומטית (96 = 24h ב-15m)

    חוקי שמרנות: אם באותו נר גם stop וגם target נפגעו - נספור stop.
    """
    is_long = direction.upper() == "LONG"

    df = candles_after.head(max_candles).copy()
    if df.empty:
        return {
            "outcome": "no_data",
            "exit_price": entry_price,
            "exit_idx": None,
            "candles_held": 0,
            "pnl_pct": 0,
            "pnl_usd_per_unit": 0,
        }

    for i, candle in df.iterrows():
        high = float(candle["high"])
        low = float(candle["low"])

        stop_hit = (low <= stop) if is_long else (high >= stop)
        t1_hit = (high >= target_1) if is_long else (low <= target_1)
        t2_hit = False
        if target_2:
            t2_hit = (high >= target_2) if is_long else (low <= target_2)

        if stop_hit and t1_hit:
            # שניהם באותו נר - שמרנות, סופרים stop
            return _build_result("stop", stop, i, df, entry_price, is_long, candles_held=df.index.get_loc(i) + 1)

        if stop_hit:
            return _build_result("stop", stop, i, df, entry_price, is_long, candles_held=df.index.get_loc(i) + 1)

        if t2_hit and target_2:
            return _build_result("target_2", target_2, i, df, entry_price, is_long, candles_held=df.index.get_loc(i) + 1)

        if t1_hit:
            return _build_result("target_1", target_1, i, df, entry_price, is_long, candles_held=df.index.get_loc(i) + 1)

    # לא נפגע stop ולא target - סגירה במחיר הסיום של הנר האחרון
    last_close = float(df.iloc[-1]["close"])
    return _build_result("timeout", last_close, df.index[-1], df, entry_price, is_long, candles_held=len(df))


def _build_result(outcome: str, exit_price: float, exit_idx, df: pd.DataFrame,
                  entry_price: float, is_long: bool, candles_held: int) -> Dict:
    if is_long:
        gross_pnl_pct = (exit_price - entry_price) / entry_price * 100
        gross_pnl_usd = exit_price - entry_price
    else:
        gross_pnl_pct = (entry_price - exit_price) / entry_price * 100
        gross_pnl_usd = entry_price - exit_price

    # עמלות בורסה - import בתוך הפונקציה למניעת cycle
    try:
        from config import ROUND_TRIP_FEE_PCT
    except ImportError:
        ROUND_TRIP_FEE_PCT = 0.2

    # P/L נטו אחרי עמלות (החיסור באחוזים, כי העמלה היא % מהפוזיציה)
    net_pnl_pct = gross_pnl_pct - ROUND_TRIP_FEE_PCT
    fee_usd = entry_price * (ROUND_TRIP_FEE_PCT / 100)
    net_pnl_usd = gross_pnl_usd - fee_usd

    exit_time = df.loc[exit_idx, "open_time"] if exit_idx in df.index else None

    return {
        "outcome": outcome,
        "exit_price": round(exit_price, 2),
        "exit_idx": int(exit_idx) if exit_idx is not None else None,
        "exit_time": str(exit_time) if exit_time is not None else None,
        "candles_held": candles_held,
        "minutes_held": candles_held * 15,
        # תאימות לאחור - pnl_pct הוא net (זה מה שמעניין אותנו)
        "pnl_pct": round(net_pnl_pct, 3),
        "pnl_usd_per_unit": round(net_pnl_usd, 2),
        # gross למחקר ולהשוואה
        "gross_pnl_pct": round(gross_pnl_pct, 3),
        "gross_pnl_usd_per_unit": round(gross_pnl_usd, 2),
        "fee_pct": ROUND_TRIP_FEE_PCT,
        "fee_usd_per_unit": round(fee_usd, 2),
    }
