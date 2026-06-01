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


def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, period=14) -> pd.Series:
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return true_range.rolling(period).mean()


def calculate_adx(high: pd.Series, low: pd.Series, close: pd.Series, period=14) -> pd.Series:
    """
    ADX — misura la FORZA del trend (non la direzione).
    ADX > 25 = trend forte → sistema attivo
    ADX < 20 = mercato laterale → sistema in pausa
    """
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm[plus_dm < 0] = 0
    minus_dm[minus_dm < 0] = 0

    atr = calculate_atr(high, low, close, period)
    plus_di = 100 * (plus_dm.ewm(span=period).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(span=period).mean() / atr)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.ewm(span=period).mean()
    return adx


def calculate_bollinger(close: pd.Series, period=20, std=2):
    sma = close.rolling(period).mean()
    std_dev = close.rolling(period).std()
    return sma + (std_dev * std), sma, sma - (std_dev * std)


class EnhancedTechnicalAnalyzer:
    """
    Technical analyzer potenziato con:
    - Filtro EMA200 obbligatorio (trend direction)
    - ADX filter (solo in trend, non in laterale)
    - Soglia score alzata a 80
    - Supporto multi-timeframe
    """

    def get_indicators(self, df: pd.DataFrame) -> Dict:
        if df is None or len(df) < 60:
            return {}

        close = df["close"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)

        rsi = calculate_rsi(close, 14)
        macd_line, macd_signal, macd_hist = calculate_macd(close)
        bb_upper, bb_mid, bb_lower = calculate_bollinger(close)
        atr = calculate_atr(high, low, close, 14)
        adx = calculate_adx(high, low, close, 14)
        ema20 = close.ewm(span=20).mean()
        ema50 = close.ewm(span=50).mean()
        ema200 = close.ewm(span=200).mean()

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
            "atr": round(float(atr.iloc[-1]), 6),
            "adx": round(float(adx.iloc[-1]), 2),
            "ema20": round(float(ema20.iloc[-1]), 5),
            "ema50": round(float(ema50.iloc[-1]), 5),
            "ema200": round(float(ema200.iloc[-1]), 5),
            "close": current_close,
            "above_ema20": current_close > float(ema20.iloc[-1]),
            "above_ema50": current_close > float(ema50.iloc[-1]),
            "above_ema200": current_close > float(ema200.iloc[-1]),
            "trend_strong": float(adx.iloc[-1]) > 25,
        }

    def get_technical_score(self, df: pd.DataFrame) -> Dict:
        """
        Score tecnico con filtri potenziati.
        
        NOVITÀ rispetto alla versione precedente:
        1. Se ADX < 20 → mercato laterale → score basso → nessun segnale
        2. Filtro EMA200 obbligatorio: LONG solo sopra, SHORT solo sotto
        3. Soglia minima interna alzata
        """
        ind = self.get_indicators(df)
        if not ind:
            return {"score": 0, "direction": "neutral", "strength": 0, "signals": [], "filtered": False}

        # ── FILTRO 1: ADX — no segnali in mercato laterale ────────────────
        adx = ind.get("adx", 0)
        if adx < 20:
            return {
                "score": 0,
                "direction": "neutral",
                "strength": 0,
                "signals": [f"ADX {adx:.1f} < 20 — mercato laterale, nessun segnale"],
                "atr": ind["atr"],
                "indicators": ind,
                "filtered": True,
                "filter_reason": "ADX_LOW",
            }

        # ── FILTRO 2: EMA200 — determina direzione consentita ─────────────
        above_ema200 = ind.get("above_ema200", True)
        allowed_direction = "LONG" if above_ema200 else "SHORT"

        score = 0
        signals = []

        # RSI (peso 20)
        rsi = ind["rsi"]
        if rsi < 35:
            score += 20
            signals.append(f"RSI oversold ({rsi:.0f})")
        elif rsi > 65:
            score -= 20
            signals.append(f"RSI overbought ({rsi:.0f})")
        elif rsi >= 55:
            score += 8
        elif rsi <= 45:
            score -= 8

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

        # EMA alignment (peso 30)
        ema_score = 0
        if ind["above_ema20"]:
            ema_score += 10
        if ind["above_ema50"]:
            ema_score += 10
        if ind["above_ema200"]:
            ema_score += 10
            signals.append("Price above EMA200 — uptrend")
        else:
            signals.append("Price below EMA200 — downtrend")
        score += ema_score - 15

        # ADX bonus (trend forte = segnale più affidabile)
        if adx > 35:
            score = score * 1.2
            signals.append(f"ADX {adx:.0f} — trend molto forte")
        elif adx > 25:
            score = score * 1.1

        # Bollinger (peso 15)
        bb_pct = (ind["close"] - ind["bb_lower"]) / max(ind["bb_upper"] - ind["bb_lower"], 0.0001) * 100
        if bb_pct < 10:
            score += 15
            signals.append("Price vicino BB lower")
        elif bb_pct > 90:
            score -= 15
            signals.append("Price vicino BB upper")

        score = max(-100, min(100, score))

        # ── FILTRO 3: EMA200 direction filter ─────────────────────────────
        if score > 0 and not above_ema200:
            score = score * 0.3  # Penalizza segnali long sotto EMA200
            signals.append("⚠️ Segnale long sotto EMA200 — penalizzato")
        elif score < 0 and above_ema200:
            score = score * 0.3  # Penalizza segnali short sopra EMA200
            signals.append("⚠️ Segnale short sopra EMA200 — penalizzato")

        direction = "up" if score > 20 else "down" if score < -20 else "neutral"
        strength = abs(score)

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
            "adx": adx,
            "rsi": rsi,
            "above_ema200": above_ema200,
            "allowed_direction": allowed_direction,
            "indicators": ind,
            "filtered": False,
        }

    def get_multiframe_score(self, df_h1: pd.DataFrame, df_h4: pd.DataFrame) -> Dict:
        """
        Analisi multi-timeframe.
        Il segnale H1 deve essere confermato da H4 nella stessa direzione.
        Se H1 e H4 sono discordanti → nessun segnale.
        """
        score_h1 = self.get_technical_score(df_h1)
        score_h4 = self.get_technical_score(df_h4)

        h1_direction = score_h1.get("direction", "neutral")
        h4_direction = score_h4.get("direction", "neutral")

        # Stessa direzione → segnale confermato (bonus)
        if h1_direction == h4_direction and h1_direction != "neutral":
            combined_score = (score_h1["score"] * 0.6 + score_h4["score"] * 0.4) * 1.2
            confirmed = True
            confirmation = f"H1 e H4 allineati su {h1_direction}"
        elif h1_direction != "neutral" and h4_direction == "neutral":
            combined_score = score_h1["score"] * 0.7
            confirmed = False
            confirmation = "H4 neutro — segnale H1 ridotto"
        elif h1_direction != h4_direction and h1_direction != "neutral" and h4_direction != "neutral":
            combined_score = score_h1["score"] * 0.2
            confirmed = False
            confirmation = f"H1 e H4 discordanti — segnale molto debole"
        else:
            combined_score = 0
            confirmed = False
            confirmation = "Nessun segnale su H1"

        combined_score = max(-100, min(100, combined_score))

        return {
            "score": round(combined_score, 2),
            "direction": h1_direction,
            "confirmed_by_h4": confirmed,
            "confirmation": confirmation,
            "h1_score": score_h1["score"],
            "h4_score": score_h4["score"],
            "atr": score_h1.get("atr", 0),
            "adx": score_h1.get("adx", 0),
            "signals": score_h1.get("signals", []) + [confirmation],
            "above_ema200": score_h1.get("above_ema200", True),
            "filtered": score_h1.get("filtered", False),
        }

    def detect_patterns(self, df: pd.DataFrame) -> List[str]:
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
        if total_range > 0 and upper_wick > body * 2 and upper_wick > lower_wick * 2:
            patterns.append("PIN_BAR_BEARISH")
        if total_range > 0 and lower_wick > body * 2 and lower_wick > upper_wick * 2:
            patterns.append("PIN_BAR_BULLISH")
        if c > o and pc > po and c > po and o < pc:
            patterns.append("ENGULFING_BULLISH")
        if o > c and po > pc and o > pc and c < po:
            patterns.append("ENGULFING_BEARISH")
        if h < ph and l > pl:
            patterns.append("INSIDE_BAR")
        return patterns


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from core.data_collector.price_data import PriceDataCollector

    collector = PriceDataCollector()
    analyzer = EnhancedTechnicalAnalyzer()

    df_h1 = collector.get_ohlcv_yahoo("EUR/USD", "1h", days=90)
    df_h4 = collector.get_ohlcv_yahoo("EUR/USD", "4h", days=180)

    if df_h1 is not None and df_h4 is not None:
        result = analyzer.get_multiframe_score(df_h1, df_h4)
        print(f"\n📊 EUR/USD Multi-timeframe:")
        print(f"  Score combinato: {result['score']}")
        print(f"  Direction: {result['direction']}")
        print(f"  H4 confermato: {result['confirmed_by_h4']}")
        print(f"  ADX: {result['adx']:.1f}")
        for signal in result["signals"][:5]:
            print(f"  → {signal}")
