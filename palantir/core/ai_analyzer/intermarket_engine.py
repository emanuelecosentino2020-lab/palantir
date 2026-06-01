import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Optional
import logging

logger = logging.getLogger(__name__)

# Simboli Yahoo Finance per asset correlati
INTERMARKET_SYMBOLS = {
    "DXY": "DX-Y.NYB",        # US Dollar Index
    "VIX": "^VIX",             # Volatility Index
    "GOLD": "GC=F",            # Gold futures
    "OIL": "CL=F",             # WTI Crude Oil
    "US10Y": "^TNX",           # US 10Y Treasury yield
    "US2Y": "^IRX",            # US 2Y Treasury
    "SPX": "^GSPC",            # S&P 500
}

# Come ogni asset impatta le coppie forex
CORRELATION_MAP = {
    "EUR/USD": {"DXY": -0.90, "US10Y": -0.60, "VIX": -0.30},
    "GBP/USD": {"DXY": -0.85, "US10Y": -0.55, "VIX": -0.25},
    "USD/JPY": {"DXY": +0.75, "US10Y": +0.80, "VIX": -0.70},
    "AUD/USD": {"DXY": -0.75, "GOLD": +0.55, "VIX": -0.65, "OIL": +0.40},
    "USD/CAD": {"DXY": +0.70, "OIL": -0.75},
    "EUR/GBP": {"DXY": +0.10},
}


class IntermarketEngine:
    """
    Analizza correlazioni cross-asset in tempo reale.
    Quando DXY si muove forte → bias automatico su tutte le coppie USD.
    Quando VIX spika → risk-off mode → long USD/JPY, short AUD/USD.
    """

    def __init__(self):
        self._cache = {}
        self._cache_time = {}
        self.cache_ttl = 300  # 5 minuti

    def _is_cached(self, key: str) -> bool:
        if key not in self._cache_time:
            return False
        return (datetime.utcnow() - self._cache_time[key]).seconds < self.cache_ttl

    def _get_asset_data(self, symbol_key: str, periods: int = 20) -> Optional[pd.Series]:
        """Scarica dati recenti per un asset"""
        if self._is_cached(symbol_key):
            return self._cache.get(symbol_key)

        yahoo_symbol = INTERMARKET_SYMBOLS.get(symbol_key)
        if not yahoo_symbol:
            return None

        try:
            ticker = yf.Ticker(yahoo_symbol)
            df = ticker.history(period="5d", interval="1h")
            if df.empty or len(df) < 5:
                return None
            series = df["Close"]
            self._cache[symbol_key] = series
            self._cache_time[symbol_key] = datetime.utcnow()
            return series
        except Exception as e:
            logger.error(f"Errore scaricamento {symbol_key}: {e}")
            return None

    def get_asset_momentum(self, symbol_key: str, periods: int = 4) -> float:
        """
        Calcola momentum recente di un asset.
        Ritorna variazione percentuale nelle ultime N candele H1.
        Positivo = asset in salita, Negativo = in discesa.
        """
        data = self._get_asset_data(symbol_key)
        if data is None or len(data) < periods + 1:
            return 0.0
        recent = data.iloc[-1]
        past = data.iloc[-(periods + 1)]
        if past == 0:
            return 0.0
        return ((recent - past) / past) * 100

    def get_regime(self) -> Dict:
        """
        Determina il regime di mercato corrente.
        - risk_on: VIX basso, SPX in salita → favorisce AUD, EUR
        - risk_off: VIX alto, SPX in calo → favorisce USD, JPY
        - transition: segnali misti
        """
        vix_momentum = self.get_asset_momentum("VIX", 4)
        spx_momentum = self.get_asset_momentum("SPX", 4)
        vix_data = self._get_asset_data("VIX")
        vix_level = float(vix_data.iloc[-1]) if vix_data is not None else 20.0

        if vix_level > 25 or vix_momentum > 5:
            regime = "risk_off"
            description = "VIX elevato — mercato in risk-off"
            strength = min(100, vix_level * 2)
        elif vix_level < 15 and spx_momentum > 0:
            regime = "risk_on"
            description = "VIX basso, SPX positivo — risk appetite alto"
            strength = min(100, (15 - vix_level) * 5 + spx_momentum * 10)
        else:
            regime = "transition"
            description = "Regime incerto — segnali misti"
            strength = 30

        logger.info(f"📊 Regime: {regime} (VIX: {vix_level:.1f}, strength: {strength:.0f})")
        return {
            "regime": regime,
            "description": description,
            "strength": round(strength, 2),
            "vix_level": round(vix_level, 2),
            "vix_momentum": round(vix_momentum, 2),
            "spx_momentum": round(spx_momentum, 2),
        }

    def get_dxy_bias(self) -> Dict:
        """
        Analizza il DXY per determinare bias direzionale USD.
        Movimento DXY forte → segnale automatico su coppie USD.
        """
        dxy_momentum_1h = self.get_asset_momentum("DXY", 1)
        dxy_momentum_4h = self.get_asset_momentum("DXY", 4)
        dxy_momentum_1d = self.get_asset_momentum("DXY", 24)

        # Score pesato: 1h conta di più per trading intraday
        dxy_score = (dxy_momentum_1h * 0.5 + dxy_momentum_4h * 0.3 + dxy_momentum_1d * 0.2) * 100

        # Determina bias
        if dxy_score > 15:
            bias = "strong_usd"
            description = f"DXY in forte salita (+{dxy_momentum_1h:.2f}%) → short EUR/GBP/AUD"
        elif dxy_score > 5:
            bias = "mild_usd"
            description = f"DXY leggermente positivo → leggero bias USD"
        elif dxy_score < -15:
            bias = "strong_anti_usd"
            description = f"DXY in forte calo ({dxy_momentum_1h:.2f}%) → long EUR/GBP/AUD"
        elif dxy_score < -5:
            bias = "mild_anti_usd"
            description = f"DXY leggermente negativo → leggero bias anti-USD"
        else:
            bias = "neutral"
            description = "DXY laterale — nessun bias forte"

        return {
            "bias": bias,
            "score": round(dxy_score, 2),
            "momentum_1h": round(dxy_momentum_1h, 4),
            "momentum_4h": round(dxy_momentum_4h, 4),
            "description": description,
        }

    def get_pair_intermarket_score(self, symbol: str) -> Dict:
        """
        Calcola lo score intermarket per una specifica coppia forex.
        Combina DXY, VIX, correlazioni specifiche della coppia.
        Ritorna score da -100 a +100.
        """
        correlations = CORRELATION_MAP.get(symbol, {})
        if not correlations:
            return {"score": 0, "description": "Nessuna correlazione mappata"}

        total_score = 0
        total_weight = 0
        signals = []

        for asset, correlation in correlations.items():
            momentum = self.get_asset_momentum(asset, 4)
            if momentum == 0:
                continue

            # Score = correlazione × momentum dell'asset
            asset_score = correlation * momentum * 20
            weight = abs(correlation)
            total_score += asset_score * weight
            total_weight += weight

            if abs(asset_score) > 5:
                direction = "bullish" if asset_score > 0 else "bearish"
                signals.append(f"{asset} {direction} ({momentum:+.2f}%)")

        # Aggiungi regime bias
        regime = self.get_regime()
        regime_score = 0
        if regime["regime"] == "risk_off":
            if "JPY" in symbol:
                regime_score = regime["strength"] * 0.3
            elif "AUD" in symbol or "EUR" in symbol:
                regime_score = -regime["strength"] * 0.3
        elif regime["regime"] == "risk_on":
            if "AUD" in symbol:
                regime_score = regime["strength"] * 0.2
            elif "JPY" in symbol:
                regime_score = -regime["strength"] * 0.2

        final_score = (total_score / total_weight if total_weight > 0 else 0) + regime_score
        final_score = max(-100, min(100, final_score))

        return {
            "symbol": symbol,
            "score": round(final_score, 2),
            "regime": regime["regime"],
            "signals": signals,
            "description": f"Regime: {regime['regime']} | " + ", ".join(signals[:2]) if signals else "Mercato neutro",
        }

    def get_all_pairs_scores(self, pairs: list) -> Dict:
        """Calcola score intermarket per tutte le coppie"""
        results = {}
        regime = self.get_regime()
        dxy = self.get_dxy_bias()

        logger.info(f"🌍 Intermarket — Regime: {regime['regime']}, DXY: {dxy['bias']}")

        for pair in pairs:
            score_data = self.get_pair_intermarket_score(pair)
            results[pair] = score_data

        return {
            "pairs": results,
            "regime": regime,
            "dxy": dxy,
            "calculated_at": datetime.utcnow().isoformat(),
        }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    engine = IntermarketEngine()

    from config.settings import FOREX_PAIRS
    print("\n🌍 Intermarket Analysis:")
    results = engine.get_all_pairs_scores(FOREX_PAIRS)

    print(f"\nRegime: {results['regime']['regime']} (VIX: {results['regime']['vix_level']})")
    print(f"DXY Bias: {results['dxy']['bias']} — {results['dxy']['description']}")
    print("\nScores per coppia:")
    for pair, data in results["pairs"].items():
        print(f"  {pair}: {data['score']:+.1f} — {data['description'][:60]}")
