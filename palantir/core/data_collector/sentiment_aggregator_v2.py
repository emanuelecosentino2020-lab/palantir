import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import requests
import praw
import feedparser
import json
import re
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from collections import Counter
import logging

from config.settings import (
    ANTHROPIC_API_KEY, LLM_MODEL, FOREX_PAIRS,
    REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT,
    TWITTER_BEARER_TOKEN,
)

logger = logging.getLogger(__name__)

# ── FONTI PER LIVELLO ────────────────────────────────────────────────────────

# Livello 1: Istituzionale — banche, hedge fund, analisti senior
INSTITUTIONAL_SOURCES = {
    # Research notes pubbliche
    "imf_blog": "https://www.imf.org/en/Blogs/rss",
    "bis_speeches": "https://www.bis.org/rss/topics_cbspeeches.rss",
    "fed_speeches": "https://www.federalreserve.gov/feeds/speeches.xml",
    "ecb_publications": "https://www.ecb.europa.eu/rss/press.html",
    "world_bank": "https://blogs.worldbank.org/rss.xml",
    "brookings": "https://www.brookings.edu/topic/economy/feed/",
    # Analisi professionali
    "fxstreet_analysis": "https://www.fxstreet.com/rss/analysis",
    "action_forex": "https://www.actionforex.com/feed/",
    "forexlive_analysis": "https://www.forexlive.com/feed/analysis",
}

INSTITUTIONAL_KEYWORDS = {
    "bullish_signals": [
        "upgrade", "overweight", "buy rating", "target raised", "outperform",
        "positive outlook", "above consensus", "beat expectations", "hawkish",
        "rate hike", "tightening", "strong growth", "resilient", "upside risk",
    ],
    "bearish_signals": [
        "downgrade", "underweight", "sell rating", "target cut", "underperform",
        "negative outlook", "below consensus", "miss expectations", "dovish",
        "rate cut", "easing", "weak growth", "recession risk", "downside risk",
    ],
    "key_institutions": [
        "goldman sachs", "jpmorgan", "jp morgan", "morgan stanley", "citibank",
        "citigroup", "deutsche bank", "barclays", "ubs", "credit suisse",
        "hsbc", "bnp paribas", "societe generale", "nomura", "mizuho",
        "bank of america", "wells fargo", "blackrock", "pimco", "vanguard",
        "bridgewater", "ray dalio", "imf", "world bank", "oecd",
    ],
}

# Livello 2: Semi-professionale — trader esperti, fintwit, analisti indipendenti
SEMIPRO_SOURCES = {
    "zerohedge": "https://feeds.feedburner.com/zerohedge/feed",
    "seeking_alpha": "https://seekingalpha.com/market_currents.xml",
    "forexlive_news": "https://www.forexlive.com/feed/news",
    "dailyfx": "https://www.dailyfx.com/feeds/all",
    "babypips": "https://www.babypips.com/learn/forex/feed",
}

# Livello 3: Retail — Reddit, forum, social
RETAIL_SUBREDDITS = [
    "Forex", "investing", "economics", "wallstreetbets",
    "stocks", "Trading", "algotrading", "MacroEconomics",
]


class MultiLevelSentimentAggregator:
    """
    Analisi sentiment a 3 livelli — come fanno i desk istituzionali.
    
    Livello 1 — Istituzionale (peso 50%):
    Banche, hedge fund, banche centrali, FMI. 
    Questi muovono il mercato. Il loro sentiment è il più predittivo.
    
    Livello 2 — Semi-professionale (peso 30%):
    Trader esperti, analisti indipendenti, fintwit.
    Rappresentano il "mercato informato" — spesso precede il retail.
    
    Livello 3 — Retail (peso 20%):
    Reddit, forum, social. 
    Indicatore contrarian — quando il retail è all-in su una direzione,
    spesso è il momento di fare il contrario.
    """

    def __init__(self):
        self._cache: Dict = {}
        self._cache_ttl = 1800  # 30 minuti
        self.reddit = self._init_reddit()

    def _init_reddit(self):
        if not REDDIT_CLIENT_ID or not REDDIT_CLIENT_SECRET:
            logger.info("Reddit non configurato — sentiment retail da fonti alternative")
            return None
        try:
            return praw.Reddit(
                client_id=REDDIT_CLIENT_ID,
                client_secret=REDDIT_CLIENT_SECRET,
                user_agent=REDDIT_USER_AGENT,
            )
        except Exception as e:
            logger.error(f"Errore Reddit init: {e}")
            return None

    def _is_cached(self, key: str) -> bool:
        if key not in self._cache:
            return False
        age = (datetime.now(timezone.utc) - self._cache[key]["time"]).seconds
        return age < self._cache_ttl

    def _keyword_score(self, text: str, bullish_list: List[str], bearish_list: List[str]) -> float:
        text_lower = text.lower()
        bull = sum(1 for w in bullish_list if w in text_lower)
        bear = sum(1 for w in bearish_list if w in text_lower)
        total = bull + bear
        if total == 0:
            return 0.0
        return ((bull - bear) / total) * 100

    # ── LIVELLO 1: ISTITUZIONALE ─────────────────────────────────────────────

    def get_institutional_sentiment(self, symbol: str) -> Dict:
        """Raccoglie e analizza sentiment istituzionale"""
        cache_key = f"inst_{symbol}"
        if self._is_cached(cache_key):
            return self._cache[cache_key]["data"]

        base, quote = symbol.split("/")
        all_items = []

        for source_name, url in INSTITUTIONAL_SOURCES.items():
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:10]:
                    title = getattr(entry, 'title', '')
                    summary = getattr(entry, 'summary', '')[:300]
                    combined = (title + " " + summary).lower()

                    # Filtra per rilevanza coppia
                    if not any(c.lower() in combined for c in [base, quote, "forex", "currency", "rate"]):
                        continue

                    # Cerca menzioni di istituzioni chiave
                    institutions_found = [
                        inst for inst in INSTITUTIONAL_KEYWORDS["key_institutions"]
                        if inst in combined
                    ]

                    score = self._keyword_score(
                        combined,
                        INSTITUTIONAL_KEYWORDS["bullish_signals"],
                        INSTITUTIONAL_KEYWORDS["bearish_signals"],
                    )

                    weight = 2.0 if institutions_found else 1.0

                    all_items.append({
                        "source": source_name,
                        "title": title[:100],
                        "score": score,
                        "weight": weight,
                        "institutions": institutions_found,
                    })
            except Exception as e:
                logger.debug(f"Errore {source_name}: {e}")

        if not all_items:
            result = {"score": 0, "level": "institutional", "items_analyzed": 0, "symbol": symbol}
        else:
            weighted_score = sum(i["score"] * i["weight"] for i in all_items)
            total_weight = sum(i["weight"] for i in all_items)
            final_score = weighted_score / total_weight if total_weight > 0 else 0

            # Istituzioni menzionate
            all_institutions = []
            for item in all_items:
                all_institutions.extend(item["institutions"])
            top_institutions = [inst for inst, _ in Counter(all_institutions).most_common(3)]

            result = {
                "score": round(final_score, 2),
                "level": "institutional",
                "items_analyzed": len(all_items),
                "symbol": symbol,
                "top_institutions": top_institutions,
                "bias": "bullish" if final_score > 15 else "bearish" if final_score < -15 else "neutral",
            }

        self._cache[cache_key] = {"data": result, "time": datetime.now(timezone.utc)}
        logger.info(f"🏦 Inst sentiment {symbol}: {result['score']:.1f} ({result.get('bias')}) — {result['items_analyzed']} items")
        return result

    # ── LIVELLO 2: SEMI-PROFESSIONALE ────────────────────────────────────────

    def get_semipro_sentiment(self, symbol: str) -> Dict:
        """Raccoglie sentiment da trader esperti e analisti indipendenti"""
        cache_key = f"semipro_{symbol}"
        if self._is_cached(cache_key):
            return self._cache[cache_key]["data"]

        base, quote = symbol.split("/")
        scores = []

        for source_name, url in SEMIPRO_SOURCES.items():
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:8]:
                    title = getattr(entry, 'title', '')
                    summary = getattr(entry, 'summary', '')[:300]
                    combined = (title + " " + summary).lower()

                    if not any(c.lower() in combined for c in [base, quote, symbol.replace("/", "")]):
                        continue

                    # Sentiment keywords più tecniche per analisti
                    bullish = ["breakout", "bullish", "long", "buy", "upside", "rally",
                               "support holding", "higher", "bounce", "accumulate"]
                    bearish = ["breakdown", "bearish", "short", "sell", "downside", "drop",
                               "resistance", "lower", "decline", "distribute"]

                    score = self._keyword_score(combined, bullish, bearish)
                    scores.append(score)

            except Exception as e:
                logger.debug(f"Errore semipro {source_name}: {e}")

        final_score = sum(scores) / len(scores) if scores else 0
        result = {
            "score": round(final_score, 2),
            "level": "semipro",
            "items_analyzed": len(scores),
            "symbol": symbol,
            "bias": "bullish" if final_score > 15 else "bearish" if final_score < -15 else "neutral",
        }

        self._cache[cache_key] = {"data": result, "time": datetime.now(timezone.utc)}
        logger.info(f"📊 Semipro sentiment {symbol}: {result['score']:.1f}")
        return result

    # ── LIVELLO 3: RETAIL ────────────────────────────────────────────────────

    def get_retail_sentiment(self, symbol: str) -> Dict:
        """
        Raccoglie sentiment retail da Reddit.
        
        IMPORTANTE: Il sentiment retail è spesso un indicatore CONTRARIAN.
        Quando r/Forex è all-in LONG su EUR/USD, spesso è il top del movimento.
        Il sistema usa questo come filtro aggiuntivo, non come segnale diretto.
        """
        cache_key = f"retail_{symbol}"
        if self._is_cached(cache_key):
            return self._cache[cache_key]["data"]

        base, quote = symbol.split("/")
        all_posts = []

        if self.reddit:
            for subreddit_name in RETAIL_SUBREDDITS[:3]:
                try:
                    subreddit = self.reddit.subreddit(subreddit_name)
                    search_terms = [symbol, base, f"{base}/{quote}"]

                    for term in search_terms[:2]:
                        for post in subreddit.search(term, limit=10, time_filter="day"):
                            text = (post.title + " " + post.selftext[:200]).lower()
                            bullish = ["bullish", "long", "buy", "moon", "up", "calls", "bull"]
                            bearish = ["bearish", "short", "sell", "down", "puts", "bear", "crash"]
                            score = self._keyword_score(text, bullish, bearish)

                            all_posts.append({
                                "score": score,
                                "upvotes": post.score,
                                "upvote_ratio": post.upvote_ratio,
                                "subreddit": subreddit_name,
                            })
                except Exception as e:
                    logger.debug(f"Errore Reddit {subreddit_name}: {e}")

        if not all_posts:
            result = {"score": 0, "level": "retail", "items_analyzed": 0, "symbol": symbol,
                      "contrarian_signal": False}
        else:
            # Peso per upvotes
            weighted = sum(p["score"] * max(1, p["upvotes"]) * p["upvote_ratio"] for p in all_posts)
            total_w = sum(max(1, p["upvotes"]) * p["upvote_ratio"] for p in all_posts)
            final_score = weighted / total_w if total_w > 0 else 0

            # Segnale contrarian: se retail > 70 o < -70, considera il contrario
            contrarian = abs(final_score) > 70
            contrarian_direction = "SHORT" if final_score > 70 else "LONG" if final_score < -70 else None

            result = {
                "score": round(final_score, 2),
                "level": "retail",
                "items_analyzed": len(all_posts),
                "symbol": symbol,
                "bias": "bullish" if final_score > 15 else "bearish" if final_score < -15 else "neutral",
                "contrarian_signal": contrarian,
                "contrarian_direction": contrarian_direction,
            }

        self._cache[cache_key] = {"data": result, "time": datetime.now(timezone.utc)}
        logger.info(f"👥 Retail sentiment {symbol}: {result['score']:.1f} (contrarian: {result.get('contrarian_signal')})")
        return result

    # ── AGGREGATORE FINALE ────────────────────────────────────────────────────

    def get_aggregated_sentiment(self, symbol: str) -> Dict:
        """
        Score finale aggregato a 3 livelli con pesi professionali.
        
        Pesi:
        - Istituzionale: 50% — muove il mercato
        - Semi-pro: 30% — mercato informato
        - Retail: 20% — con logica contrarian
        """
        institutional = self.get_institutional_sentiment(symbol)
        semipro = self.get_semipro_sentiment(symbol)
        retail = self.get_retail_sentiment(symbol)

        inst_score = institutional.get("score", 0)
        semipro_score = semipro.get("score", 0)
        retail_score = retail.get("score", 0)

        # Se retail è contrarian, inverti il suo contributo
        retail_contribution = retail_score
        if retail.get("contrarian_signal"):
            retail_contribution = -retail_score * 1.2  # Amplifica il contrarian
            logger.info(f"⚠️ {symbol}: segnale contrarian retail rilevato — invertito")

        # Score aggregato pesato
        aggregated = (
            inst_score * 0.50 +
            semipro_score * 0.30 +
            retail_contribution * 0.20
        )
        aggregated = round(max(-100, min(100, aggregated)), 2)

        # Divergenza: se institutional e retail sono in forte disaccordo
        divergence = abs(inst_score - retail_score) > 50
        if divergence:
            logger.info(f"📊 {symbol}: DIVERGENZA institutional vs retail — inst:{inst_score:.0f} retail:{retail_score:.0f}")

        return {
            "symbol": symbol,
            "aggregated_score": aggregated,
            "institutional_score": inst_score,
            "semipro_score": semipro_score,
            "retail_score": retail_score,
            "institutional_bias": institutional.get("bias", "neutral"),
            "retail_contrarian": retail.get("contrarian_signal", False),
            "institutional_divergence": divergence,
            "top_institutions": institutional.get("top_institutions", []),
            "overall_bias": "bullish" if aggregated > 20 else "bearish" if aggregated < -20 else "neutral",
            "confidence": min(100, abs(aggregated) + (20 if not divergence else 0)),
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
        }

    def get_all_pairs_sentiment(self) -> Dict:
        """Sentiment aggregato per tutte le coppie"""
        results = {}
        for pair in FOREX_PAIRS:
            results[pair] = self.get_aggregated_sentiment(pair)
        return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    aggregator = MultiLevelSentimentAggregator()

    print("\n📊 Multi-Level Sentiment Analysis")
    print("=" * 50)

    for pair in ["EUR/USD", "GBP/USD", "USD/JPY"]:
        result = aggregator.get_aggregated_sentiment(pair)
        print(f"\n{pair}:")
        print(f"  Aggregato: {result['aggregated_score']:+.1f} ({result['overall_bias']})")
        print(f"  Institutional: {result['institutional_score']:+.1f} ({result['institutional_bias']})")
        print(f"  Semi-pro: {result['semipro_score']:+.1f}")
        print(f"  Retail: {result['retail_score']:+.1f} (contrarian: {result['retail_contrarian']})")
        if result['top_institutions']:
            print(f"  Top institutions: {', '.join(result['top_institutions'])}")
        if result['institutional_divergence']:
            print(f"  ⚠️ DIVERGENZA istituzionale vs retail!")
