import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pandas as pd
import numpy as np
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


class OrderFlowAnalyzer:
    """
    Analizza order flow e zone di liquidita.
    Pattern: Stop Hunt + Reversal
    """

    def __init__(self):
        self.swing_lookback = 20
        self.liquidity_threshold = 0.002
        # FIX: cooldown per evitare segnali duplicati
        self._last_stop_hunt_level = None
        self._last_stop_hunt_bar = -999
        self.stop_hunt_cooldown = 15  # candele di cooldown per stesso livello

    def find_swing_levels(self, df: pd.DataFrame, window: int = 100) -> Dict:
        """
        FIX: usa solo le ultime N candele invece di tutto il DataFrame.
        Questo evita il problema di performance che bloccava il backtest.
        """
        # Usa solo le ultime 200 candele per efficienza
        df_window = df.tail(min(window, len(df)))

        if len(df_window) < self.swing_lookback * 2:
            return {"highs": [], "lows": []}

        highs = []
        lows = []
        lb = self.swing_lookback

        for i in range(lb, len(df_window) - lb):
            high_val = float(df_window["high"].iloc[i])
            low_val = float(df_window["low"].iloc[i])
            window_high = df_window["high"].iloc[i - lb:i + lb]
            window_low = df_window["low"].iloc[i - lb:i + lb]

            if high_val == float(window_high.max()):
                # Conta tocchi in finestra ristretta (no loop su tutto il df)
                zone = high_val * self.liquidity_threshold
                touches = sum(1 for j in range(max(0, i-30), min(len(df_window), i+30))
                              if abs(float(df_window["high"].iloc[j]) - high_val) < zone)
                highs.append({"price": high_val, "index": i, "strength": touches * 10})

            if low_val == float(window_low.min()):
                zone = low_val * self.liquidity_threshold
                touches = sum(1 for j in range(max(0, i-30), min(len(df_window), i+30))
                              if abs(float(df_window["low"].iloc[j]) - low_val) < zone)
                lows.append({"price": low_val, "index": i, "strength": touches * 10})

        highs = sorted(highs, key=lambda x: x["strength"], reverse=True)[:5]
        lows = sorted(lows, key=lambda x: x["strength"], reverse=True)[:5]

        return {"highs": highs, "lows": lows}

    def detect_stop_hunt(self, df: pd.DataFrame, current_bar: int = -1) -> Optional[Dict]:
        """
        Rileva stop hunt con cooldown per evitare segnali duplicati.
        """
        if len(df) < 10:
            return None

        swing_levels = self.find_swing_levels(df)
        current_price = float(df["close"].iloc[-1])

        for i in range(-3, 0):
            candle = df.iloc[i]
            low = float(candle["low"])
            high = float(candle["high"])
            close_val = float(candle["close"])
            open_val = float(candle["open"])

            # Stop hunt bullish
            for level in swing_levels["lows"]:
                level_price = level["price"]

                # COOLDOWN: stesso livello non puo triggerare per N candele
                if (self._last_stop_hunt_level is not None and
                        abs(level_price - self._last_stop_hunt_level) < level_price * 0.001 and
                        current_bar - self._last_stop_hunt_bar < self.stop_hunt_cooldown):
                    continue

                if low < level_price and close_val > level_price:
                    body = abs(close_val - open_val)
                    wick = close_val - low
                    if body > 0 and wick / body > 2:
                        self._last_stop_hunt_level = level_price
                        self._last_stop_hunt_bar = current_bar
                        return {
                            "type": "stop_hunt_bullish",
                            "direction": "LONG",
                            "level_price": level_price,
                            "current_price": current_price,
                            "score": min(100, level["strength"] * 2 + 30),
                            "description": f"Stop hunt bullish su {level_price:.5f} — reversal long atteso",
                        }

            # Stop hunt bearish
            for level in swing_levels["highs"]:
                level_price = level["price"]

                if (self._last_stop_hunt_level is not None and
                        abs(level_price - self._last_stop_hunt_level) < level_price * 0.001 and
                        current_bar - self._last_stop_hunt_bar < self.stop_hunt_cooldown):
                    continue

                if high > level_price and close_val < level_price:
                    body = abs(close_val - open_val)
                    wick = high - close_val
                    if body > 0 and wick / body > 2:
                        self._last_stop_hunt_level = level_price
                        self._last_stop_hunt_bar = current_bar
                        return {
                            "type": "stop_hunt_bearish",
                            "direction": "SHORT",
                            "level_price": level_price,
                            "current_price": current_price,
                            "score": min(100, level["strength"] * 2 + 30),
                            "description": f"Stop hunt bearish su {level_price:.5f} — reversal short atteso",
                        }

        return None

    def get_nearest_liquidity_zones(self, df: pd.DataFrame, current_price: float) -> Dict:
        swing_levels = self.find_swing_levels(df)
        nearest_resistance = None
        nearest_support = None
        min_r = float("inf")
        min_s = float("inf")

        for level in swing_levels["highs"]:
            if level["price"] > current_price:
                dist = level["price"] - current_price
                if dist < min_r:
                    min_r = dist
                    nearest_resistance = level

        for level in swing_levels["lows"]:
            if level["price"] < current_price:
                dist = current_price - level["price"]
                if dist < min_s:
                    min_s = dist
                    nearest_support = level

        return {
            "nearest_resistance": nearest_resistance,
            "nearest_support": nearest_support,
            "resistance_pips": round(min_r * 10000, 1) if nearest_resistance else None,
            "support_pips": round(min_s * 10000, 1) if nearest_support else None,
        }

    def get_order_flow_score(self, df: pd.DataFrame, current_bar: int = -1) -> Dict:
        if df is None or len(df) < 30:
            return {"score": 0, "direction": "neutral", "signals": []}

        current_price = float(df["close"].iloc[-1])
        signals = []
        score = 0

        stop_hunt = self.detect_stop_hunt(df, current_bar)
        if stop_hunt:
            hunt_score = stop_hunt["score"]
            score += hunt_score if stop_hunt["direction"] == "LONG" else -hunt_score
            signals.append(stop_hunt["description"])

        liquidity = self.get_nearest_liquidity_zones(df, current_price)
        if liquidity["resistance_pips"] and liquidity["resistance_pips"] < 20:
            score += 15
            signals.append(f"Vicino a resistenza ({liquidity['resistance_pips']:.0f} pip)")
        if liquidity["support_pips"] and liquidity["support_pips"] < 20:
            score -= 15
            signals.append(f"Vicino a supporto ({liquidity['support_pips']:.0f} pip)")

        score = max(-100, min(100, score))
        direction = "LONG" if score > 20 else "SHORT" if score < -20 else "neutral"

        return {
            "score": round(score, 2),
            "direction": direction,
            "stop_hunt": stop_hunt,
            "liquidity_zones": liquidity,
            "signals": signals,
        }
