import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pandas as pd
import numpy as np
from datetime import datetime, timezone
from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)


class DataCleaner:
    """Pulisce e normalizza tutti i dati prima che entrino nel DB"""

    def clean_ohlcv(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """
        Pulisce dati OHLCV:
        - Rimuove duplicati
        - Rimuove prezzi negativi o zero
        - Rimuove gap anomali (variazioni >5% in una candela)
        - Normalizza timezone a UTC
        """
        if df is None or df.empty:
            return df

        original_len = len(df)

        # 1. Rimuovi duplicati
        df = df[~df.index.duplicated(keep="last")]

        # 2. Rimuovi prezzi invalidi
        df = df[(df["close"] > 0) & (df["open"] > 0) & (df["high"] > 0) & (df["low"] > 0)]

        # 3. Rimuovi candele dove high < low (dati corrotti)
        df = df[df["high"] >= df["low"]]

        # 4. Rimuovi gap anomali (variazione close >5% rispetto alla candela precedente)
        if len(df) > 1:
            pct_change = df["close"].pct_change().abs()
            df = df[pct_change < 0.05]

        # 5. Assicura che l'indice sia timezone-aware (UTC)
        if df.index.tzinfo is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")

        # 6. Ordina per timestamp
        df = df.sort_index()

        cleaned_len = len(df)
        if original_len != cleaned_len:
            logger.info(f"🧹 {symbol}: rimossi {original_len - cleaned_len} record invalidi")

        return df

    def clean_news(self, news_list: List[Dict]) -> List[Dict]:
        """
        Pulisce lista news:
        - Rimuove duplicati per URL
        - Rimuove news senza titolo
        - Normalizza date a UTC
        - Limita summary a 500 caratteri
        """
        seen_urls = set()
        cleaned = []

        for news in news_list:
            # Salta senza URL o titolo
            if not news.get("url") or not news.get("title"):
                continue

            # Salta duplicati
            url = news["url"].strip()
            if url in seen_urls:
                continue
            seen_urls.add(url)

            # Normalizza data
            pub_date = news.get("published_at")
            if pub_date is None:
                news["published_at"] = datetime.now(timezone.utc)
            elif isinstance(pub_date, datetime) and pub_date.tzinfo is None:
                news["published_at"] = pub_date.replace(tzinfo=timezone.utc)

            # Limita lunghezza summary
            if news.get("summary"):
                news["summary"] = news["summary"][:500]

            # Pulisci titolo
            news["title"] = news["title"].strip()[:200]

            cleaned.append(news)

        removed = len(news_list) - len(cleaned)
        if removed > 0:
            logger.info(f"🧹 News: rimossi {removed} duplicati/invalidi")

        return cleaned

    def validate_signal(self, signal: Dict) -> tuple:
        """
        Valida un segnale prima che passi al Risk Manager.
        Ritorna (is_valid: bool, reason: str)
        """
        required_fields = ["symbol", "direction", "entry_price", "raw_score"]
        for field in required_fields:
            if field not in signal or signal[field] is None:
                return False, f"Campo mancante: {field}"

        if signal["direction"] not in ["LONG", "SHORT"]:
            return False, f"Direzione invalida: {signal['direction']}"

        if signal["entry_price"] <= 0:
            return False, "Entry price non valido"

        if not (0 <= signal["raw_score"] <= 100):
            return False, f"Score fuori range: {signal['raw_score']}"

        return True, "OK"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cleaner = DataCleaner()

    # Test con dati finti
    import pandas as pd
    df = pd.DataFrame({
        "open": [1.1, 1.2, -0.5, 1.3],
        "high": [1.15, 1.25, 0.8, 1.35],
        "low": [1.05, 1.15, 0.3, 1.25],
        "close": [1.12, 1.22, 0.4, 1.32],
        "volume": [100, 200, 50, 300],
    }, index=pd.date_range("2024-01-01", periods=4, freq="1h"))

    cleaned = cleaner.clean_ohlcv(df, "EUR/USD")
    print(f"Originale: {len(df)} righe → Pulito: {len(cleaned)} righe")
