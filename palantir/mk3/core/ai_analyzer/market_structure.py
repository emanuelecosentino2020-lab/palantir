import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


class MarketStructureAnalyzer:
    """
    Analizza la struttura di mercato: Higher Highs/Lows vs Lower Highs/Lows.
    
    Questo è il filtro più potente per eliminare i falsi segnali:
    - Mercato in uptrend (HH/HL) → solo segnali LONG
    - Mercato in downtrend (LH/LL) → solo segnali SHORT
    - Struttura rotta → possibile cambio di trend
    
    Le grandi banche usano questo come filtro primario prima di entrare.
    """

    def __init__(self, swing_period: int = 10):
        self.swing_period = swing_period

    def find_swing_points(self, df: pd.DataFrame) -> Tuple[List, List]:
        """Identifica swing highs e swing lows sul DataFrame"""
        swing_highs = []
        swing_lows = []
        n = self.swing_period

        for i in range(n, len(df) - n):
            high = float(df["high"].iloc[i])
            low = float(df["low"].iloc[i])

            # Swing high: massimo locale
            window_highs = df["high"].iloc[i-n:i+n+1].astype(float)
            if high == float(window_highs.max()):
                swing_highs.append({"price": high, "index": i, "timestamp": str(df.index[i])})

            # Swing low: minimo locale
            window_lows = df["low"].iloc[i-n:i+n+1].astype(float)
            if low == float(window_lows.min()):
                swing_lows.append({"price": low, "index": i, "timestamp": str(df.index[i])})

        return swing_highs, swing_lows

    def get_market_structure(self, df: pd.DataFrame) -> Dict:
        """
        Determina la struttura di mercato corrente.
        
        Uptrend: ogni swing high > precedente swing high E ogni swing low > precedente swing low
        Downtrend: ogni swing high < precedente swing high E ogni swing low < precedente swing low
        Ranging: struttura mista
        """
        if df is None or len(df) < self.swing_period * 4:
            return {"structure": "unknown", "score": 0, "direction": "neutral"}

        swing_highs, swing_lows = self.find_swing_points(df)

        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return {"structure": "insufficient_data", "score": 0, "direction": "neutral"}

        # Analizza ultimi 3 swing highs e lows
        recent_highs = sorted(swing_highs, key=lambda x: x["index"])[-3:]
        recent_lows = sorted(swing_lows, key=lambda x: x["index"])[-3:]

        # Conta Higher Highs e Higher Lows
        hh_count = sum(1 for i in range(1, len(recent_highs))
                      if recent_highs[i]["price"] > recent_highs[i-1]["price"])
        hl_count = sum(1 for i in range(1, len(recent_lows))
                      if recent_lows[i]["price"] > recent_lows[i-1]["price"])

        # Conta Lower Highs e Lower Lows
        lh_count = sum(1 for i in range(1, len(recent_highs))
                      if recent_highs[i]["price"] < recent_highs[i-1]["price"])
        ll_count = sum(1 for i in range(1, len(recent_lows))
                      if recent_lows[i]["price"] < recent_lows[i-1]["price"])

        max_count = len(recent_highs) - 1

        # Determina struttura
        uptrend_score = (hh_count + hl_count) / (max_count * 2) * 100 if max_count > 0 else 0
        downtrend_score = (lh_count + ll_count) / (max_count * 2) * 100 if max_count > 0 else 0

        if uptrend_score >= 75:
            structure = "uptrend"
            direction = "LONG"
            score = uptrend_score
            description = f"HH/HL confermato — trend rialzista solido"
        elif downtrend_score >= 75:
            structure = "downtrend"
            direction = "SHORT"
            score = -downtrend_score
            description = f"LH/LL confermato — trend ribassista solido"
        elif uptrend_score > downtrend_score:
            structure = "weak_uptrend"
            direction = "LONG"
            score = uptrend_score * 0.5
            description = "Struttura prevalentemente rialzista"
        elif downtrend_score > uptrend_score:
            structure = "weak_downtrend"
            direction = "SHORT"
            score = -downtrend_score * 0.5
            description = "Struttura prevalentemente ribassista"
        else:
            structure = "ranging"
            direction = "neutral"
            score = 0
            description = "Mercato laterale — struttura indefinita"

        # Cerca Break of Structure (BOS) — cambio di trend imminente
        bos = self._detect_break_of_structure(df, recent_highs, recent_lows)

        return {
            "structure": structure,
            "direction": direction,
            "score": round(score, 2),
            "description": description,
            "hh_count": hh_count,
            "hl_count": hl_count,
            "lh_count": lh_count,
            "ll_count": ll_count,
            "break_of_structure": bos,
            "last_swing_high": recent_highs[-1]["price"] if recent_highs else None,
            "last_swing_low": recent_lows[-1]["price"] if recent_lows else None,
        }

    def _detect_break_of_structure(self, df: pd.DataFrame, swing_highs: List, swing_lows: List) -> Optional[Dict]:
        """
        Rileva un Break of Structure (BOS) — momento in cui il trend potrebbe cambiare.
        
        Bullish BOS: prezzo rompe sopra il precedente swing high in downtrend
        Bearish BOS: prezzo rompe sotto il precedente swing low in uptrend
        """
        if not swing_highs or not swing_lows:
            return None

        current_price = float(df["close"].iloc[-1])
        last_high = swing_highs[-1]["price"]
        last_low = swing_lows[-1]["price"]
        prev_high = swing_highs[-2]["price"] if len(swing_highs) >= 2 else last_high
        prev_low = swing_lows[-2]["price"] if len(swing_lows) >= 2 else last_low

        # Bullish BOS: prezzo ha rotto sopra un precedente swing high significativo
        if current_price > prev_high and last_high > prev_high:
            return {
                "type": "bullish_bos",
                "level": prev_high,
                "description": f"Break of Structure rialzista su {prev_high:.5f}",
                "implication": "LONG",
            }

        # Bearish BOS: prezzo ha rotto sotto un precedente swing low significativo
        if current_price < prev_low and last_low < prev_low:
            return {
                "type": "bearish_bos",
                "level": prev_low,
                "description": f"Break of Structure ribassista su {prev_low:.5f}",
                "implication": "SHORT",
            }

        return None

    def validate_signal_direction(self, signal_direction: str, df: pd.DataFrame) -> Dict:
        """
        Valida se la direzione del segnale è in linea con la struttura di mercato.
        Ritorna un multiplier: 1.3 se allineato, 0.3 se contrario.
        """
        structure = self.get_market_structure(df)
        market_direction = structure.get("direction", "neutral")

        if market_direction == "neutral":
            return {"valid": True, "multiplier": 0.7, "reason": "Struttura neutrale"}

        if signal_direction == market_direction or market_direction in ["weak_uptrend", "weak_downtrend"]:
            return {
                "valid": True,
                "multiplier": 1.3,
                "reason": f"Segnale allineato alla struttura: {structure['structure']}",
            }
        else:
            return {
                "valid": False,
                "multiplier": 0.2,
                "reason": f"Segnale CONTRO la struttura: {signal_direction} vs {market_direction}",
            }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from core.data_collector.price_data import PriceDataCollector

    collector = PriceDataCollector()
    analyzer = MarketStructureAnalyzer()

    df = collector.get_ohlcv_yahoo("EUR/USD", "1h", days=60)
    if df is not None:
        structure = analyzer.get_market_structure(df)
        print(f"\n📊 Market Structure EUR/USD:")
        print(f"  Struttura: {structure['structure']}")
        print(f"  Direzione: {structure['direction']}")
        print(f"  Score: {structure['score']}")
        print(f"  {structure['description']}")
        print(f"  HH: {structure['hh_count']} | HL: {structure['hl_count']}")
        print(f"  LH: {structure['lh_count']} | LL: {structure['ll_count']}")
        if structure['break_of_structure']:
            print(f"  ⚡ BOS: {structure['break_of_structure']['description']}")
