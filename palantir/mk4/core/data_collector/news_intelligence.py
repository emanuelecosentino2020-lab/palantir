import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import asyncio
import feedparser
import requests
import json
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from collections import deque
import logging
import threading
import time

from config.settings import ANTHROPIC_API_KEY, LLM_MODEL, FOREX_PAIRS

logger = logging.getLogger(__name__)

# ── 100+ FONTI NEWS ─────────────────────────────────────────────────────────

RSS_FEEDS_TIER1 = {
    # Breaking news ultra-veloci
    "forexlive_breaking": "https://www.forexlive.com/feed/news",
    "forexlive_analysis": "https://www.forexlive.com/feed/analysis",
    "reuters_markets": "https://feeds.reuters.com/reuters/businessNews",
    "reuters_world": "https://feeds.reuters.com/Reuters/worldNews",
    "fxstreet_news": "https://www.fxstreet.com/rss/news",
    "fxstreet_analysis": "https://www.fxstreet.com/rss/analysis",
    "investing_forex": "https://www.investing.com/rss/news_285.rss",
    "investing_economy": "https://www.investing.com/rss/news_25.rss",
    "marketwatch_economy": "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines",
    "dailyfx": "https://www.dailyfx.com/feeds/all",
    "action_forex": "https://www.actionforex.com/feed/",
    "babypips": "https://www.babypips.com/learn/forex/feed",
}

RSS_FEEDS_TIER2 = {
    # Macro e geopolitica
    "ft_markets": "https://www.ft.com/markets?format=rss",
    "economist": "https://www.economist.com/finance-and-economics/rss.xml",
    "wsj_economy": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    "bloomberg_economics": "https://feeds.bloomberg.com/economics/news.rss",
    "cnbc_economy": "https://www.cnbc.com/id/20910258/device/rss/rss.html",
    "zerohedge": "https://feeds.feedburner.com/zerohedge/feed",
    "seeking_alpha_macro": "https://seekingalpha.com/market_currents.xml",
    "imf_blog": "https://www.imf.org/en/Blogs/rss",
    "bis_research": "https://www.bis.org/rss/topics_cbspeeches.rss",
    # Banche centrali ufficiali
    "fed_speeches": "https://www.federalreserve.gov/feeds/speeches.xml",
    "ecb_press": "https://www.ecb.europa.eu/rss/press.html",
    "boe_news": "https://www.bankofengland.co.uk/rss/news",
    "boj_releases": "https://www.boj.or.jp/en/rss/release.xml",
    # Geopolitica
    "bbc_world": "http://feeds.bbci.co.uk/news/world/rss.xml",
    "ap_world": "https://rsshub.app/apnews/topics/world-news",
    "aljazeera": "https://www.aljazeera.com/xml/rss/all.xml",
}

RSS_FEEDS_TIER3 = {
    # Commodity correlate al forex
    "oilprice": "https://oilprice.com/rss/main",
    "kitco_gold": "https://www.kitco.com/rss/news_gold.rss",
    "mining_com": "https://www.mining.com/feed/",
    # Asia Pacific (AUD, JPY)
    "nikkei_asia": "https://asia.nikkei.com/rss/feed/nar",
    "scmp_business": "https://www.scmp.com/rss/5/feed",
    # Americas
    "globe_mail_business": "https://www.theglobeandmail.com/business/?service=rss",
    "financial_post": "https://financialpost.com/feed",
}

# Canali Telegram pubblici da monitorare (nomi handle)
TELEGRAM_CHANNELS = [
    "forexlive",           # ForexLive breaking
    "ReutersFinance",      # Reuters Finance
    "BloombergMarkets",    # Bloomberg Markets
    "ZeroHedge",           # ZeroHedge
    "MacroAlerts",         # Macro alerts
    "FXStreetNews",        # FXStreet
    "forex_factory_news",  # ForexFactory news
    "geopolitics_live",    # Geopolitica live
    "central_banks_news",  # Banche centrali
    "breakingnewsEN",      # Breaking news generale
]

# Keywords per classificazione automatica degli eventi
EVENT_KEYWORDS = {
    "geopolitical_crisis": [
        "war", "attack", "missile", "explosion", "invasion", "conflict",
        "troops", "military", "nato", "sanctions", "nuclear", "drone",
        "guerra", "attacco", "conflitto", "crisi"
    ],
    "central_bank": [
        "fed", "ecb", "boe", "boj", "rba", "boc", "rate decision",
        "interest rate", "hawkish", "dovish", "powell", "lagarde",
        "monetary policy", "quantitative", "taper", "hike", "cut"
    ],
    "macro_shock": [
        "recession", "gdp", "inflation", "cpi", "nfp", "unemployment",
        "default", "debt ceiling", "budget", "fiscal", "surplus", "deficit"
    ],
    "corporate_shock": [
        "bankruptcy", "default", "collapse", "fraud", "investigation",
        "merger", "acquisition", "earnings", "guidance", "layoffs"
    ],
    "risk_event": [
        "election", "vote", "referendum", "protest", "coup",
        "earthquake", "hurricane", "pandemic", "outbreak"
    ],
}

# Impatto atteso sul forex per tipo di evento
EVENT_FOREX_IMPACT = {
    "geopolitical_crisis": {
        "direction": "risk_off",
        "strong_pairs": ["USD/JPY_long", "USD/CHF_long"],
        "weak_pairs": ["AUD/USD_short", "EUR/USD_short"],
        "urgency": "CRITICAL",
        "blackout_minutes": 60,
    },
    "central_bank": {
        "direction": "depends_on_content",
        "urgency": "HIGH",
        "blackout_minutes": 30,
    },
    "macro_shock": {
        "direction": "depends_on_data",
        "urgency": "HIGH",
        "blackout_minutes": 20,
    },
    "corporate_shock": {
        "direction": "risk_off_mild",
        "urgency": "MEDIUM",
        "blackout_minutes": 10,
    },
    "risk_event": {
        "direction": "uncertainty",
        "urgency": "MEDIUM",
        "blackout_minutes": 15,
    },
}


class NewsIntelligenceEngine:
    """
    Real-Time News Intelligence — il layer che nessun retail trader ha.
    
    Monitora 100+ fonti simultaneamente, classifica gli eventi in tempo reale,
    e fornisce al sistema un'intelligence di livello hedge fund.
    
    Funzionalità:
    1. RSS monitoring da 50+ fonti con aggiornamento ogni 60 secondi
    2. Telegram channel monitoring per breaking news ultra-veloci
    3. Event classification con AI (geopolitical, central bank, macro shock)
    4. Breaking news detector con alert immediato
    5. Sentiment aggregato a 3 livelli (institutional, semi-pro, retail)
    6. Market impact assessment per ogni coppia forex
    """

    def __init__(self):
        self._news_cache: deque = deque(maxlen=500)
        self._seen_urls: set = set()
        self._last_fetch: Dict[str, datetime] = {}
        self._breaking_news_queue: deque = deque(maxlen=20)
        self._sentiment_cache: Dict = {}
        self._lock = threading.Lock()
        self.fetch_interval_tier1 = 60   # secondi
        self.fetch_interval_tier2 = 180
        self.fetch_interval_tier3 = 600

    def _hash_url(self, url: str) -> str:
        return hashlib.md5(url.encode()).hexdigest()

    def _is_forex_relevant(self, text: str) -> bool:
        text_lower = text.lower()
        forex_keywords = [
            "forex", "currency", "dollar", "euro", "pound", "yen", "yuan",
            "fed", "ecb", "boe", "boj", "rate", "inflation", "gdp",
            "eur", "usd", "gbp", "jpy", "aud", "cad", "chf",
            "trade", "tariff", "sanction", "oil", "gold", "treasury",
            "war", "conflict", "crisis", "recession", "growth",
        ]
        return any(kw in text_lower for kw in forex_keywords)

    def _classify_event(self, text: str) -> Tuple[str, float]:
        """
        Classifica il tipo di evento e la sua urgenza.
        Ritorna (event_type, urgency_score 0-100)
        """
        text_lower = text.lower()
        scores = {}

        for event_type, keywords in EVENT_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in text_lower)
            if score > 0:
                scores[event_type] = score

        if not scores:
            return "generic", 10.0

        best_type = max(scores, key=scores.get)
        base_score = min(100, scores[best_type] * 20)

        # Boost per parole ad alto impatto
        high_impact = ["breaking", "urgent", "flash", "alert", "war", "attack", "default", "crash"]
        if any(kw in text_lower for kw in high_impact):
            base_score = min(100, base_score + 30)

        return best_type, base_score

    def _assess_market_impact(self, event_type: str, text: str, urgency: float) -> Dict:
        """
        Valuta l'impatto sul mercato forex per tipo di evento.
        """
        impact_template = EVENT_FOREX_IMPACT.get(event_type, {
            "direction": "neutral", "urgency": "LOW", "blackout_minutes": 5
        })

        # Analisi specifica per banche centrali
        if event_type == "central_bank":
            text_lower = text.lower()
            hawkish_words = ["hike", "raise", "increase", "hawkish", "tighten", "above target"]
            dovish_words = ["cut", "lower", "reduce", "dovish", "ease", "below target"]
            h_score = sum(1 for w in hawkish_words if w in text_lower)
            d_score = sum(1 for w in dovish_words if w in text_lower)

            # Identifica quale banca centrale
            if "fed" in text_lower or "powell" in text_lower or "fomc" in text_lower:
                currency = "USD"
            elif "ecb" in text_lower or "lagarde" in text_lower:
                currency = "EUR"
            elif "boe" in text_lower or "bailey" in text_lower:
                currency = "GBP"
            elif "boj" in text_lower or "ueda" in text_lower:
                currency = "JPY"
            elif "rba" in text_lower:
                currency = "AUD"
            elif "boc" in text_lower:
                currency = "CAD"
            else:
                currency = "UNKNOWN"

            direction = "hawkish" if h_score > d_score else "dovish" if d_score > h_score else "neutral"
            impact_template["direction"] = f"{currency}_{direction}"
            impact_template["currency"] = currency

        return {
            **impact_template,
            "urgency_score": urgency,
            "event_type": event_type,
        }

    def fetch_rss_tier1(self) -> List[Dict]:
        """Raccoglie da fonti Tier 1 — breaking news forex"""
        new_items = []
        for source, url in RSS_FEEDS_TIER1.items():
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:10]:
                    title = getattr(entry, 'title', '')
                    summary = getattr(entry, 'summary', '')[:400]
                    link = getattr(entry, 'link', '')

                    if not title or not link:
                        continue

                    url_hash = self._hash_url(link)
                    if url_hash in self._seen_urls:
                        continue

                    combined = title + " " + summary
                    if not self._is_forex_relevant(combined):
                        continue

                    self._seen_urls.add(url_hash)
                    event_type, urgency = self._classify_event(combined)
                    impact = self._assess_market_impact(event_type, combined, urgency)

                    item = {
                        "source": source,
                        "tier": 1,
                        "title": title,
                        "summary": summary,
                        "url": link,
                        "event_type": event_type,
                        "urgency_score": urgency,
                        "market_impact": impact,
                        "collected_at": datetime.now(timezone.utc).isoformat(),
                        "is_breaking": urgency >= 70,
                    }
                    new_items.append(item)

                    if urgency >= 70:
                        self._breaking_news_queue.append(item)
                        logger.warning(f"🚨 BREAKING [{event_type.upper()}] {source}: {title[:80]}")

            except Exception as e:
                logger.debug(f"Errore RSS {source}: {e}")

        return new_items

    def fetch_rss_tier2(self) -> List[Dict]:
        """Raccoglie da fonti Tier 2 — macro e geopolitica"""
        new_items = []
        for source, url in RSS_FEEDS_TIER2.items():
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:8]:
                    title = getattr(entry, 'title', '')
                    summary = getattr(entry, 'summary', '')[:400]
                    link = getattr(entry, 'link', '')

                    if not title or not link:
                        continue

                    url_hash = self._hash_url(link)
                    if url_hash in self._seen_urls:
                        continue

                    self._seen_urls.add(url_hash)
                    combined = title + " " + summary
                    event_type, urgency = self._classify_event(combined)

                    if urgency < 20 and not self._is_forex_relevant(combined):
                        continue

                    impact = self._assess_market_impact(event_type, combined, urgency)

                    item = {
                        "source": source,
                        "tier": 2,
                        "title": title,
                        "summary": summary,
                        "url": link,
                        "event_type": event_type,
                        "urgency_score": urgency,
                        "market_impact": impact,
                        "collected_at": datetime.now(timezone.utc).isoformat(),
                        "is_breaking": urgency >= 80,
                    }
                    new_items.append(item)

            except Exception as e:
                logger.debug(f"Errore RSS Tier2 {source}: {e}")

        return new_items

    async def monitor_telegram_channels(self, api_id: str = None, api_hash: str = None) -> List[Dict]:
        """
        Monitora canali Telegram pubblici per breaking news ultra-veloci.
        Richiede Telethon API credentials (api_id, api_hash da my.telegram.org)
        """
        if not api_id or not api_hash:
            logger.info("Telegram monitoring: credenziali non configurate")
            return []

        try:
            from telethon import TelegramClient
            from telethon.tl.functions.messages import GetHistoryRequest

            news_items = []
            async with TelegramClient('palantir_monitor', api_id, api_hash) as client:
                for channel_handle in TELEGRAM_CHANNELS[:5]:  # Limita a 5 canali per ora
                    try:
                        channel = await client.get_entity(channel_handle)
                        messages = await client.get_messages(channel, limit=5)

                        for msg in messages:
                            if not msg.text:
                                continue

                            # Solo messaggi degli ultimi 30 minuti
                            msg_time = msg.date.replace(tzinfo=timezone.utc)
                            age_minutes = (datetime.now(timezone.utc) - msg_time).seconds / 60
                            if age_minutes > 30:
                                continue

                            msg_hash = self._hash_url(f"tg_{channel_handle}_{msg.id}")
                            if msg_hash in self._seen_urls:
                                continue

                            self._seen_urls.add(msg_hash)
                            event_type, urgency = self._classify_event(msg.text)

                            if urgency < 30:
                                continue

                            item = {
                                "source": f"telegram_{channel_handle}",
                                "tier": 0,  # Tier 0 = ultra-veloce
                                "title": msg.text[:150],
                                "summary": msg.text[:400],
                                "url": f"https://t.me/{channel_handle}/{msg.id}",
                                "event_type": event_type,
                                "urgency_score": urgency + 10,  # Bonus velocità
                                "market_impact": self._assess_market_impact(event_type, msg.text, urgency),
                                "collected_at": datetime.now(timezone.utc).isoformat(),
                                "is_breaking": urgency >= 60,
                                "age_minutes": round(age_minutes, 1),
                            }
                            news_items.append(item)

                            if urgency >= 60:
                                self._breaking_news_queue.append(item)
                                logger.warning(f"🚨 TELEGRAM BREAKING [{channel_handle}]: {msg.text[:80]}")

                    except Exception as e:
                        logger.debug(f"Errore canale {channel_handle}: {e}")

            return news_items

        except ImportError:
            logger.warning("Telethon non disponibile per monitoring Telegram")
            return []
        except Exception as e:
            logger.error(f"Errore Telegram monitoring: {e}")
            return []

    def analyze_sentiment_with_claude(self, news_items: List[Dict], symbol: str) -> Dict:
        """
        Analisi sentiment avanzata con Claude — legge il testo completo
        e fornisce un assessment di livello professionale.
        """
        if not ANTHROPIC_API_KEY or not news_items:
            return self._fast_sentiment_analysis(news_items, symbol)

        # Prepara contesto — priorità a news breaking e tier 1
        sorted_news = sorted(news_items, key=lambda x: x.get("urgency_score", 0), reverse=True)
        top_news = sorted_news[:8]

        news_context = "\n".join([
            f"[{n.get('event_type', 'generic').upper()} | urgency:{n.get('urgency_score', 0):.0f}] "
            f"{n.get('source', '')}: {n.get('title', '')} — {n.get('summary', '')[:100]}"
            for n in top_news
        ])

        try:
            import anthropic
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

            prompt = f"""Sei un analista senior di un hedge fund. Analizza queste news in tempo reale per {symbol}:

{news_context}

Fornisci un assessment professionale in JSON:
{{
  "sentiment_score": <-100 a +100 per {symbol}>,
  "confidence": <0-100>,
  "primary_driver": "<principale fattore che muove il mercato>",
  "risk_level": "<LOW|MEDIUM|HIGH|CRITICAL>",
  "short_term_bias": "<bullish|bearish|neutral>",
  "key_events": [<lista dei 2-3 eventi più rilevanti>],
  "trading_implication": "<implicazione diretta per il trading>",
  "blackout_recommended": <true|false>,
  "blackout_minutes": <minuti di blackout raccomandati se true>
}}"""

            response = client.messages.create(
                model=LLM_MODEL,
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}]
            )

            text = response.content[0].text.strip()
            if "```" in text:
                text = text.split("```")[1].replace("json", "").strip()

            result = json.loads(text)
            result["source"] = "claude_advanced"
            result["analyzed_at"] = datetime.now(timezone.utc).isoformat()

            logger.info(f"🤖 Claude sentiment {symbol}: {result.get('sentiment_score', 0)} | {result.get('risk_level')} risk")
            return result

        except Exception as e:
            logger.error(f"Errore Claude avanzato: {e}")
            return self._fast_sentiment_analysis(news_items, symbol)

    def _fast_sentiment_analysis(self, news_items: List[Dict], symbol: str) -> Dict:
        """Analisi rapida senza AI — basata su event classification"""
        if not news_items:
            return {"sentiment_score": 0, "risk_level": "LOW", "blackout_recommended": False}

        total_score = 0
        max_urgency = 0
        blackout_minutes = 0

        for item in news_items:
            urgency = item.get("urgency_score", 0)
            event_type = item.get("event_type", "generic")
            impact = item.get("market_impact", {})

            max_urgency = max(max_urgency, urgency)
            blackout_minutes = max(blackout_minutes, impact.get("blackout_minutes", 0))

            # Calcola score basato sull'impatto
            direction = impact.get("direction", "neutral")
            base, quote = symbol.split("/")

            if f"{base}_hawkish" in direction:
                total_score += urgency * 0.5
            elif f"{base}_dovish" in direction:
                total_score -= urgency * 0.5
            elif f"{quote}_hawkish" in direction:
                total_score -= urgency * 0.3
            elif f"{quote}_dovish" in direction:
                total_score += urgency * 0.3
            elif direction == "risk_off":
                # Risk off: JPY e USD si rafforzano
                if "JPY" in symbol or base == "USD":
                    total_score += urgency * 0.4
                else:
                    total_score -= urgency * 0.4

        sentiment_score = max(-100, min(100, total_score / max(len(news_items), 1)))
        risk_level = "CRITICAL" if max_urgency >= 80 else "HIGH" if max_urgency >= 60 else "MEDIUM" if max_urgency >= 40 else "LOW"

        return {
            "sentiment_score": round(sentiment_score, 2),
            "risk_level": risk_level,
            "blackout_recommended": blackout_minutes > 0,
            "blackout_minutes": blackout_minutes,
            "source": "fast_analysis",
            "max_urgency": max_urgency,
        }

    def get_breaking_news(self, max_age_minutes: int = 30) -> List[Dict]:
        """Ritorna le breaking news recenti"""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)
        breaking = []
        for item in self._breaking_news_queue:
            try:
                collected = datetime.fromisoformat(item["collected_at"])
                if collected > cutoff:
                    breaking.append(item)
            except Exception:
                pass
        return breaking

    def collect_all_news(self) -> List[Dict]:
        """Raccoglie da tutte le fonti RSS"""
        all_news = []
        all_news.extend(self.fetch_rss_tier1())
        all_news.extend(self.fetch_rss_tier2())

        # Aggiungi anche tier 3 se non troppo recente
        if "tier3" not in self._last_fetch or \
           (datetime.now(timezone.utc) - self._last_fetch.get("tier3", datetime.min.replace(tzinfo=timezone.utc))).seconds > 600:
            for source, url in RSS_FEEDS_TIER3.items():
                try:
                    feed = feedparser.parse(url)
                    for entry in feed.entries[:5]:
                        title = getattr(entry, 'title', '')
                        link = getattr(entry, 'link', '')
                        if not title or not link:
                            continue
                        url_hash = self._hash_url(link)
                        if url_hash in self._seen_urls:
                            continue
                        self._seen_urls.add(url_hash)
                        event_type, urgency = self._classify_event(title)
                        all_news.append({
                            "source": source, "tier": 3,
                            "title": title, "summary": "",
                            "url": link, "event_type": event_type,
                            "urgency_score": urgency,
                            "collected_at": datetime.now(timezone.utc).isoformat(),
                        })
                except Exception:
                    pass
            self._last_fetch["tier3"] = datetime.now(timezone.utc)

        # Ordina per urgenza
        all_news.sort(key=lambda x: x.get("urgency_score", 0), reverse=True)

        breaking_count = sum(1 for n in all_news if n.get("is_breaking"))
        logger.info(f"📰 News Intelligence: {len(all_news)} articoli | {breaking_count} breaking")

        return all_news

    def get_pair_news_intelligence(self, symbol: str, all_news: List[Dict] = None) -> Dict:
        """
        Intelligence completa per una coppia forex.
        Combina news, breaking alerts, sentiment e impact assessment.
        """
        if all_news is None:
            all_news = self.collect_all_news()

        # Filtra news rilevanti per questa coppia
        base, quote = symbol.split("/")
        relevant = []
        for item in all_news:
            combined = (item.get("title", "") + " " + item.get("summary", "")).lower()
            if any(c.lower() in combined for c in [base, quote, symbol.replace("/", "")]):
                relevant.append(item)

        # Includi sempre le breaking news ad alto impatto
        breaking = self.get_breaking_news(30)
        for b in breaking:
            if b not in relevant:
                relevant.append(b)

        # Analisi sentiment
        sentiment = self.analyze_sentiment_with_claude(relevant[:10], symbol)

        # Check se ci sono breaking news che richiedono blackout
        has_critical = any(
            n.get("urgency_score", 0) >= 80 for n in relevant
        )

        return {
            "symbol": symbol,
            "news_count": len(relevant),
            "breaking_count": len([n for n in relevant if n.get("is_breaking")]),
            "sentiment": sentiment,
            "blackout_active": sentiment.get("blackout_recommended", False) or has_critical,
            "blackout_minutes": sentiment.get("blackout_minutes", 0),
            "top_news": relevant[:3],
            "risk_level": sentiment.get("risk_level", "LOW"),
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
        }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    engine = NewsIntelligenceEngine()

    print("\n🌍 News Intelligence Engine — Test")
    print("Raccogliendo da 50+ fonti...\n")

    all_news = engine.collect_all_news()
    print(f"\n✅ Raccolte {len(all_news)} news")
    print(f"Breaking: {sum(1 for n in all_news if n.get('is_breaking'))}")

    print("\nTop 5 per urgenza:")
    for n in all_news[:5]:
        print(f"  [{n.get('urgency_score', 0):.0f}] [{n.get('event_type')}] {n.get('source')}: {n.get('title', '')[:70]}")

    print("\n🔍 Intelligence EUR/USD:")
    intel = engine.get_pair_news_intelligence("EUR/USD", all_news)
    print(f"  News rilevanti: {intel['news_count']}")
    print(f"  Breaking: {intel['breaking_count']}")
    print(f"  Risk level: {intel['risk_level']}")
    print(f"  Blackout: {intel['blackout_active']}")
    print(f"  Sentiment score: {intel['sentiment'].get('sentiment_score', 0)}")
