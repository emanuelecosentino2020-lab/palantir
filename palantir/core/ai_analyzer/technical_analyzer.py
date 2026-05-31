import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pandas as pd
import numpy as np
from typing import Dict, Optional, List
import logging

logger = logging.getLogger(__name__)


def calculate_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def calculate_macd(close: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = close.ewm(span=fast).mean()
    ema_slow = close.ewm(span=slow).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def calculate_bollinger(close: pd.Series, period=20, std=2):
    sma = close.rolling(period).mean()
    std_dev = close.rolling(period).std()
    upper = sma + (std_dev * std)
    lower = sma - (std_dev * std)
    return upper, sma, lower


def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, period=14) -> pd.Series:
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return true_range.rolling(period).mean()


class TechnicalAnalyzer:

    def get_indicators(self, df: pd.DataFrame) -> Dict:
        """Calcola tutti gli indicatori tecnici su un DataFrame OHLCV"""
        if df is None or len(df) < 50:
            return {}

        close = df["close"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)

        # Indicatori
        rsi = calculate_rsi(close, 14)
        macd_line, macd_signal, macd_hist = calculate_macd(close)
        bb_upper, bb_mid, bb_lower = calculate_bollinger(close)
        atr = calculate_atr(high, low, close, 14)
        ema20 = close.ewm(span=20).mean()
        ema50 = close.ewm(span=50).mean()
        ema200 = close.ewm(span=200).mean()

        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else last
        current_close = float(close.iloc[-1])

        return {
            "rsi": round(float(rsi.iloc[-1]), 2),
            "rsi_prev": round(float(rsi.iloc[-2]), 2),
            "macd": round(float(macd_line.iloc[-1]), 6),
            "macd_signal": round(float(macd_signal.iloc[-1]), 6),
            "macd_hist": round(float(macd_hist.iloc[-1]), 6),
            "macd_hist_prev": round(float(macd_hist.iloc[-2]), 6),
            "bb_upper": round(float(bb_upper.iloc[-1]), 5),
            "bb_mid": round(float(bb_mid.iloc[-1]), 5),
            "bb_lower": round(float(bb_lower.iloc[-1]), 5),
            "bb_pct": round((current_close - float(bb_lower.iloc[-1])) / (float(bb_upper.iloc[-1]) - float(bb_lower.iloc[-1])) * 100, 2),
            "atr": round(float(atr.iloc[-1]), 6),
            "ema20": round(float(ema20.iloc[-1]), 5),
            "ema50": round(float(ema50.iloc[-1]), 5),
            "ema200": round(float(ema200.iloc[-1]), 5),
            "close": current_close,
            "above_ema20": current_close > float(ema20.iloc[-1]),
            "above_ema50": current_close > float(ema50.iloc[-1]),
            "above_ema200": current_close > float(ema200.iloc[-1]),
        }

    def get_technical_score(self, df: pd.DataFrame) -> Dict:
        """
        Calcola score tecnico composito da -100 a +100.
        Positivo = bias rialzista, Negativo = bias ribassista.
        """
        ind = self.get_indicators(df)
        if not ind:
            return {"score": 0, "direction": "neutral", "strength": 0, "signals": []}

        score = 0
        signals = []

        # RSI (peso 25)
        rsi = ind["rsi"]
        if rsi < 30:
            score += 25
            signals.append("RSI oversold — potenziale rimbalzo")
        elif rsi > 70:
            score -= 25
            signals.append("RSI overbought — potenziale correzione")
        elif 45 < rsi < 55:
            pass  # Neutro
        elif rsi >= 55:
            score += 10
        else:
            score -= 10

        # MACD (peso 25)
        if ind["macd_hist"] > 0 and ind["macd_hist_prev"] <= 0:
            score += 25
            signals.append("MACD crossover bullish")
        elif ind["macd_hist"] < 0 and ind["macd_hist_prev"] >= 0:
            score -= 25
            signals.append("MACD crossover bearish")
        elif ind["macd_hist"] > 0:
            score += 10
        elif ind["macd_hist"] < 0:
            score -= 10

        # EMA trend (peso 30)
        ema_score = 0
        if ind["above_ema20"]:
            ema_score += 10
        if ind["above_ema50"]:
            ema_score += 10
        if ind["above_ema200"]:
            ema_score += 10
        score += ema_score - 15  # Normalizza: 0 EMAs = -15, tutte e 3 = +15

        if ind["above_ema200"]:
            signals.append("Price above EMA200 — trend rialzista")
        else:
            signals.append("Price below EMA200 — trend ribassista")

        # Bollinger Bands (peso 20)
        bb_pct = ind["bb_pct"]
        if bb_pct < 10:
            score += 20
            signals.append("Price vicino BB lower — possibile rimbalzo")
        elif bb_pct > 90:
            score -= 20
            signals.append("Price vicino BB upper — possibile inversione")

        # Determina direzione e forza
        score = max(-100, min(100, score))
        if score > 20:
            direction = "up"
        elif score < -20:
            direction = "down"
        else:
            direction = "neutral"

        strength = abs(score)

        # Key levels (supporto e resistenza semplici)
        recent_high = float(df["high"].tail(20).max())
        recent_low = float(df["low"].tail(20).min())

        return {
            "score": round(score, 2),
            "direction": direction,
            "strength": round(strength, 2),
            "signals": signals,
            "key_resistance": round(recent_high, 5),
            "key_support": round(recent_low, 5),
            "atr": ind["atr"],
            "rsi": rsi,
            "indicators": ind,
        }

    def detect_patterns(self, df: pd.DataFrame) -> List[str]:
        """Rileva pattern candlestick: Pin Bar, Engulfing, Inside Bar"""
        patterns = []
        if len(df) < 3:
            return patterns

        last = df.iloc[-1]
        prev = df.iloc[-2]

        o, h, l, c = float(last["open"]), float(last["high"]), float(last["low"]), float(last["close"])
        po, ph, pl, pc = float(prev["open"]), float(prev["high"]), float(prev["low"]), float(prev["close"])

        body = abs(c - o)
        total_range = h - l
        upper_wick = h - max(o, c)
        lower_wick = min(o, c) - l

        # Pin Bar bearish (lunga wick superiore)
        if total_range > 0 and upper_wick > body * 2 and upper_wick > lower_wick * 2:
            patterns.append("PIN_BAR_BEARISH")

        # Pin Bar bullish (lunga wick inferiore)
        if total_range > 0 and lower_wick > body * 2 and lower_wick > upper_wick * 2:
            patterns.append("PIN_BAR_BULLISH")

        # Engulfing bullish
        if c > o and pc > po and c > po and o < pc:
            patterns.append("ENGULFING_BULLISH")

        # Engulfing bearish
        if o > c and po > pc and o > pc and c < po:
            patterns.append("ENGULFING_BEARISH")

        # Inside Bar
        if h < ph and l > pl:
            patterns.append("INSIDE_BAR")

        return patterns


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from core.data_collector.price_data import PriceDataCollector

    collector = PriceDataCollector()
    analyzer = TechnicalAnalyzer()

    df = collector.get_ohlcv_yahoo("EUR/USD", "1h", days=60)
    if df is not None:
        result = analyzer.get_technical_score(df)
        print(f"\n📊 EUR/USD Technical Score:")
        print(f"  Score: {result['score']}")
        print(f"  Direction: {result['direction']}")
        print(f"  RSI: {result['rsi']}")
        print(f"  ATR: {result['atr']}")
        for signal in result["signals"]:
            print(f"  → {signal}")
        patterns = analyzer.detect_patterns(df)
        if patterns:
            print(f"  Patterns: {patterns}")
