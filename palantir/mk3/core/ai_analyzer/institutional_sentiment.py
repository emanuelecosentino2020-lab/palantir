import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import requests
import feedparser
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
import logging
import json

from config.settings import ANTHROPIC_API_KEY, LLM_MODEL

logger = logging.getLogger(__name__)

# Fonti pubbliche di sentiment istituzionale
INSTITUTIONAL_SOURCES = {
    "forexlive_analysis": "https://www.forexlive.com/feed/analysis",
    "investing_analysis": "https://www.investing.com/rss/news_25.rss",
    "fxstreet": "https://www.fxstreet.com/rss/news",
    "dailyfx_analysis": "https://www.dailyfx.com/feeds/all",
    "action_forex": "https://www.actionforex.com/feed/",
}

# Keyword che indicano bias istituzionale
BULLISH_INSTITUTIONAL = [
    "upgrade", "overweight", "buy", "long", "bullish", "outperform",
    "target raised", "positive outlook", "upside", "accumulate",
    "hawkish surprise", "above forecast", "beat expectations",
]
BEARISH_INSTITUTIONAL = [
    "downgrade", "underweight", "sell", "short", "bearish", "underperform",
    "target cut", "negative outlook", "downside", "reduce",
    "dovish surprise", "below forecast", "miss expectations",
]

# Banche e istituzioni di riferimento
KEY_INSTITUTIONS = [
    "goldman sachs", "jpmorgan", "citibank", "deutsche bank",
    "barclays", "ubs", "morgan stanley", "bank of america",
    "hsbc", "nomura", "societe generale", "bnp paribas",
    "imf", "world bank", "federal reserve", "ecb", "boe", "boj",
]


class InstitutionalSentimentAnalyzer:
    """
    Analizza il sentiment istituzionale leggendo:
    1. Note di ricerca e analisi delle grandi banche
    2. Dichiarazioni di banche centrali
    3. Economic surprise index
    
    Questo è il layer di intelligenza che i retail trader non hanno.
    """

    def __init__(self):
        self._cache = {}
        self._cache_time = {}
        self.cache_ttl = 3600  # 1 ora

    def _is_cached(self, key: str) -> bool:
        if key not in self._cache_time:
            return False
        return (datetime.now(timezone.utc) - self._cache_time[key]).seconds < self.cache_ttl

    def fetch_institutional_news(self) -> List[Dict]:
        """Raccoglie news e analisi da fonti istituzionali"""
        if self._is_cached("inst_news"):
            return self._cache["inst_news"]

        all_news = []
        for source_name, url in INSTITUTIONAL_SOURCES.items():
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:15]:
                    title = getattr(entry, 'title', '')
                    summary = getattr(entry, 'summary', '')[:500]
                    if not title:
                        continue

                    # Filtra solo contenuti con menzione di istituzioni chiave
                    combined = (title + " " + summary).lower()
                    has_institution = any(inst in combined for inst in KEY_INSTITUTIONS)
                    has_forex = any(kw in combined for kw in ['forex', 'eur', 'usd', 'gbp', 'jpy', 'aud', 'cad', 'currency', 'rate'])

                    all_news.append({
                        "source": source_name,
                        "title": title,
                        "summary": summary,
                        "is_institutional": has_institution,
                        "is_forex_relevant": has_forex,
                        "published_at": datetime.now(timezone.utc).isoformat(),
                    })
            except Exception as e:
                logger.error(f"Errore {source_name}: {e}")

        self._cache["inst_news"] = all_news
        self._cache_time["inst_news"] = datetime.now(timezone.utc)
        logger.info(f"✅ Institutional news: {len(all_news)} articoli ({sum(1 for n in all_news if n['is_institutional'])} istituzionali)")
        return all_news

    def analyze_with_claude(self, news_list: List[Dict], symbol: str) -> Dict:
        """
        Usa Claude per analizzare il sentiment istituzionale su una coppia.
        Estrae bias delle grandi banche e target price.
        """
        if not ANTHROPIC_API_KEY:
            return self._keyword_sentiment(news_list, symbol)

        # Filtra news rilevanti
        relevant = [n for n in news_list if n.get('is_forex_relevant')][:8]
        if not relevant:
            return {"score": 0, "institutional_bias": "neutral", "key_institutions": []}

        news_text = "\n".join([f"- {n['title']}: {n['summary'][:150]}" for n in relevant])

        try:
            import anthropic
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

            prompt = f"""Analizza queste news/analisi per {symbol} da fonti finanziarie professionali:

{news_text}

Rispondi SOLO con JSON:
{{
  "sentiment_score": <-100 a +100>,
  "institutional_bias": "<bullish|bearish|neutral>",
  "key_institutions": [<lista istituzioni menzionate>],
  "consensus": "<descrizione breve del consensus istituzionale>",
  "key_risk": "<principale rischio identificato>",
  "time_horizon": "<short|medium|long>"
}}"""

            response = client.messages.create(
                model=LLM_MODEL,
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}]
            )

            text = response.content[0].text.strip()
            if "```" in text:
                text = text.split("```")[1].replace("json", "").strip()

            result = json.loads(text)
            result["source"] = "claude_institutional"
            logger.info(f"✅ Institutional sentiment {symbol}: {result.get('sentiment_score', 0)} ({result.get('institutional_bias', 'N/A')})")
            return result

        except Exception as e:
            logger.error(f"Errore Claude institutional: {e}")
            return self._keyword_sentiment(news_list, symbol)

    def _keyword_sentiment(self, news_list: List[Dict], symbol: str) -> Dict:
        """Fallback sentiment basato su keyword"""
        score = 0
        institutions_found = []

        for news in news_list[:10]:
            text = (news.get('title', '') + ' ' + news.get('summary', '')).lower()
            score += sum(1 for kw in BULLISH_INSTITUTIONAL if kw in text)
            score -= sum(1 for kw in BEARISH_INSTITUTIONAL if kw in text)
            for inst in KEY_INSTITUTIONS:
                if inst in text and inst not in institutions_found:
                    institutions_found.append(inst)

        normalized = max(-100, min(100, score * 15))
        return {
            "sentiment_score": normalized,
            "institutional_bias": "bullish" if normalized > 20 else "bearish" if normalized < -20 else "neutral",
            "key_institutions": institutions_found[:3],
            "consensus": "Analisi keyword (Claude non disponibile)",
            "source": "keyword_fallback",
        }

    def get_economic_surprise_index(self) -> Dict:
        """
        Proxy dell'Economic Surprise Index.
        Costruisce un indice basato su: dati FRED recenti vs attese storiche.
        """
        if self._is_cached("esi"):
            return self._cache["esi"]

        try:
            # Usa dati FRED per costruire surprise index approssimato
            import requests

            # NFP recente
            nfp_url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=PAYEMS"
            response = requests.get(nfp_url, timeout=10)
            lines = response.text.strip().split('\n')
            recent_values = []
            for line in lines[-6:]:
                parts = line.split(',')
                if len(parts) >= 2 and parts[1].strip() != '.':
                    try:
                        recent_values.append(float(parts[1].strip()))
                    except ValueError:
                        pass

            if len(recent_values) >= 3:
                # Sorpresa = differenza dall'ultimo valore vs media recente
                latest = recent_values[-1]
                avg = sum(recent_values[:-1]) / len(recent_values[:-1])
                momentum = ((latest - avg) / avg) * 100

                esi_score = max(-100, min(100, momentum * 10))
                bias = "positive" if esi_score > 10 else "negative" if esi_score < -10 else "neutral"
            else:
                esi_score = 0
                bias = "neutral"

            result = {
                "us_esi_score": round(esi_score, 2),
                "us_bias": bias,
                "interpretation": f"Dati macro USA {'meglio' if esi_score > 0 else 'peggio'} delle attese recenti",
                "usd_implication": "bullish" if esi_score > 10 else "bearish" if esi_score < -10 else "neutral",
            }

            self._cache["esi"] = result
            self._cache_time["esi"] = datetime.now(timezone.utc)
            logger.info(f"✅ ESI proxy: {esi_score:.1f} ({bias})")
            return result

        except Exception as e:
            logger.error(f"Errore ESI: {e}")
            return {"us_esi_score": 0, "us_bias": "neutral", "usd_implication": "neutral"}

    def get_full_institutional_score(self, symbol: str) -> Dict:
        """Score istituzionale completo per una coppia"""
        news = self.fetch_institutional_news()
        claude_analysis = self.analyze_with_claude(news, symbol)
        esi = self.get_economic_surprise_index()

        # Combina sentiment Claude + ESI
        claude_score = claude_analysis.get("sentiment_score", 0)
        esi_score = esi.get("us_esi_score", 0)

        # Per coppie USD: ESI impatta direttamente
        base, quote = symbol.split("/")
        if quote == "USD":
            esi_impact = -esi_score * 0.3  # USD forte = coppia scende
        elif base == "USD":
            esi_impact = esi_score * 0.3   # USD forte = coppia sale
        else:
            esi_impact = 0

        final_score = claude_score * 0.7 + esi_impact
        final_score = max(-100, min(100, final_score))

        return {
            "symbol": symbol,
            "institutional_score": round(final_score, 2),
            "claude_sentiment": claude_score,
            "esi_score": esi_score,
            "institutional_bias": claude_analysis.get("institutional_bias", "neutral"),
            "key_institutions": claude_analysis.get("key_institutions", []),
            "consensus": claude_analysis.get("consensus", ""),
            "calculated_at": datetime.now(timezone.utc).isoformat(),
        }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    analyzer = InstitutionalSentimentAnalyzer()

    print("\n🏦 Institutional Sentiment Analysis:")
    result = analyzer.get_full_institutional_score("EUR/USD")
    print(f"  Score: {result['institutional_score']}")
    print(f"  Bias: {result['institutional_bias']}")
    print(f"  ESI: {result['esi_score']}")
    print(f"  Consensus: {result['consensus'][:80]}")

    esi = analyzer.get_economic_surprise_index()
    print(f"\n📊 Economic Surprise Index (proxy):")
    print(f"  US ESI: {esi['us_esi_score']}")
    print(f"  USD implication: {esi['usd_implication']}")
