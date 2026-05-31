import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from typing import Optional
import logging

from core.data_collector.base_collector import BaseCollector, retry, rate_limit
from config.settings import TWELVE_DATA_API_KEY, FOREX_PAIRS, TIMEFRAMES

logger = logging.getLogger(__name__)

# Mappa coppie forex → simboli Yahoo Finance
YAHOO_SYMBOLS = {
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/JPY": "USDJPY=X",
    "AUD/USD": "AUDUSD=X",
    "USD/CAD": "USDCAD=X",
    "EUR/GBP": "EURGBP=X",
}

# Mappa timeframe → intervallo Yahoo Finance
YAHOO_INTERVALS = {
    "1min": "1m",
    "5min": "5m",
    "15min": "15m",
    "1h": "1h",
    "4h": "1h",   # Yahoo non supporta 4h, usiamo 1h e aggreghiamo
    "1day": "1d",
}


class PriceDataCollector(BaseCollector):

    def __init__(self):
        super().__init__("price_data")
        self.twelve_data_base = "https://api.twelvedata.com"

    @retry(max_attempts=3, delay=2.0)
    @rate_limit(calls_per_minute=8)
    def get_ohlcv_yahoo(self, symbol: str, timeframe: str = "1h", days: int = 180) -> Optional[pd.DataFrame]:
        """
        Scarica dati OHLCV via Yahoo Finance (gratuito, ottimo per MVP)
        """
        yahoo_symbol = YAHOO_SYMBOLS.get(symbol)
        if not yahoo_symbol:
            logger.error(f"Simbolo non supportato: {symbol}")
            return None

        interval = YAHOO_INTERVALS.get(timeframe, "1h")
        end = datetime.utcnow()
        start = end - timedelta(days=days)

        try:
            ticker = yf.Ticker(yahoo_symbol)
            df = ticker.history(start=start, end=end, interval=interval)

            if df.empty:
                logger.warning(f"Nessun dato per {symbol} ({timeframe})")
                return None

            # Normalizza colonne
            df = df.rename(columns={
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            })
            df = df[["open", "high", "low", "close", "volume"]]
            df.index = pd.to_datetime(df.index, utc=True)
            df.index.name = "timestamp"

            logger.info(f"✅ {symbol} ({timeframe}): {len(df)} candele scaricate")
            return df

        except Exception as e:
            logger.error(f"Errore Yahoo Finance per {symbol}: {e}")
            return None

    @retry(max_attempts=3, delay=2.0)
    @rate_limit(calls_per_minute=5)
    def get_ohlcv_twelve_data(self, symbol: str, timeframe: str = "1h", outputsize: int = 500) -> Optional[pd.DataFrame]:
        """
        Scarica dati OHLCV via Twelve Data (più affidabile per real-time)
        Richiede API key
        """
        if not TWELVE_DATA_API_KEY:
            logger.warning("Twelve Data API key non configurata, uso Yahoo Finance")
            return self.get_ohlcv_yahoo(symbol, timeframe)

        # Converti simbolo forex per Twelve Data
        td_symbol = symbol.replace("/", "")  # EUR/USD → EURUSD

        params = {
            "symbol": td_symbol,
            "interval": timeframe,
            "outputsize": outputsize,
            "apikey": TWELVE_DATA_API_KEY,
            "format": "JSON",
        }

        data = self.get(f"{self.twelve_data_base}/time_series", params=params)
        if not data or "values" not in data:
            logger.warning(f"Twelve Data fallito per {symbol}, uso Yahoo Finance come fallback")
            return self.get_ohlcv_yahoo(symbol, timeframe)

        df = pd.DataFrame(data["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.set_index("datetime").sort_index()
        df = df.rename(columns={"open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume"})
        df = df[["open", "high", "low", "close", "volume"]].astype(float)

        logger.info(f"✅ {symbol} ({timeframe}) via Twelve Data: {len(df)} candele")
        return df

    def get_all_pairs(self, timeframe: str = "1h", days: int = 180) -> dict:
        """Scarica dati per tutte le 6 coppie"""
        result = {}
        for pair in FOREX_PAIRS:
            df = self.get_ohlcv_yahoo(pair, timeframe, days)
            if df is not None:
                result[pair] = df
        logger.info(f"✅ Dati scaricati per {len(result)}/{len(FOREX_PAIRS)} coppie")
        return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    collector = PriceDataCollector()

    # Test: scarica EUR/USD H1 degli ultimi 30 giorni
    df = collector.get_ohlcv_yahoo("EUR/USD", "1h", days=30)
    if df is not None:
        print(f"\n✅ EUR/USD H1 — ultime 5 candele:")
        print(df.tail())
    else:
        print("❌ Errore nel download")
