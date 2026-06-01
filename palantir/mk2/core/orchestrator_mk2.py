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
from core.ai_analyzer.technical_analyzer_v2 import EnhancedTechnicalAnalyzer
from core.ai_analyzer.llm_analyzer import LLMAnalyzer
from core.ai_analyzer.intermarket_engine import IntermarketEngine
from core.ai_analyzer.order_flow import OrderFlowAnalyzer
from core.ai_analyzer.signal_generator_v2 import SignalGeneratorMK2
from core.risk_manager.risk_manager import RiskManager
from core.output.telegram_bot import TelegramBot
from database.models import SessionLocal, Signal as SignalModel
from database.init_db import init_database

logger = logging.getLogger(__name__)

signal_counter = 0


def run_mk2_cycle():
    """
    Ciclo di analisi MK2 — potenziato con intermarket e order flow.
    """
    global signal_counter
    logger.info(f"🚀 Ciclo MK2 — {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")

    # Inizializza tutti i moduli
    price_collector = PriceDataCollector()
    news_collector = NewsCollector()
    macro_collector = MacroDataCollector()
    sentiment_collector = SentimentCollector()
    cot_collector = COTCollector()
    tech_analyzer = EnhancedTechnicalAnalyzer()
    llm_analyzer = LLMAnalyzer()
    intermarket_engine = IntermarketEngine()
    order_flow_analyzer = OrderFlowAnalyzer()
    signal_gen = SignalGeneratorMK2()
    risk_manager = RiskManager()
    telegram = TelegramBot()
    db = SessionLocal()

    try:
        # ── Raccolta dati globali ────────────────────────────────────────
        news = news_collector.collect_all()
        macro_events = macro_collector.get_forex_factory_calendar()
        cot_data = cot_collector.fetch_cot_data()

        # ── Analisi intermarket (una volta per tutti) ────────────────────
        intermarket_results = intermarket_engine.get_all_pairs_scores(FOREX_PAIRS)
        regime = intermarket_results.get("regime", {})
        dxy = intermarket_results.get("dxy", {})

        logger.info(f"📊 Regime: {regime.get('regime', 'N/A')} | DXY: {dxy.get('bias', 'N/A')}")
        logger.info(f"📰 {len(news)} news | 📅 {len(macro_events)} eventi macro")

        # ── Analisi per coppia ───────────────────────────────────────────
        for symbol in FOREX_PAIRS:
            try:
                # Price data H1 e H4
                df_h1 = price_collector.get_ohlcv_yahoo(symbol, "1h", days=90)
                df_h4 = price_collector.get_ohlcv_yahoo(symbol, "4h", days=180)

                if df_h1 is None or len(df_h1) < 60:
                    logger.warning(f"Dati H1 insufficienti per {symbol}")
                    continue

                current_price = float(df_h1["close"].iloc[-1])

                # Analisi tecnica multi-timeframe
                if df_h4 is not None and len(df_h4) >= 60:
                    technical = tech_analyzer.get_multiframe_score(df_h1, df_h4)
                else:
                    technical = tech_analyzer.get_technical_score(df_h1)
                    technical["confirmed_by_h4"] = False

                technical["patterns"] = tech_analyzer.detect_patterns(df_h1)

                # Order flow
                order_flow = order_flow_analyzer.get_order_flow_score(df_h1)

                # Intermarket score per questa coppia
                pair_intermarket = intermarket_results.get("pairs", {}).get(symbol, {})

                # News per questa coppia
                pair_news = [n for n in news if symbol in n.get("related_symbols", [])]
                if not pair_news:
                    pair_news = news[:5]

                # Analisi LLM
                llm_result = llm_analyzer.analyze_news(pair_news, symbol)
                llm_score = llm_result.get("sentiment_score", 0)

                # Sentiment sociale
                social = sentiment_collector.get_combined_sentiment(symbol)
                social_score = social.get("combined_score", 0)

                # COT score
                cot_score = cot_collector.get_cot_score(symbol)

                # Sentiment combinato (LLM 60% + Social 30% + COT 10%)
                combined_sentiment = {
                    "combined_score": llm_score * 0.6 + social_score * 0.3 + cot_score * 0.1
                }

                # Log stato coppia
                logger.debug(
                    f"  {symbol}: tech={technical.get('score', 0):+.0f} "
                    f"intermarket={pair_intermarket.get('score', 0):+.0f} "
                    f"orderflow={order_flow.get('score', 0):+.0f} "
                    f"sentiment={combined_sentiment['combined_score']:+.0f}"
                )

                # Genera segnale MK2
                signal = signal_gen.check_all_strategies(
                    symbol=symbol,
                    technical=technical,
                    sentiment=combined_sentiment,
                    macro_events=macro_events,
                    cot_score=cot_score,
                    intermarket=pair_intermarket,
                    order_flow=order_flow,
                    current_price=current_price,
                )

                if not signal:
                    continue

                # Risk Manager
                approved = risk_manager.evaluate_signal(signal)

                if not approved:
                    db.add(SignalModel(
                        symbol=symbol,
                        direction=signal.get("direction"),
                        strategy_name=signal.get("strategy_name"),
                        entry_price=current_price,
                        raw_score=signal.get("raw_score"),
                        reasoning=signal.get("reasoning"),
                        status="rejected",
                    ))
                    db.commit()
                    continue

                # Segnale approvato — salva e invia
                signal_counter += 1
                db.add(SignalModel(
                    symbol=symbol,
                    direction=approved.get("direction"),
                    strategy_name=approved.get("strategy_name"),
                    entry_price=approved.get("entry_price"),
                    stop_loss=approved.get("stop_loss"),
                    take_profit_1=approved.get("take_profit_1"),
                    take_profit_2=approved.get("take_profit_2"),
                    position_size=approved.get("position_size"),
                    risk_amount=approved.get("risk_amount"),
                    risk_reward=approved.get("risk_reward"),
                    raw_score=approved.get("raw_score"),
                    reasoning=approved.get("reasoning"),
                    status="paper" if PAPER_TRADING else "sent",
                ))
                db.commit()

                telegram.send_signal(approved, signal_counter)
                logger.info(f"🚀 Segnale #{signal_counter:04d}: {symbol} {approved['direction']} via {approved.get('strategy_name')} (score: {approved.get('raw_score')})")

            except Exception as e:
                logger.error(f"Errore analisi {symbol}: {e}", exc_info=True)
                continue

    except Exception as e:
        logger.error(f"Errore ciclo MK2: {e}", exc_info=True)
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    logger.info("🚀 PALANTIR MK2 — Avvio test ciclo")
    logger.info(f"  Paper trading: {PAPER_TRADING}")
    logger.info(f"  Coppie: {', '.join(FOREX_PAIRS)}")
    logger.info(f"  Soglia segnale: 80/100")
    logger.info(f"  Filtri: EMA200 + ADX + Multi-timeframe + Intermarket + Order Flow")

    init_database()
    run_mk2_cycle()

    logger.info("✅ Ciclo MK2 completato")
