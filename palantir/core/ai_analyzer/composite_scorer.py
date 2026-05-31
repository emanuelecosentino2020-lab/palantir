import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from datetime import datetime
from typing import Dict, Optional
import logging

from config.settings import SENTIMENT_WEIGHTS, FOREX_PAIRS

logger = logging.getLogger(__name__)


class CompositeScorer:
    """
    Combina tutti i segnali in un unico score per ogni coppia.
    technical 60% + sentiment 30% + macro 10%
    """

    def __init__(self):
        self.weights = SENTIMENT_WEIGHTS

    def calculate_score(
        self,
        symbol: str,
        technical_score: float,
        sentiment_score: float,
        macro_score: float = 0.0,
        cot_score: float = 0.0,
    ) -> Dict:
        """
        Calcola score composito da -100 a +100.
        """
        # Score pesato
        composite = (
            technical_score * self.weights["technical"] +
            sentiment_score * self.weights["sentiment"] +
            macro_score * self.weights["macro"]
        )

        # Aggiusta leggermente con COT (se disponibile)
        if cot_score != 0:
            composite = composite * 0.9 + cot_score * 0.1

        composite = round(max(-100, min(100, composite)), 2)

        # Determina direzione e confidenza
        if composite >= 65:
            direction = "LONG"
            confidence = "HIGH"
        elif composite >= 40:
            direction = "LONG"
            confidence = "MEDIUM"
        elif composite <= -65:
            direction = "SHORT"
            confidence = "HIGH"
        elif composite <= -40:
            direction = "SHORT"
            confidence = "MEDIUM"
        else:
            direction = "NEUTRAL"
            confidence = "LOW"

        return {
            "symbol": symbol,
            "composite_score": composite,
            "direction": direction,
            "confidence": confidence,
            "technical_score": round(technical_score, 2),
            "sentiment_score": round(sentiment_score, 2),
            "macro_score": round(macro_score, 2),
            "cot_score": round(cot_score, 2),
            "calculated_at": datetime.utcnow().isoformat(),
            "tradeable": confidence in ["HIGH", "MEDIUM"] and direction != "NEUTRAL",
        }

    def score_all_pairs(self, scores_data: Dict) -> Dict:
        """
        Calcola score per tutte le coppie.
        scores_data = {symbol: {technical, sentiment, macro, cot}}
        """
        results = {}
        for symbol in FOREX_PAIRS:
            data = scores_data.get(symbol, {})
            result = self.calculate_score(
                symbol=symbol,
                technical_score=data.get("technical", 0),
                sentiment_score=data.get("sentiment", 0),
                macro_score=data.get("macro", 0),
                cot_score=data.get("cot", 0),
            )
            results[symbol] = result
            if result["tradeable"]:
                logger.info(f"⚡ {symbol}: {result['direction']} score={result['composite_score']} ({result['confidence']})")

        return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    scorer = CompositeScorer()

    # Test con dati simulati
    test_data = {
        "EUR/USD": {"technical": 72, "sentiment": 65, "macro": 30, "cot": 40},
        "GBP/USD": {"technical": -45, "sentiment": -60, "macro": -20, "cot": -30},
        "USD/JPY": {"technical": 20, "sentiment": 10, "macro": 15, "cot": 5},
    }

    results = scorer.score_all_pairs(test_data)
    print("\n📊 Composite Scores:")
    for symbol, result in results.items():
        print(f"  {symbol}: {result['composite_score']:+.1f} → {result['direction']} ({result['confidence']})")
