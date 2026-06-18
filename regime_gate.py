"""Regime gate: veto directional entries that fight a confirmed trend.

Pure function. Reads ONLY the market_summary already computed at decision time
(EMA-alignment trend + EMA50/EMA200), so it is identical in live and backtest
and introduces no look-ahead. Do NOT call live fetches from here.
"""
from typing import Optional


def regime_gate_veto(direction: str, market_summary: dict,
                     signal: str = "ema_cross") -> Optional[str]:
    """Return a veto reason if this entry fights a confirmed trend, else None.

    signal="ema_cross" (default, R1):
        LONG vetoed when ema_50 < ema_200 (death cross), regardless of trend label.
        SHORT vetoed when ema_50 > ema_200 (golden cross), regardless of trend label.
        If EMA values are absent, do NOT veto (safe default).

    signal="trend_and_cross" (old R0, preserved for comparison):
        LONG vetoed when trend=="יורד" AND ema_50 < ema_200.
        SHORT vetoed when trend=="עולה" AND ema_50 > ema_200.
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

    if signal == "ema_cross":
        # Veto purely on EMA cross; trend label is irrelevant.
        # If EMAs are absent, safe default is no veto.
        if not have_emas:
            return None
        if d == "LONG" and ema50 < ema200:
            return (f"REGIME GATE: LONG חסום — death cross (EMA50={ema50} < EMA200={ema200})")
        if d == "SHORT" and ema50 > ema200:
            return (f"REGIME GATE: SHORT חסום — golden cross (EMA50={ema50} > EMA200={ema200})")
        return None

    # signal == "trend_and_cross" (R0 behaviour)
    if d == "LONG" and trend == "יורד":
        if have_emas and ema50 < ema200:
            return (f"REGIME GATE: LONG חסום בירידה מאוששת (trend=יורד, EMA50={ema50} < EMA200={ema200})")
    if d == "SHORT" and trend == "עולה":
        if have_emas and ema50 > ema200:
            return (f"REGIME GATE: SHORT חסום בעלייה מאוששת (trend=עולה, EMA50={ema50} > EMA200={ema200})")
    return None
