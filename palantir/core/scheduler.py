import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import logging
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime

from core.data_collector.price_data import PriceDataCollector
from core.data_collector.news_collector import NewsCollector
from core.data_collector.macro_data import MacroDataCollector
from core.data_collector.sentiment_collector import SentimentCollector
from database.models import SessionLocal, Price, News, MacroEvent
from config.settings import (
    FOREX_PAIRS,
    PRICE_DATA_INTERVAL_MINUTES,
    NEWS_INTERVAL_MINUTES,
    MACRO_INTERVAL_MINUTES,
    PRIMARY_TIMEFRAME,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("scheduler")

# Inizializza collectors
price_collector = PriceDataCollector()
news_collector = NewsCollector()
macro_collector = MacroDataCollector()
sentiment_collector = SentimentCollector()


def job_collect_prices():
    """Task: raccoglie prezzi per tutte le coppie ogni 5 minuti"""
    logger.info("📈 Raccolta prezzi...")
    db = SessionLocal()
    try:
        for pair in FOREX_PAIRS:
            df = price_collector.get_ohlcv_yahoo(pair, "15min", days=2)
            if df is None or df.empty:
                continue
            # Salva ultima candela nel DB
            last = df.iloc[-1]
            price = Price(
                symbol=pair,
                timeframe="15min",
                timestamp=df.index[-1].to_pydatetime(),
                open=float(last["open"]),
                high=float(last["high"]),
                low=float(last["low"]),
                close=float(last["close"]),
                volume=float(last["volume"]) if last["volume"] else 0,
            )
            db.merge(price)
        db.commit()
        logger.info(f"✅ Prezzi aggiornati per {len(FOREX_PAIRS)} coppie")
    except Exception as e:
        logger.error(f"❌ Errore raccolta prezzi: {e}")
        db.rollback()
    finally:
        db.close()


def job_collect_news():
    """Task: raccoglie news ogni 10 minuti"""
    logger.info("📰 Raccolta news...")
    db = SessionLocal()
    try:
        news_list = news_collector.collect_all()
        new_count = 0
        for news_data in news_list:
            # Controlla se già esiste
            existing = db.query(News).filter(News.url == news_data["url"]).first()
            if existing:
                continue
            news = News(
                source=news_data["source"],
                title=news_data["title"],
                summary=news_data.get("summary", ""),
                url=news_data["url"],
                published_at=news_data.get("published_at"),
                related_symbols=news_data.get("related_symbols", []),
            )
            db.add(news)
            new_count += 1
        db.commit()
        logger.info(f"✅ {new_count} nuove news salvate")
    except Exception as e:
        logger.error(f"❌ Errore raccolta news: {e}")
        db.rollback()
    finally:
        db.close()


def job_collect_macro():
    """Task: raccoglie dati macro ogni ora"""
    logger.info("📊 Raccolta dati macro...")
    try:
        macro_data = macro_collector.get_all_macro_data()
        logger.info(f"✅ Macro: {len(macro_data)} serie aggiornate")
    except Exception as e:
        logger.error(f"❌ Errore raccolta macro: {e}")


def job_check_db_health():
    """Task: verifica salute del database ogni 30 minuti"""
    db = SessionLocal()
    try:
        price_count = db.query(Price).count()
        news_count = db.query(News).count()
        logger.info(f"💾 DB Health — Prezzi: {price_count}, News: {news_count}")
    finally:
        db.close()


def start_scheduler():
    """Avvia lo scheduler con tutti i job configurati"""
    scheduler = BlockingScheduler(timezone="UTC")

    # Prezzi ogni 5 minuti
    scheduler.add_job(
        job_collect_prices,
        trigger=IntervalTrigger(minutes=PRICE_DATA_INTERVAL_MINUTES),
        id="collect_prices",
        name="Raccolta Prezzi",
        max_instances=1,
        coalesce=True,
    )

    # News ogni 10 minuti
    scheduler.add_job(
        job_collect_news,
        trigger=IntervalTrigger(minutes=NEWS_INTERVAL_MINUTES),
        id="collect_news",
        name="Raccolta News",
        max_instances=1,
        coalesce=True,
    )

    # Macro ogni ora
    scheduler.add_job(
        job_collect_macro,
        trigger=IntervalTrigger(minutes=MACRO_INTERVAL_MINUTES),
        id="collect_macro",
        name="Raccolta Macro",
        max_instances=1,
        coalesce=True,
    )

    # Health check ogni 30 minuti
    scheduler.add_job(
        job_check_db_health,
        trigger=IntervalTrigger(minutes=30),
        id="db_health",
        name="DB Health Check",
    )

    logger.info("🚀 Scheduler avviato!")
    logger.info(f"  📈 Prezzi: ogni {PRICE_DATA_INTERVAL_MINUTES} min")
    logger.info(f"  📰 News: ogni {NEWS_INTERVAL_MINUTES} min")
    logger.info(f"  📊 Macro: ogni {MACRO_INTERVAL_MINUTES} min")

    # Esegui subito al primo avvio
    job_collect_prices()
    job_collect_news()
    job_collect_macro()

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("⛔ Scheduler fermato")


if __name__ == "__main__":
    start_scheduler()
