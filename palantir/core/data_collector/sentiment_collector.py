import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import praw
import requests
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import logging
import re

from core.data_collector.base_collector import BaseCollector, retry, rate_limit
from config.settings import REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT, FOREX_PAIRS

logger = logging.getLogger(__name__)

SUBREDDITS = ["Forex", "investing", "economics", "wallstreetbets"]

# Parole bullish e bearish per sentiment base
BULLISH_WORDS = ["bullish", "long", "buy", "breakout", "upside", "rally", "surge", "strong", "rise", "moon"]
BEARISH_WORDS = ["bearish", "short", "sell", "breakdown", "downside", "crash", "dump", "weak", "fall", "drop"]


def basic_sentiment(text: str) -> float:
    """Sentiment base senza AI — conta parole bullish/bearish. Ritorna -100 a +100"""
    text_lower = text.lower()
    bullish_count = sum(1 for w in BULLISH_WORDS if w in text_lower)
    bearish_count = sum(1 for w in BEARISH_WORDS if w in text_lower)
    total = bullish_count + bearish_count
    if total == 0:
        return 0.0
    return ((bullish_count - bearish_count) / total) * 100


class SentimentCollector(BaseCollector):

    def __init__(self):
        super().__init__("sentiment")
        self.reddit = self._init_reddit()

    def _init_reddit(self):
        """Inizializza Reddit API — gratuito"""
        if not REDDIT_CLIENT_ID or not REDDIT_CLIENT_SECRET:
            logger.warning("Reddit API non configurata — sentiment Reddit disabilitato")
            return None
        try:
            reddit = praw.Reddit(
                client_id=REDDIT_CLIENT_ID,
                client_secret=REDDIT_CLIENT_SECRET,
                user_agent=REDDIT_USER_AGENT,
            )
            logger.info("✅ Reddit API connessa")
            return reddit
        except Exception as e:
            logger.error(f"Errore Reddit init: {e}")
            return None

    @retry(max_attempts=2, delay=2.0)
    @rate_limit(calls_per_minute=10)
    def get_reddit_sentiment(self, pair: str, limit: int = 25) -> Dict:
        """Raccoglie post Reddit rilevanti per una coppia forex"""
        if not self.reddit:
            return {"symbol": pair, "score": 0, "post_count": 0, "source": "reddit"}

        base, quote = pair.split("/")
        keywords = [pair, base, quote, pair.replace("/", "")]
        all_posts = []

        for subreddit_name in SUBREDDITS[:2]:  # Limita per non esaurire quota
            try:
                subreddit = self.reddit.subreddit(subreddit_name)
                for keyword in keywords[:2]:
                    for post in subreddit.search(keyword, limit=limit // 4, time_filter="day"):
                        all_posts.append({
                            "title": post.title,
                            "text": post.selftext[:200],
                            "score": post.score,
                            "upvote_ratio": post.upvote_ratio,
                        })
            except Exception as e:
                logger.error(f"Errore Reddit {subreddit_name}: {e}")

        if not all_posts:
            return {"symbol": pair, "score": 0, "post_count": 0, "source": "reddit"}

        # Calcola sentiment pesato per upvote
        total_weight = 0
        weighted_sentiment = 0
        for post in all_posts:
            text = post["title"] + " " + post["text"]
            sentiment = basic_sentiment(text)
            weight = max(1, post["score"]) * post["upvote_ratio"]
            weighted_sentiment += sentiment * weight
            total_weight += weight

        final_score = weighted_sentiment / total_weight if total_weight > 0 else 0

        return {
            "symbol": pair,
            "score": round(final_score, 2),
            "post_count": len(all_posts),
            "source": "reddit",
            "collected_at": datetime.utcnow().isoformat(),
        }

    @retry(max_attempts=3, delay=1.0)
    @rate_limit(calls_per_minute=15)
    def get_stocktwits_sentiment(self, pair: str) -> Dict:
        """Raccoglie sentiment da StockTwits — gratuito fino a 400 req/ora"""
        # Converti formato: EUR/USD → EURUSD
        symbol = pair.replace("/", "")

        try:
            url = f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
            response = requests.get(url, timeout=10)

            if response.status_code == 404:
                logger.info(f"StockTwits: simbolo {symbol} non trovato")
                return {"symbol": pair, "score": 0, "message_count": 0, "source": "stocktwits"}

            response.raise_for_status()
            data = response.json()
            messages = data.get("messages", [])

            if not messages:
                return {"symbol": pair, "score": 0, "message_count": 0, "source": "stocktwits"}

            # StockTwits ha sentiment label nativo: "Bullish" o "Bearish"
            bullish = sum(1 for m in messages if m.get("entities", {}).get("sentiment", {}).get("basic") == "Bullish")
            bearish = sum(1 for m in messages if m.get("entities", {}).get("sentiment", {}).get("basic") == "Bearish")
            total = bullish + bearish

            if total > 0:
                score = ((bullish - bearish) / total) * 100
            else:
                # Fallback: analisi testo
                texts = " ".join([m.get("body", "") for m in messages[:20]])
                score = basic_sentiment(texts)

            self.record_success()
            return {
                "symbol": pair,
                "score": round(score, 2),
                "bullish_count": bullish,
                "bearish_count": bearish,
                "message_count": len(messages),
                "source": "stocktwits",
                "collected_at": datetime.utcnow().isoformat(),
            }

        except Exception as e:
            logger.error(f"Errore StockTwits {pair}: {e}")
            self.record_failure()
            return {"symbol": pair, "score": 0, "message_count": 0, "source": "stocktwits"}

    def get_combined_sentiment(self, pair: str) -> Dict:
        """Combina Reddit + StockTwits in un unico score"""
        reddit = self.get_reddit_sentiment(pair)
        stocktwits = self.get_stocktwits_sentiment(pair)

        # Media pesata: StockTwits più affidabile per forex
        reddit_weight = 0.3
        stocktwits_weight = 0.7
        combined = (reddit["score"] * reddit_weight + stocktwits["score"] * stocktwits_weight)

        return {
            "symbol": pair,
            "combined_score": round(combined, 2),
            "reddit_score": reddit["score"],
            "stocktwits_score": stocktwits["score"],
            "reddit_posts": reddit.get("post_count", 0),
            "stocktwits_messages": stocktwits.get("message_count", 0),
            "collected_at": datetime.utcnow().isoformat(),
        }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    collector = SentimentCollector()

    print("\n📊 Test sentiment EUR/USD:")
    result = collector.get_combined_sentiment("EUR/USD")
    print(f"  Score combinato: {result['combined_score']}")
    print(f"  Reddit: {result['reddit_score']} ({result['reddit_posts']} post)")
    print(f"  StockTwits: {result['stocktwits_score']} ({result['stocktwits_messages']} messaggi)")
