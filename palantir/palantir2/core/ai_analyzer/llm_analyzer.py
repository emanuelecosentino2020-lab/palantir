import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import json
import hashlib
from datetime import datetime
from typing import Dict, List, Optional
import logging
import anthropic

from config.settings import ANTHROPIC_API_KEY, LLM_MODEL
from database.models import SessionLocal, News

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Sei un analista finanziario esperto specializzato nel mercato Forex.
Il tuo compito è analizzare news finanziarie e determinare il loro impatto sulle coppie valutarie.

Rispondi SEMPRE e SOLO con un JSON valido con questa struttura esatta:
{
  "sentiment_score": <numero da -100 a +100>,
  "key_themes": [<lista di 2-4 temi chiave>],
  "expected_impact": "<high|medium|low>",
  "affected_pairs": [<lista coppie forex impattate>],
  "reasoning": "<spiegazione breve in max 2 frasi>",
  "direction": "<bullish|bearish|neutral> per la prima coppia nella lista"
}

Regole:
- sentiment_score positivo = bullish per USD, negativo = bearish per USD
- expected_impact high = muoverà il mercato significativamente
- Sii conciso e preciso"""


class LLMAnalyzer:

    def __init__(self):
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None
        self.analysis_cache = {}  # Cache in-memory

    def _get_cache_key(self, text: str) -> str:
        return hashlib.md5(text.encode()).hexdigest()

    def _get_cached_analysis(self, cache_key: str) -> Optional[Dict]:
        """Controlla cache nel DB prima di chiamare l'API"""
        db = SessionLocal()
        try:
            news = db.query(News).filter(
                News.url == f"cache_{cache_key}",
                News.sentiment_analyzed == True
            ).first()
            if news and news.sentiment_score is not None:
                return {"sentiment_score": news.sentiment_score, "cached": True}
        except Exception:
            pass
        finally:
            db.close()
        return None

    def analyze_news(self, news_list: List[Dict], symbol: str) -> Dict:
        """
        Analizza una lista di news per una coppia forex usando Claude.
        Usa cache aggressiva per ridurre i costi API.
        """
        if not self.client:
            logger.warning("Claude API non configurata — uso sentiment base")
            return self._fallback_sentiment(news_list, symbol)

        if not news_list:
            return {"sentiment_score": 0, "expected_impact": "low", "reasoning": "Nessuna news disponibile"}

        # Prepara testo news (max 10 news, max 200 char per titolo)
        news_text = "\n".join([
            f"- {n.get('title', '')[:200]}"
            for n in news_list[:10]
        ])

        cache_key = self._get_cache_key(f"{symbol}_{news_text[:500]}")

        # Controlla cache in-memory
        if cache_key in self.analysis_cache:
            logger.debug(f"LLM cache hit per {symbol}")
            return self.analysis_cache[cache_key]

        # Chiama Claude API
        try:
            user_message = f"""Analizza queste news per la coppia {symbol}:

{news_text}

Contesto mercato attuale: analisi intraday, timeframe H1."""

            message = self.client.messages.create(
                model=LLM_MODEL,
                max_tokens=500,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}]
            )

            response_text = message.content[0].text.strip()

            # Pulisci eventuali markdown
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0].strip()

            result = json.loads(response_text)
            result["source"] = "claude"
            result["analyzed_at"] = datetime.utcnow().isoformat()

            # Salva in cache
            self.analysis_cache[cache_key] = result
            logger.info(f"✅ LLM {symbol}: score={result.get('sentiment_score', 0)}, impact={result.get('expected_impact', 'N/A')}")
            return result

        except json.JSONDecodeError as e:
            logger.error(f"Errore parsing JSON da Claude: {e}")
            return self._fallback_sentiment(news_list, symbol)
        except Exception as e:
            logger.error(f"Errore Claude API: {e}")
            return self._fallback_sentiment(news_list, symbol)

    def _fallback_sentiment(self, news_list: List[Dict], symbol: str) -> Dict:
        """Sentiment base senza AI — conta parole chiave"""
        BULLISH = ["rally", "surge", "rise", "gain", "strong", "hawkish", "bullish", "beat", "above"]
        BEARISH = ["fall", "drop", "crash", "weak", "dovish", "bearish", "miss", "below", "concern"]

        score = 0
        for news in news_list[:10]:
            text = (news.get("title", "") + " " + news.get("summary", "")).lower()
            score += sum(1 for w in BULLISH if w in text)
            score -= sum(1 for w in BEARISH if w in text)

        normalized = max(-100, min(100, score * 10))
        return {
            "sentiment_score": normalized,
            "expected_impact": "medium" if abs(normalized) > 30 else "low",
            "reasoning": "Analisi keyword base (Claude API non disponibile)",
            "source": "fallback",
        }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    analyzer = LLMAnalyzer()

    test_news = [
        {"title": "Fed signals rate cut ahead amid cooling inflation data"},
        {"title": "EUR/USD rallies as ECB maintains hawkish stance"},
        {"title": "US jobs data beats expectations, dollar strengthens"},
    ]

    result = analyzer.analyze_news(test_news, "EUR/USD")
    print(f"\n🤖 LLM Analysis EUR/USD:")
    print(f"  Score: {result.get('sentiment_score', 0)}")
    print(f"  Impact: {result.get('expected_impact', 'N/A')}")
    print(f"  Reasoning: {result.get('reasoning', 'N/A')}")
    print(f"  Source: {result.get('source', 'N/A')}")
