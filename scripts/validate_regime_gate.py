"""Validate the regime gate against historical trades (spec §6 #1-#2).
For each closed trade, fetch the 250 15m candles ending at its decision candle,
recompute market_summary, apply regime_gate_veto, and tally vetoed-vs-passed
WR/PnL. GOAL: vetoed trades should be mostly LOSERS (gate catches the bad ones)
and the gate must NOT veto the winning range-period LONGs (no over-blocking).
"""
import json, os, sys, time
from datetime import datetime, timezone
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from regime_gate import regime_gate_veto
from data.binance_client import BinanceClient
from data.indicators import market_summary

TRADES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory", "trades.json")

def ms_epoch(s):
    return int(datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp() * 1000)

def main():
    trades = [t for t in json.load(open(TRADES))["trades"]
              if t.get("status") == "closed" and t.get("simulation", {}).get("pnl_pct") is not None]
    client = BinanceClient()
    buckets = defaultdict(lambda: {"vetoed": [], "passed": []})
    for t in trades:
        hs = t["hunter_setup"]; direction = hs.get("כיוון")
        entry = float(t["decision"].get("כניסה") or 0)
        if not direction or entry <= 0:
            continue
        sym = "BTCUSDT" if entry > 10000 else "ETHUSDT"
        end = ms_epoch(t["timestamp_analyzed"])  # endTime inclusive on open_time → window ends AT the decision candle
        df = client.get_klines(sym, "15m", limit=250, end_time=end)
        time.sleep(0.08)
        if len(df) < 50:
            continue
        summary = market_summary(df)
        veto = regime_gate_veto(direction, summary)
        live = "live" if t.get("live_mode") is True else "paper"
        key = (live, direction)
        pnl = t["simulation"]["pnl_pct"]
        buckets[key]["vetoed" if veto else "passed"].append(pnl)

    def stat(lst):
        if not lst:
            return "n=0"
        w = sum(1 for p in lst if p > 0)
        return f"n={len(lst):>3}  WR={100*w/len(lst):>5.1f}%  totPnl={sum(lst):>+7.2f}%"

    print("=== REGIME GATE VALIDATION (vetoed should be losers; passed should keep the winners) ===")
    for key in sorted(buckets):
        live, direction = key
        b = buckets[key]
        print(f"\n[{live} {direction}]")
        print(f"  VETOED by gate : {stat(b['vetoed'])}")
        print(f"  PASSED by gate : {stat(b['passed'])}")

if __name__ == "__main__":
    main()
