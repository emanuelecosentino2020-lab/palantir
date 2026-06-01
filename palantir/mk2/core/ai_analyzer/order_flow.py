import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


class OrderFlowAnalyzer:
    """
    Analizza order flow e zone di liquidità.
    
    Concetto chiave: il mercato si muove verso le zone dove ci sono 
    molti stop loss dei retail trader (liquidità). Le banche "cacciano"
    questi stop prima di muoversi nella direzione vera.
    
    Pattern principale: Stop Hunt + Reversal
    1. Prezzo rompe un massimo/minimo significativo (falso breakout)
    2. Spike rapido che "caccia" gli stop
    3. Reversal immediato nella direzione opposta
    4. → Questo è il segnale di entrata
    """

    def __init__(self):
        self.swing_lookback = 20    # Candele per identificare swing highs/lows
        self.liquidity_threshold = 0.002  # 0.2% distanza per zona di liquidità

    def find_swing_levels(self, df: pd.DataFrame) -> Dict:
        """
        Identifica swing highs e lows significativi.
        Questi sono le zone dove si accumulano gli stop loss.
        """
        if len(df) < self.swing_lookback * 2:
            return {"highs": [], "lows": []}

        highs = []
        lows = []

        for i in range(self.swing_lookback, len(df) - self.swing_lookback):
            window_high = df["high"].iloc[i - self.swing_lookback:i + self.swing_lookback]
            window_low = df["low"].iloc[i - self.swing_lookback:i + self.swing_lookback]

            # Swing high: massimo locale
            if float(df["high"].iloc[i]) == float(window_high.max()):
                highs.append({
                    "price": float(df["high"].iloc[i]),
                    "index": i,
                    "timestamp": str(df.index[i]),
                    "strength": self._calculate_level_strength(df, i, "high"),
                })

            # Swing low: minimo locale
            if float(df["low"].iloc[i]) == float(window_low.min()):
                lows.append({
                    "price": float(df["low"].iloc[i]),
                    "index": i,
                    "timestamp": str(df.index[i]),
                    "strength": self._calculate_level_strength(df, i, "low"),
                })

        # Mantieni solo i livelli più forti (ultimi 5)
        highs = sorted(highs, key=lambda x: x["strength"], reverse=True)[:5]
        lows = sorted(lows, key=lambda x: x["strength"], reverse=True)[:5]

        return {"highs": highs, "lows": lows}

    def _calculate_level_strength(self, df: pd.DataFrame, idx: int, level_type: str) -> float:
        """
        Calcola la forza di un livello S/R.
        Livelli più testati e più distanti dal prezzo corrente = più forti.
        """
        price = float(df["high"].iloc[idx]) if level_type == "high" else float(df["low"].iloc[idx])
        current_price = float(df["close"].iloc[-1])

        # Quante volte il prezzo ha toccato questa zona
        touches = 0
        zone_size = price * self.liquidity_threshold
        for i in range(max(0, idx - 50), min(len(df), idx + 50)):
            if abs(float(df["high"].iloc[i]) - price) < zone_size or \
               abs(float(df["low"].iloc[i]) - price) < zone_size:
                touches += 1

        # Distanza dal prezzo corrente (livelli vicini = più rilevanti)
        distance_pct = abs(price - current_price) / current_price * 100
        proximity_score = max(0, 10 - distance_pct * 2)

        return touches * 10 + proximity_score

    def detect_stop_hunt(self, df: pd.DataFrame) -> Optional[Dict]:
        """
        Rileva pattern di stop hunt nelle ultime candele.
        
        Pattern: spike che rompe un livello chiave seguito da reversal immediato
        Questo indica che le "mani forti" hanno cacciato gli stop retail.
        """
        if len(df) < 10:
            return None

        swing_levels = self.find_swing_levels(df)
        last_3 = df.iloc[-3:]
        current_price = float(df["close"].iloc[-1])
        current_candle = df.iloc[-1]

        # Controlla stop hunt bullish (spike verso il basso poi reversal su)
        for low_level in swing_levels["lows"]:
            level_price = low_level["price"]

            for i in range(-3, 0):
                candle = df.iloc[i]
                low = float(candle["low"])
                close = float(candle["close"])

                # La candela ha rotto sotto il livello ma ha chiuso sopra (pin bar / engulfing)
                if low < level_price and close > level_price:
                    wick_size = close - low
                    body_size = abs(float(candle["close"]) - float(candle["open"]))

                    # Wick deve essere almeno 2x il body (segnale di rifiuto forte)
                    if body_size > 0 and wick_size / body_size > 2:
                        score = min(100, low_level["strength"] * 2 + 30)
                        return {
                            "type": "stop_hunt_bullish",
                            "direction": "LONG",
                            "level_price": level_price,
                            "current_price": current_price,
                            "score": round(score, 2),
                            "description": f"Stop hunt bullish su {level_price:.5f} — reversal long atteso",
                        }

        # Controlla stop hunt bearish (spike verso l'alto poi reversal giù)
        for high_level in swing_levels["highs"]:
            level_price = high_level["price"]

            for i in range(-3, 0):
                candle = df.iloc[i]
                high = float(candle["high"])
                close = float(candle["close"])

                if high > level_price and close < level_price:
                    wick_size = high - close
                    body_size = abs(float(candle["close"]) - float(candle["open"]))

                    if body_size > 0 and wick_size / body_size > 2:
                        score = min(100, high_level["strength"] * 2 + 30)
                        return {
                            "type": "stop_hunt_bearish",
                            "direction": "SHORT",
                            "level_price": level_price,
                            "current_price": current_price,
                            "score": round(score, 2),
                            "description": f"Stop hunt bearish su {level_price:.5f} — reversal short atteso",
                        }

        return None

    def get_nearest_liquidity_zones(self, df: pd.DataFrame, current_price: float) -> Dict:
        """
        Trova le zone di liquidità più vicine al prezzo attuale.
        Queste sono i target naturali del mercato.
        """
        swing_levels = self.find_swing_levels(df)

        nearest_resistance = None
        nearest_support = None
        min_resistance_dist = float("inf")
        min_support_dist = float("inf")

        for level in swing_levels["highs"]:
            if level["price"] > current_price:
                dist = level["price"] - current_price
                if dist < min_resistance_dist:
                    min_resistance_dist = dist
                    nearest_resistance = level

        for level in swing_levels["lows"]:
            if level["price"] < current_price:
                dist = current_price - level["price"]
                if dist < min_support_dist:
                    min_support_dist = dist
                    nearest_support = level

        return {
            "nearest_resistance": nearest_resistance,
            "nearest_support": nearest_support,
            "resistance_pips": round(min_resistance_dist * 10000, 1) if nearest_resistance else None,
            "support_pips": round(min_support_dist * 10000, 1) if nearest_support else None,
        }

    def get_order_flow_score(self, df: pd.DataFrame) -> Dict:
        """
        Score complessivo dell'order flow per una coppia.
        Combina stop hunt detection + liquidità zones + imbalance.
        """
        if df is None or len(df) < 30:
            return {"score": 0, "direction": "neutral", "signals": []}

        current_price = float(df["close"].iloc[-1])
        signals = []
        score = 0

        # 1. Stop hunt detection
        stop_hunt = self.detect_stop_hunt(df)
        if stop_hunt:
            hunt_score = stop_hunt["score"]
            if stop_hunt["direction"] == "LONG":
                score += hunt_score
            else:
                score -= hunt_score
            signals.append(stop_hunt["description"])
            logger.info(f"🎯 Stop hunt rilevato: {stop_hunt['description']}")

        # 2. Liquidity zones
        liquidity = self.get_nearest_liquidity_zones(df, current_price)
        if liquidity["nearest_resistance"] and liquidity["resistance_pips"]:
            if liquidity["resistance_pips"] < 20:
                score += 15
                signals.append(f"Vicino a resistenza liquidity ({liquidity['resistance_pips']:.0f} pip)")

        if liquidity["nearest_support"] and liquidity["support_pips"]:
            if liquidity["support_pips"] < 20:
                score -= 15
                signals.append(f"Vicino a supporto liquidity ({liquidity['support_pips']:.0f} pip)")

        # 3. Volume imbalance (se disponibile)
        if "volume" in df.columns:
            last_5_volume = df["volume"].iloc[-5:]
            avg_volume = df["volume"].iloc[-20:].mean()
            if avg_volume > 0:
                volume_ratio = float(last_5_volume.mean()) / float(avg_volume)
                if volume_ratio > 1.5:
                    signals.append(f"Volume anomalo ({volume_ratio:.1f}x media)")

        score = max(-100, min(100, score))
        direction = "LONG" if score > 20 else "SHORT" if score < -20 else "neutral"

        return {
            "score": round(score, 2),
            "direction": direction,
            "stop_hunt": stop_hunt,
            "liquidity_zones": liquidity,
            "signals": signals,
        }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from core.data_collector.price_data import PriceDataCollector

    collector = PriceDataCollector()
    analyzer = OrderFlowAnalyzer()

    df = collector.get_ohlcv_yahoo("EUR/USD", "1h", days=30)
    if df is not None:
        result = analyzer.get_order_flow_score(df)
        print(f"\n🎯 Order Flow EUR/USD:")
        print(f"  Score: {result['score']}")
        print(f"  Direction: {result['direction']}")
        for signal in result["signals"]:
            print(f"  → {signal}")

        liquidity = result["liquidity_zones"]
        if liquidity["nearest_resistance"]:
            print(f"  Resistenza: {liquidity['nearest_resistance']['price']:.5f} ({liquidity['resistance_pips']} pip)")
        if liquidity["nearest_support"]:
            print(f"  Supporto: {liquidity['nearest_support']['price']:.5f} ({liquidity['support_pips']} pip)")
