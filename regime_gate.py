"""Regime gate: veto directional entries that fight a confirmed trend.

Pure function. Reads ONLY the market_summary already computed at decision time
(EMA-alignment trend + EMA50/EMA200), so it is identical in live and backtest
and introduces no look-ahead. Do NOT call live fetches from here.
"""
from typing import Optional


def regime_gate_veto(direction: str, market_summary: dict,
                     require_ema_cross: bool = True) -> Optional[str]:
    """Return a veto reason if this entry fights a confirmed trend, else None.

    LONG is vetoed in a confirmed downtrend; SHORT in a confirmed uptrend.
    'Confirmed' = the EMA9/21/50 alignment trend agrees AND (when
    require_ema_cross) the longer EMA50-vs-EMA200 relationship agrees too.
    """
    if not direction:
        return None
    d = direction.upper()
    ms = market_summary or {}
    trend = ms.get("trend")
    ind = ms.get("indicators") or {}
    ema50 = ind.get("ema_50")
    ema200 = ind.get("ema_200")
    have_emas = ema50 is not None and ema200 is not None

    if d == "LONG" and trend == "יורד":
        if (not require_ema_cross) or (have_emas and ema50 < ema200):
            return (f"REGIME GATE: LONG חסום בירידה מאוששת "
                    f"(trend=יורד, EMA50={ema50} < EMA200={ema200})")
    if d == "SHORT" and trend == "עולה":
        if (not require_ema_cross) or (have_emas and ema50 > ema200):
            return (f"REGIME GATE: SHORT חסום בעלייה מאוששת "
                    f"(trend=עולה, EMA50={ema50} > EMA200={ema200})")
    return None
