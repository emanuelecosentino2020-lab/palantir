import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from datetime import datetime, timezone
from typing import Dict, List, Optional
import logging

from config.settings import FOREX_PAIRS, MIN_SIGNAL_SCORE

logger = logging.getLogger(__name__)


class SignalGenerator:
    """
    Genera segnali raw basati sulle 3 edge strategies.
    I segnali passano poi al Risk Manager per l'approvazione finale.
    """

    def check_macro_momentum(self, symbol: str, technical: Dict, sentiment: Dict, macro_events: List) -> Optional[Dict]:
        """
        STRATEGY 1 — Macro Momentum
        Attivazione: dato macro esce above/below consensus + sentiment AI > 60
        Logica: il mercato sotto-reagisce ai dati macro nelle prime 15 minuti
        """
        sentiment_score = sentiment.get("combined_score", 0)
        tech_score = technical.get("score", 0)
        tech_direction = technical.get("direction", "neutral")

        # Cerca eventi macro HIGH impact recenti (ultimi 30 min)
        recent_high_impact = [
            e for e in macro_events
            if e.get("impact") == "high" and e.get("surprise") is not None
        ]

        if not recent_high_impact:
            return None

        for event in recent_high_impact:
            surprise = event.get("surprise", 0)
            currency = event.get("currency", "")

            # Verifica che l'evento impatti la coppia
            base, quote = symbol.split("/")
            if currency not in (base, quote):
                continue

            # Dato positivo + sentiment bullish
            if surprise > 0 and sentiment_score > 60 and currency == base:
                if tech_score > 20:  # Conferma tecnica minima
                    raw_score = min(100, 60 + abs(surprise) * 5 + sentiment_score * 0.3)
                    return {
                        "symbol": symbol,
                        "direction": "LONG",
                        "strategy_name": "Macro Momentum",
                        "entry_price": None,  # Sarà riempito con prezzo live
                        "raw_score": round(raw_score, 2),
                        "atr": technical.get("atr", 0.001),
                        "reasoning": f"{event['name']} above consensus (surprise: {surprise:+.2f}). Sentiment: {sentiment_score:+.0f}",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }

            # Dato negativo + sentiment bearish
            if surprise < 0 and sentiment_score < -60 and currency == base:
                if tech_score < -20:
                    raw_score = min(100, 60 + abs(surprise) * 5 + abs(sentiment_score) * 0.3)
                    return {
                        "symbol": symbol,
                        "direction": "SHORT",
                        "strategy_name": "Macro Momentum",
                        "entry_price": None,
                        "raw_score": round(raw_score, 2),
                        "atr": technical.get("atr", 0.001),
                        "reasoning": f"{event['name']} below consensus (surprise: {surprise:+.2f}). Sentiment: {sentiment_score:+.0f}",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }

        return None

    def check_sentiment_divergence(self, symbol: str, technical: Dict, sentiment: Dict, cot_score: float = 0) -> Optional[Dict]:
        """
        STRATEGY 2 — Sentiment Divergence
        Attivazione: price action laterale ma sentiment diverge nettamente (>70) + COT in accordo
        Logica: il mercato non ha ancora prezzato il cambio di sentiment
        """
        sentiment_score = sentiment.get("combined_score", 0)
        tech_score = technical.get("score", 0)
        tech_strength = technical.get("strength", 0)

        # Price action deve essere laterale/debole
        if tech_strength > 50:
            return None  # Trend già forte, niente divergenza

        # Sentiment deve essere forte
        if abs(sentiment_score) < 70:
            return None

        # COT deve essere in accordo (o neutro)
        if sentiment_score > 0 and cot_score < -30:
            return None  # COT contrario al sentiment bullish
        if sentiment_score < 0 and cot_score > 30:
            return None  # COT contrario al sentiment bearish

        raw_score = min(100, abs(sentiment_score) * 0.7 + abs(cot_score) * 0.3)

        if raw_score < MIN_SIGNAL_SCORE:
            return None

        direction = "LONG" if sentiment_score > 0 else "SHORT"

        return {
            "symbol": symbol,
            "direction": direction,
            "strategy_name": "Sentiment Divergence",
            "entry_price": None,
            "raw_score": round(raw_score, 2),
            "atr": technical.get("atr", 0.001),
            "reasoning": f"Sentiment forte ({sentiment_score:+.0f}) con price action laterale. COT: {cot_score:+.0f}",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    def check_technical_confluence(self, symbol: str, technical: Dict, sentiment: Dict) -> Optional[Dict]:
        """
        STRATEGY 3 — Technical Confluence
        Attivazione: 3+ indicatori tecnici allineati + sentiment non contrario
        Logica: setup tecnico classico ad alta probabilità
        """
        tech_score = technical.get("score", 0)
        tech_strength = technical.get("strength", 0)
        sentiment_score = sentiment.get("combined_score", 0)
        signals = technical.get("signals", [])
        indicators = technical.get("indicators", {})

        # Deve esserci forza tecnica significativa
        if tech_strength < 50:
            return None

        # Conta segnali tecnici allineati
        aligned_signals = len(signals)
        if aligned_signals < 2:
            return None

        # Sentiment non deve essere fortemente contrario
        if tech_score > 0 and sentiment_score < -50:
            return None  # Sentiment bearish forte contro setup bullish
        if tech_score < 0 and sentiment_score > 50:
            return None  # Sentiment bullish forte contro setup bearish

        # Bonus se c'è conferma da pattern candlestick
        patterns = technical.get("patterns", [])
        pattern_bonus = 0
        if tech_score > 0 and any("BULLISH" in p for p in patterns):
            pattern_bonus = 15
        elif tech_score < 0 and any("BEARISH" in p for p in patterns):
            pattern_bonus = 15

        raw_score = min(100, tech_strength + pattern_bonus + abs(sentiment_score) * 0.2)

        if raw_score < MIN_SIGNAL_SCORE:
            return None

        direction = "LONG" if tech_score > 0 else "SHORT"

        return {
            "symbol": symbol,
            "direction": direction,
            "strategy_name": "Technical Confluence",
            "entry_price": None,
            "raw_score": round(raw_score, 2),
            "atr": technical.get("atr", 0.001),
            "reasoning": f"Confluenza tecnica: {', '.join(signals[:3])}. Score: {tech_score:+.0f}",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    def check_all_strategies(
        self,
        symbol: str,
        technical: Dict,
        sentiment: Dict,
        macro_events: List = None,
        cot_score: float = 0,
        current_price: float = None,
    ) -> Optional[Dict]:
        """
        Controlla tutte e 3 le strategie e ritorna il segnale con score più alto.
        """
        macro_events = macro_events or []
        signals = []

        # Strategy 1
        s1 = self.check_macro_momentum(symbol, technical, sentiment, macro_events)
        if s1:
            signals.append(s1)

        # Strategy 2
        s2 = self.check_sentiment_divergence(symbol, technical, sentiment, cot_score)
        if s2:
            signals.append(s2)

        # Strategy 3
        s3 = self.check_technical_confluence(symbol, technical, sentiment)
        if s3:
            signals.append(s3)

        if not signals:
            return None

        # Prendi il segnale con score più alto
        best_signal = max(signals, key=lambda x: x["raw_score"])

        # Aggiungi entry price se disponibile
        if current_price:
            best_signal["entry_price"] = current_price

        logger.info(f"📡 Segnale generato: {symbol} {best_signal['direction']} via {best_signal['strategy_name']} (score: {best_signal['raw_score']})")
        return best_signal


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    generator = SignalGenerator()

    # Test Strategy 3 — Technical Confluence
    mock_technical = {
        "score": 72,
        "direction": "up",
        "strength": 72,
        "signals": ["RSI oversold — potenziale rimbalzo", "MACD crossover bullish", "Price above EMA200"],
        "atr": 0.0012,
        "indicators": {},
        "patterns": ["PIN_BAR_BULLISH"],
    }
    mock_sentiment = {"combined_score": 45}

    signal = generator.check_all_strategies(
        "EUR/USD", mock_technical, mock_sentiment, current_price=1.0850
    )

    if signal:
        print(f"\n📡 Segnale: {signal['symbol']} {signal['direction']}")
        print(f"  Strategia: {signal['strategy_name']}")
        print(f"  Score: {signal['raw_score']}")
        print(f"  Reasoning: {signal['reasoning']}")
    else:
        print("Nessun segnale generato")
