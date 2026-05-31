import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import feedparser
import hashlib
from datetime import datetime, timezone
from typing import List, Dict, Optional
import logging

from core.data_collector.base_collector import BaseCollector, retry, rate_limit
from config.settings import FINNHUB_API_KEY, NEWS_API_KEY

logger = logging.getLogger(__name__)

# RSS feeds gratuiti — nessuna API key necessaria
RSS_FEEDS = {
    "reuters_markets": "https://feeds.reuters.com/reuters/businessNews",
    "dailyfx": "https://www.dailyfx.com/feeds/all",
    "forexlive": "https://www.forexlive.com/feed/news",
    "investing_forex": "https://www.investing.com/rss/news_285.rss",
    "marketwatch": "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines",
}

# Keywords per filtrare news rilevanti al forex
FOREX_KEYWORDS = [
    "EUR", "GBP", "USD", "JPY", "AUD", "CAD",
    "forex", "currency", "exchange rate", "central bank",
    "Fed", "ECB", "BOE", "BOJ", "RBA", "BOC",
    "interest rate", "inflation", "CPI", "NFP", "GDP",
    "monetary policy", "hawkish", "dovish",
    "dollar", "euro", "pound", "yen",
]


class NewsCollector(BaseCollector):

    def __init__(self):
        super().__init__("news")
        self.seen_urls = set()  # Evita duplicati nella stessa sessione

    def _is_forex_relevant(self, text: str) -> bool:
        """Verifica se la news è rilevante per il forex"""
        text_lower = text.lower()
        return any(kw.lower() in text_lower for kw in FOREX_KEYWORDS)

    def _parse_date(self, date_str) -> Optional[datetime]:
        """Converte vari formati di data in datetime UTC"""
        if not date_str:
            return datetime.utcnow()
        try:
            if hasattr(date_str, 'tm_year'):
                import calendar
                return datetime.utcfromtimestamp(calendar.timegm(date_str))
            return datetime.utcnow()
        except Exception:
            return datetime.utcnow()

    def _get_url_hash(self, url: str) -> str:
        return hashlib.md5(url.encode()).hexdigest()

    @retry(max_attempts=3, delay=1.0)
    def get_rss_news(self, max_per_feed: int = 20) -> List[Dict]:
        """Raccoglie news da tutti i feed RSS — completamente gratuito"""
        all_news = []

        for feed_name, feed_url in RSS_FEEDS.items():
            if not self.is_available():
                break
            try:
                feed = feedparser.parse(feed_url)
                count = 0
                for entry in feed.entries[:max_per_feed]:
                    title = getattr(entry, 'title', '')
                    summary = getattr(entry, 'summary', '')
                    url = getattr(entry, 'link', '')

                    if not title or not url:
                        continue
                    if url in self.seen_urls:
                        continue
                    if not self._is_forex_relevant(title + " " + summary):
                        continue

                    self.seen_urls.add(url)
                    all_news.append({
                        "source": feed_name,
                        "title": title,
                        "summary": summary[:500] if summary else "",
                        "url": url,
                        "published_at": self._parse_date(getattr(entry, 'published_parsed', None)),
                        "related_symbols": self._extract_symbols(title + " " + summary),
                    })
                    count += 1

                self.record_success()
                logger.info(f"✅ {feed_name}: {count} news rilevanti")

            except Exception as e:
                logger.error(f"❌ Errore RSS {feed_name}: {e}")
                self.record_failure()

        return all_news

    @retry(max_attempts=3, delay=2.0)
    @rate_limit(calls_per_minute=5)
    def get_finnhub_news(self, category: str = "forex") -> List[Dict]:
        """News da Finnhub — piano gratuito disponibile"""
        if not FINNHUB_API_KEY:
            logger.info("Finnhub non configurato, uso solo RSS")
            return []

        data = self.get(
            "https://finnhub.io/api/v1/news",
            params={"category": category, "token": FINNHUB_API_KEY}
        )

        if not data:
            return []

        news_list = []
        for item in data[:30]:
            url = item.get("url", "")
            if url in self.seen_urls:
                continue
            self.seen_urls.add(url)
            news_list.append({
                "source": "finnhub",
                "title": item.get("headline", ""),
                "summary": item.get("summary", "")[:500],
                "url": url,
                "published_at": datetime.utcfromtimestamp(item.get("datetime", 0)),
                "related_symbols": item.get("related", "").split(",") if item.get("related") else [],
                "finnhub_sentiment": item.get("sentiment", 0),
            })

        logger.info(f"✅ Finnhub: {len(news_list)} news")
        return news_list

    def _extract_symbols(self, text: str) -> List[str]:
        """Estrae le coppie forex menzionate nel testo"""
        from config.settings import FOREX_PAIRS
        found = []
        for pair in FOREX_PAIRS:
            base = pair.split("/")[0]
            quote = pair.split("/")[1]
            if base in text.upper() or quote in text.upper():
                found.append(pair)
        return found

    def collect_all(self) -> List[Dict]:
        """Raccoglie da tutte le fonti"""
        all_news = []
        all_news.extend(self.get_rss_news())
        all_news.extend(self.get_finnhub_news())

        # Ordina per data
        all_news.sort(key=lambda x: x.get("published_at", datetime.utcnow()), reverse=True)
        logger.info(f"📰 Totale news raccolte: {len(all_news)}")
        return all_news


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    collector = NewsCollector()
    news = collector.collect_all()
    print(f"\n✅ Raccolte {len(news)} news")
    for n in news[:3]:
        print(f"\n📰 {n['source']}: {n['title'][:80]}")
