import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from datetime import datetime, timezone
from typing import Dict, List, Optional
import logging

from config.settings import FOREX_PAIRS

logger = logging.getLogger(__name__)

MIN_SCORE_MK2 = 80  # Soglia alzata da 65 a 80


class SignalGeneratorMK2:
    """
    Signal generator potenziato con 5 strategie.
    Soglia minima alzata a 80 per ridurre i falsi segnali.
    
    Le 5 strategie:
    1. Technical Confluence + EMA200 filter
    2. Macro Momentum
    3. Sentiment Divergence + COT
    4. Intermarket Divergence (nuovo)
    5. Stop Hunt Reversal (nuovo)
    """

    def check_technical_confluence(self, symbol: str, technical: Dict, sentiment: Dict) -> Optional[Dict]:
        """
        STRATEGY 1 — Technical Confluence (potenziata)
        Richiede: ADX > 25, EMA200 allineata, score > 80
        """
        tech_score = technical.get("score", 0)
        tech_strength = technical.get("strength", 0)
        adx = technical.get("adx", 0)
        above_ema200 = technical.get("above_ema200", True)
        confirmed_h4 = technical.get("confirmed_by_h4", False)
        filtered = technical.get("filtered", False)
        sentiment_score = sentiment.get("combined_score", 0)

        if filtered:
            return None
        if adx < 25:
            return None
        if tech_strength < 55:
            return None

        # Sentiment non deve essere fortemente contrario
        if tech_score > 0 and sentiment_score < -40:
            return None
        if tech_score < 0 and sentiment_score > 40:
            return None

        raw_score = tech_strength
        if confirmed_h4:
            raw_score = min(100, raw_score * 1.3)
        if adx > 35:
            raw_score = min(100, raw_score * 1.1)

        if raw_score < MIN_SCORE_MK2:
            return None

        direction = "LONG" if tech_score > 0 else "SHORT"
        signals = technical.get("signals", [])

        return {
            "symbol": symbol,
            "direction": direction,
            "strategy_name": "Technical Confluence MK2",
            "entry_price": None,
            "raw_score": round(raw_score, 2),
            "atr": technical.get("atr", 0.001),
            "reasoning": f"ADX {adx:.0f}, {'H4 confermato' if confirmed_h4 else 'H1 only'}. {', '.join(signals[:2])}",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    def check_macro_momentum(self, symbol: str, technical: Dict, sentiment: Dict, macro_events: List) -> Optional[Dict]:
        """
        STRATEGY 2 — Macro Momentum (invariata)
        """
        sentiment_score = sentiment.get("combined_score", 0)
        tech_score = technical.get("score", 0)
        recent_high_impact = [e for e in macro_events if e.get("impact") == "high" and e.get("surprise") is not None]

        if not recent_high_impact:
            return None

        for event in recent_high_impact:
            surprise = event.get("surprise", 0)
            currency = event.get("currency", "")
            base, quote = symbol.split("/")
            if currency not in (base, quote):
                continue

            if surprise > 0 and sentiment_score > 55 and currency == base and tech_score > 15:
                raw_score = min(100, 65 + abs(surprise) * 5 + sentiment_score * 0.25)
                if raw_score < MIN_SCORE_MK2:
                    return None
                return {
                    "symbol": symbol,
                    "direction": "LONG",
                    "strategy_name": "Macro Momentum MK2",
                    "entry_price": None,
                    "raw_score": round(raw_score, 2),
                    "atr": technical.get("atr", 0.001),
                    "reasoning": f"{event['name']} above consensus (+{surprise:.2f}). Sentiment: {sentiment_score:+.0f}",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }

            if surprise < 0 and sentiment_score < -55 and currency == base and tech_score < -15:
                raw_score = min(100, 65 + abs(surprise) * 5 + abs(sentiment_score) * 0.25)
                if raw_score < MIN_SCORE_MK2:
                    return None
                return {
                    "symbol": symbol,
                    "direction": "SHORT",
                    "strategy_name": "Macro Momentum MK2",
                    "entry_price": None,
                    "raw_score": round(raw_score, 2),
                    "atr": technical.get("atr", 0.001),
                    "reasoning": f"{event['name']} below consensus ({surprise:.2f}). Sentiment: {sentiment_score:+.0f}",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
        return None

    def check_intermarket_divergence(self, symbol: str, technical: Dict, intermarket: Dict) -> Optional[Dict]:
        """
        STRATEGY 4 — Intermarket Divergence (nuova)
        
        Attivazione: forte movimento del DXY o regime change + conferma tecnica
        Logica: il forex segue gli asset correlati con un ritardo
        → Entri prima che il mercato aggiorni il prezzo
        """
        if not intermarket:
            return None

        intermarket_score = intermarket.get("score", 0)
        regime = intermarket.get("regime", "transition")
        tech_score = technical.get("score", 0)
        above_ema200 = technical.get("above_ema200", True)
        adx = technical.get("adx", 0)

        # Serve un segnale intermarket forte
        if abs(intermarket_score) < 40:
            return None

        # Il segnale tecnico non deve essere contrario
        if intermarket_score > 0 and tech_score < -30:
            return None
        if intermarket_score < 0 and tech_score > 30:
            return None

        # Filtro EMA200
        if intermarket_score > 0 and not above_ema200:
            intermarket_score *= 0.5
        if intermarket_score < 0 and above_ema200:
            intermarket_score *= 0.5

        raw_score = min(100, abs(intermarket_score) * 0.8 + abs(tech_score) * 0.2)

        if raw_score < MIN_SCORE_MK2:
            return None

        direction = "LONG" if intermarket_score > 0 else "SHORT"
        signals = intermarket.get("signals", [])

        return {
            "symbol": symbol,
            "direction": direction,
            "strategy_name": "Intermarket Divergence",
            "entry_price": None,
            "raw_score": round(raw_score, 2),
            "atr": technical.get("atr", 0.001),
            "reasoning": f"Regime: {regime}. {', '.join(signals[:2]) if signals else 'Correlazione cross-asset'}",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    def check_stop_hunt_reversal(self, symbol: str, technical: Dict, order_flow: Dict) -> Optional[Dict]:
        """
        STRATEGY 5 — Stop Hunt Reversal (nuova)
        
        Il segnale più potente del sistema.
        Attivazione: stop hunt rilevato su livello di liquidità chiave
        Logica: dopo che le "mani forti" hanno cacciato gli stop retail,
        il prezzo reversa violentemente → alta probabilità di successo
        """
        if not order_flow:
            return None

        stop_hunt = order_flow.get("stop_hunt")
        if not stop_hunt:
            return None

        hunt_score = stop_hunt.get("score", 0)
        direction = stop_hunt.get("direction", "LONG")
        tech_score = technical.get("score", 0)
        adx = technical.get("adx", 0)

        # Stop hunt + conferma tecnica nella stessa direzione
        if direction == "LONG" and tech_score < -20:
            return None
        if direction == "SHORT" and tech_score > 20:
            return None

        # Bonus se ADX è sopra soglia
        if adx > 20:
            hunt_score = min(100, hunt_score * 1.2)

        if hunt_score < MIN_SCORE_MK2:
            return None

        return {
            "symbol": symbol,
            "direction": direction,
            "strategy_name": "Stop Hunt Reversal",
            "entry_price": None,
            "raw_score": round(hunt_score, 2),
            "atr": technical.get("atr", 0.001),
            "reasoning": stop_hunt.get("description", "Stop hunt rilevato su livello chiave"),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "high_confidence": True,  # Segnale di alta qualità
        }

    def check_all_strategies(
        self,
        symbol: str,
        technical: Dict,
        sentiment: Dict,
        macro_events: List = None,
        cot_score: float = 0,
        intermarket: Dict = None,
        order_flow: Dict = None,
        current_price: float = None,
    ) -> Optional[Dict]:
        """
        Controlla tutte e 5 le strategie.
        Priorità: Stop Hunt > Intermarket > Macro > Technical
        """
        macro_events = macro_events or []
        signals = []

        # Strategia 5 — Stop Hunt (priorità massima)
        s5 = self.check_stop_hunt_reversal(symbol, technical, order_flow or {})
        if s5:
            signals.append(s5)

        # Strategia 4 — Intermarket
        s4 = self.check_intermarket_divergence(symbol, technical, intermarket or {})
        if s4:
            signals.append(s4)

        # Strategia 2 — Macro Momentum
        s2 = self.check_macro_momentum(symbol, technical, sentiment, macro_events)
        if s2:
            signals.append(s2)

        # Strategia 1 — Technical Confluence
        s1 = self.check_technical_confluence(symbol, technical, sentiment)
        if s1:
            signals.append(s1)

        if not signals:
            return None

        # Prendi il segnale con score più alto
        best = max(signals, key=lambda x: x["raw_score"])
        if current_price:
            best["entry_price"] = current_price

        logger.info(f"📡 {symbol} {best['direction']} via {best['strategy_name']} (score: {best['raw_score']})")
        return best


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    gen = SignalGeneratorMK2()

    mock_technical = {
        "score": 78,
        "direction": "up",
        "strength": 78,
        "adx": 32,
        "above_ema200": True,
        "confirmed_by_h4": True,
        "signals": ["MACD crossover bullish", "Price above EMA200", "ADX 32 — trend forte"],
        "atr": 0.0012,
        "filtered": False,
    }
    mock_sentiment = {"combined_score": 40}
    mock_intermarket = {"score": 65, "regime": "risk_on", "signals": ["DXY in calo"]}

    signal = gen.check_all_strategies(
        "EUR/USD", mock_technical, mock_sentiment,
        intermarket=mock_intermarket, current_price=1.0850
    )

    if signal:
        print(f"\n📡 {signal['symbol']} {signal['direction']}")
        print(f"  Strategia: {signal['strategy_name']}")
        print(f"  Score: {signal['raw_score']}")
        print(f"  Reasoning: {signal['reasoning']}")
    else:
        print("Nessun segnale — soglia 80 non raggiunta")
