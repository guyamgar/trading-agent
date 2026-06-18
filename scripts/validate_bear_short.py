"""Validate Bear Short by re-classifying the real closed SHORT trades with the new
regime-aware logic. GOAL: trades newly blessed as "Bear Short" have positive EV, and
shorts in non-downtrend regimes are NOT blessed (stay experimental/anti)."""
import json, os, sys, time
from datetime import datetime, timezone
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from strategies import classify_trade_intent
from data.binance_client import BinanceClient
from data.indicators import market_summary

TRADES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory", "trades.json")

def ms_epoch(s):
    return int(datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp() * 1000)

def main():
    trades = [t for t in json.load(open(TRADES))["trades"]
              if t.get("status") == "closed" and t.get("simulation", {}).get("pnl_pct") is not None
              and t["hunter_setup"].get("כיוון") == "SHORT"]
    client = BinanceClient()
    groups = defaultdict(list)
    for t in trades:
        setup = t["hunter_setup"].get("סוג")
        entry = float(t["decision"].get("כניסה") or 0)
        if not setup or entry <= 0:
            continue
        sym = "BTCUSDT" if entry > 10000 else "ETHUSDT"
        end = ms_epoch(t["timestamp_analyzed"])  # endTime inclusive on open_time → ends AT decision candle
        df = client.get_klines(sym, "15m", limit=250, end_time=end)
        time.sleep(0.08)
        if len(df) < 50:
            continue
        summary = market_summary(df)
        ts = datetime.strptime(t["timestamp_analyzed"], "%Y-%m-%d %H:%M:%S")
        intent = classify_trade_intent(ts, setup, "SHORT", summary)
        label = intent.get("strategy_name") or intent.get("kind")  # e.g. "Bear Short" / "NY Counter-Trend Short" / "experimental"
        groups[label].append(t["simulation"]["pnl_pct"])

    def stat(lst):
        if not lst:
            return "n=0"
        w = sum(1 for p in lst if p > 0)
        return f"n={len(lst):>3}  WR={100*w/len(lst):>5.1f}%  totPnl={sum(lst):>+7.2f}%  EV={sum(lst)/len(lst):>+6.3f}%"

    print("=== Bear Short validation — real SHORT trades re-classified (regime-aware) ===")
    print("GOAL: 'Bear Short' group EV>0; non-downtrend shorts land in 'experimental' (not blessed).")
    for label in sorted(groups, key=lambda k: -sum(groups[k])):
        print(f"  {str(label):26} {stat(groups[label])}")

if __name__ == "__main__":
    main()
