import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import logging
from datetime import datetime, timezone
from typing import Dict

from config.settings import FOREX_PAIRS, PAPER_TRADING
from core.data_collector.price_data import PriceDataCollector
from core.data_collector.news_collector import NewsCollector
from core.data_collector.macro_data import MacroDataCollector
from core.data_collector.sentiment_collector import SentimentCollector
from core.data_collector.cot_collector import COTCollector
from core.ai_analyzer.technical_analyzer import TechnicalAnalyzer
from core.ai_analyzer.llm_analyzer import LLMAnalyzer
from core.ai_analyzer.signal_generator import SignalGenerator
from core.ai_analyzer.composite_scorer import CompositeScorer
from core.risk_manager.risk_manager import RiskManager
from core.output.telegram_bot import TelegramBot
from database.models import SessionLocal, Signal as SignalModel
from database.init_db import init_database

logger = logging.getLogger(__name__)

signal_counter = 0


def run_analysis_cycle():
    """
    Un ciclo completo di analisi:
    1. Raccoglie dati
    2. Analizza ogni coppia
    3. Genera segnali
    4. Passa al Risk Manager
    5. Invia su Telegram
    """
    global signal_counter
    logger.info(f"🔄 Ciclo analisi — {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")

    # Inizializza moduli
    price_collector = PriceDataCollector()
    news_collector = NewsCollector()
    macro_collector = MacroDataCollector()
    sentiment_collector = SentimentCollector()
    cot_collector = COTCollector()
    tech_analyzer = TechnicalAnalyzer()
    llm_analyzer = LLMAnalyzer()
    signal_gen = SignalGenerator()
    risk_manager = RiskManager()
    telegram = TelegramBot()
    db = SessionLocal()

    try:
        # Raccoglie dati macro e news una volta per tutti
        news = news_collector.collect_all()
        macro_events = macro_collector.get_forex_factory_calendar()
        cot_data = cot_collector.fetch_cot_data()

        logger.info(f"📰 {len(news)} news | 📅 {len(macro_events)} eventi macro")

        # Analizza ogni coppia
        for symbol in FOREX_PAIRS:
            try:
                # 1. Price data
                df_h1 = price_collector.get_ohlcv_yahoo(symbol, "1h", days=60)
                if df_h1 is None or len(df_h1) < 50:
                    logger.warning(f"Dati insufficienti per {symbol}")
                    continue

                current_price = float(df_h1["close"].iloc[-1])

                # 2. Analisi tecnica
                technical = tech_analyzer.get_technical_score(df_h1)
                technical["patterns"] = tech_analyzer.detect_patterns(df_h1)

                # 3. News rilevanti per questa coppia
                pair_news = [n for n in news if symbol in n.get("related_symbols", [])]
                if not pair_news:
                    pair_news = news[:5]  # Usa le ultime 5 news generali

                # 4. Analisi LLM
                llm_result = llm_analyzer.analyze_news(pair_news, symbol)
                llm_score = llm_result.get("sentiment_score", 0)

                # 5. Sentiment social
                social = sentiment_collector.get_combined_sentiment(symbol)
                social_score = social.get("combined_score", 0)

                # 6. Sentiment combinato (LLM 60% + Social 40%)
                combined_sentiment = {
                    "combined_score": llm_score * 0.6 + social_score * 0.4
                }

                # 7. COT score
                cot_score = cot_collector.get_cot_score(symbol)

                # 8. Genera segnale
                signal = signal_gen.check_all_strategies(
                    symbol=symbol,
                    technical=technical,
                    sentiment=combined_sentiment,
                    macro_events=macro_events,
                    cot_score=cot_score,
                    current_price=current_price,
                )

                if not signal:
                    logger.debug(f"  {symbol}: nessun segnale")
                    continue

                # 9. Risk Manager
                approved_signal = risk_manager.evaluate_signal(signal)

                if not approved_signal:
                    # Salva segnale rifiutato nel DB
                    db_signal = SignalModel(
                        symbol=symbol,
                        direction=signal.get("direction"),
                        strategy_name=signal.get("strategy_name"),
                        entry_price=current_price,
                        raw_score=signal.get("raw_score"),
                        reasoning=signal.get("reasoning"),
                        status="rejected",
                    )
                    db.add(db_signal)
                    db.commit()
                    continue

                # 10. Salva e invia segnale approvato
                signal_counter += 1
                db_signal = SignalModel(
                    symbol=symbol,
                    direction=approved_signal.get("direction"),
                    strategy_name=approved_signal.get("strategy_name"),
                    entry_price=approved_signal.get("entry_price"),
                    stop_loss=approved_signal.get("stop_loss"),
                    take_profit_1=approved_signal.get("take_profit_1"),
                    take_profit_2=approved_signal.get("take_profit_2"),
                    position_size=approved_signal.get("position_size"),
                    risk_amount=approved_signal.get("risk_amount"),
                    risk_reward=approved_signal.get("risk_reward"),
                    raw_score=approved_signal.get("raw_score"),
                    reasoning=approved_signal.get("reasoning"),
                    status="sent" if not PAPER_TRADING else "paper",
                )
                db.add(db_signal)
                db.commit()

                # Invia su Telegram
                telegram.send_signal(approved_signal, signal_counter)
                logger.info(f"🚀 Segnale #{signal_counter:04d} inviato: {symbol} {approved_signal['direction']}")

            except Exception as e:
                logger.error(f"Errore analisi {symbol}: {e}")
                continue

    except Exception as e:
        logger.error(f"Errore ciclo analisi: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    logger.info("🚀 PALANTIR — Test ciclo analisi")
    logger.info(f"  Paper trading: {PAPER_TRADING}")
    logger.info(f"  Coppie: {', '.join(FOREX_PAIRS)}")

    init_database()
    run_analysis_cycle()

    logger.info("✅ Ciclo completato")
