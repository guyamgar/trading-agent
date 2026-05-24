"""
שכבת חיבור ל-Binance Public API
מושכים נרות OHLCV - לא צריך הרשמה או מפתח API לדאטה ציבורי
"""
import time
from typing import List, Dict, Optional
import requests
import pandas as pd

BASE_URL = "https://api.binance.com"


class BinanceClient:
    def __init__(self, base_url: str = BASE_URL):
        self.base_url = base_url
        self.session = requests.Session()

    def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 500,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        משיכת נרות (OHLCV) מ-Binance.
        interval: '1m', '3m', '5m', '15m', '30m', '1h', '4h', '1d' וכו'
        מחזיר DataFrame עם עמודות: open_time, open, high, low, close, volume, close_time
        """
        endpoint = f"{self.base_url}/api/v3/klines"
        params: Dict = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time

        resp = self.session.get(endpoint, params=params, timeout=10)
        resp.raise_for_status()
        raw = resp.json()

        cols = [
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades",
            "taker_buy_base", "taker_buy_quote", "ignore",
        ]
        df = pd.DataFrame(raw, columns=cols)

        for col in ["open", "high", "low", "close", "volume", "quote_volume"]:
            df[col] = pd.to_numeric(df[col])

        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
        df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")

        return df[["open_time", "open", "high", "low", "close", "volume", "trades", "close_time"]]

    def get_current_price(self, symbol: str) -> float:
        endpoint = f"{self.base_url}/api/v3/ticker/price"
        resp = self.session.get(endpoint, params={"symbol": symbol}, timeout=5)
        resp.raise_for_status()
        return float(resp.json()["price"])

    def get_24h_stats(self, symbol: str) -> Dict:
        endpoint = f"{self.base_url}/api/v3/ticker/24hr"
        resp = self.session.get(endpoint, params={"symbol": symbol}, timeout=5)
        resp.raise_for_status()
        return resp.json()


if __name__ == "__main__":
    client = BinanceClient()
    df = client.get_klines("BTCUSDT", "15m", limit=10)
    print(df)
    print(f"\nמחיר נוכחי: ${client.get_current_price('BTCUSDT'):,.2f}")
