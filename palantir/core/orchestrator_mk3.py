import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import logging
from datetime import datetime, timezone
from typing import Dict

from config.settings import FOREX_PAIRS, PAPER_TRADING, ACCOUNT_BALANCE
from core.data_collector.price_data import PriceDataCollector
from core.data_collector.news_collector import NewsCollector
from core.data_collector.macro_data import MacroDataCollector
from core.data_collector.sentiment_collector import SentimentCollector
from core.data_collector.cot_collector import COTCollector
from core.ai_analyzer.technical_analyzer_v2 import EnhancedTechnicalAnalyzer
from core.ai_analyzer.llm_analyzer import LLMAnalyzer
from core.ai_analyzer.intermarket_engine import IntermarketEngine
from core.ai_analyzer.market_structure import MarketStructureAnalyzer
from core.ai_analyzer.session_filter import SessionFilter
from core.ai_analyzer.institutional_sentiment import InstitutionalSentimentAnalyzer
from core.ai_analyzer.signal_generator_v2 import SignalGeneratorMK2
from core.risk_manager.risk_manager import RiskManager
from core.output.telegram_bot_v2 import TelegramBotV2
from database.models import SessionLocal, Signal as SignalModel
from database.init_db import init_database

logger = logging.getLogger(__name__)

signal_counter = 0


def run_mk3_cycle():
    """
    Ciclo completo MK3 — il sistema Palantir definitivo.
    
    Layer attivi:
    1. Session Filter — solo nelle sessioni giuste
    2. Market Structure — solo nella direzione del trend
    3. Technical (EMA200 + ADX + Multi-TF) — conferma tecnica
    4. Intermarket (DXY + VIX + correlazioni) — contesto macro
    5. Institutional Sentiment (Claude + ESI) — intelligence istituzionale  
    6. Signal Generator (5 strategie, soglia 80) — generazione segnali
    7. Risk Manager (prop firm compliant) — approvazione finale
    8. Telegram V2 (reasoning completo + alert) — distribuzione
    """
    global signal_counter
    now = datetime.now(timezone.utc)
    logger.info(f"🚀 Ciclo MK3 — {now.strftime('%H:%M:%S UTC')}")

    # Inizializza moduli
    price_collector = PriceDataCollector()
    news_collector = NewsCollector()
    macro_collector = MacroDataCollector()
    sentiment_collector = SentimentCollector()
    cot_collector = COTCollector()
    tech_analyzer = EnhancedTechnicalAnalyzer()
    llm_analyzer = LLMAnalyzer()
    intermarket = IntermarketEngine()
    structure_analyzer = MarketStructureAnalyzer()
    session_filter = SessionFilter()
    institutional = InstitutionalSentimentAnalyzer()
    signal_gen = SignalGeneratorMK2()
    risk_manager = RiskManager()
    telegram = TelegramBotV2()
    db = SessionLocal()

    try:
        # ── Dati globali ─────────────────────────────────────────────────
        news = news_collector.collect_all()
        macro_events = macro_collector.get_forex_factory_calendar()
        cot_data = cot_collector.fetch_cot_data()
        intermarket_data = intermarket.get_all_pairs_scores(FOREX_PAIRS)
        inst_news = institutional.fetch_institutional_news()
        esi = institutional.get_economic_surprise_index()

        regime = intermarket_data.get("regime", {})
        dxy = intermarket_data.get("dxy", {})

        logger.info(f"🌍 Regime: {regime.get('regime')} | DXY: {dxy.get('bias')} | ESI: {esi.get('us_esi_score', 0):.1f}")
        logger.info(f"📰 {len(news)} news | 📅 {len(macro_events)} eventi | 🏦 {len(inst_news)} istituzionali")

        # Context globale per Telegram
        market_context = {
            "regime": regime.get("regime", "N/A"),
            "dxy_bias": dxy.get("bias", "neutral"),
            "session": "N/A",
            "esi": esi.get("us_esi_score", 0),
        }

        # Pre-news alert check
        blackout = macro_collector.is_news_blackout(minutes_buffer=20)
        if blackout:
            currencies = list(blackout.keys())
            logger.warning(f"⚠️ News blackout attivo per: {currencies}")

        # ── Analisi per coppia ────────────────────────────────────────────
        for symbol in FOREX_PAIRS:
            try:
                # LAYER 1: Session filter
                session_quality = session_filter.get_session_quality(symbol)
                market_context["session"] = session_quality.get("session", "N/A")

                if not session_quality["tradeable"]:
                    logger.debug(f"  {symbol}: sessione non ottimale ({session_quality['reason']})")
                    continue

                # Price data
                df_h1 = price_collector.get_ohlcv_yahoo(symbol, "1h", days=90)
                df_h4 = price_collector.get_ohlcv_yahoo(symbol, "4h", days=180)

                if df_h1 is None or len(df_h1) < 60:
                    continue

                current_price = float(df_h1["close"].iloc[-1])

                # LAYER 2: Market structure
                structure = structure_analyzer.get_market_structure(df_h1)
                if structure["direction"] == "neutral":
                    logger.debug(f"  {symbol}: struttura laterale — skip")
                    continue

                # LAYER 3: Technical analysis
                if df_h4 is not None and len(df_h4) >= 60:
                    technical = tech_analyzer.get_multiframe_score(df_h1, df_h4)
                else:
                    technical = tech_analyzer.get_technical_score(df_h1)
                    technical["confirmed_by_h4"] = False

                if technical.get("filtered"):
                    logger.debug(f"  {symbol}: filtrato ({technical.get('filter_reason')})")
                    continue

                technical["patterns"] = tech_analyzer.detect_patterns(df_h1)

                # Valida direzione vs struttura mercato
                struct_validation = structure_analyzer.validate_signal_direction(
                    "LONG" if technical.get("score", 0) > 0 else "SHORT", df_h1
                )
                if not struct_validation["valid"]:
                    logger.debug(f"  {symbol}: segnale contro struttura — penalizzato")
                    technical["score"] = technical.get("score", 0) * struct_validation["multiplier"]

                # LAYER 4: Intermarket
                pair_intermarket = intermarket_data.get("pairs", {}).get(symbol, {})

                # LAYER 5: Sentiment completo
                pair_news = [n for n in news if symbol in n.get("related_symbols", [])]
                if not pair_news:
                    pair_news = news[:5]

                llm_result = llm_analyzer.analyze_news(pair_news, symbol)
                social = sentiment_collector.get_combined_sentiment(symbol)
                cot_score = cot_collector.get_cot_score(symbol)
                inst_score = institutional.get_full_institutional_score(symbol)

                # Sentiment composito: LLM 40% + Institutional 35% + Social 15% + COT 10%
                combined_sentiment = {
                    "combined_score": (
                        llm_result.get("sentiment_score", 0) * 0.40 +
                        inst_score.get("institutional_score", 0) * 0.35 +
                        social.get("combined_score", 0) * 0.15 +
                        cot_score * 0.10
                    )
                }

                logger.debug(
                    f"  {symbol}: tech={technical.get('score', 0):+.0f} "
                    f"struct={structure['score']:+.0f} "
                    f"inter={pair_intermarket.get('score', 0):+.0f} "
                    f"sent={combined_sentiment['combined_score']:+.0f}"
                )

                # LAYER 6: Signal generation
                signal = signal_gen.check_all_strategies(
                    symbol=symbol,
                    technical=technical,
                    sentiment=combined_sentiment,
                    macro_events=macro_events,
                    cot_score=cot_score,
                    intermarket=pair_intermarket,
                    order_flow=None,  # Order flow attivo solo in live, non in backtest
                    current_price=current_price,
                )

                if not signal:
                    continue

                # Arricchisci reasoning con contesto completo
                signal["reasoning"] = (
                    f"{signal.get('reasoning', '')} | "
                    f"Struttura: {structure['structure']} | "
                    f"Sessione: {session_quality['session']} (score {session_quality['score']}) | "
                    f"Regime: {regime.get('regime')} | "
                    f"ESI: {esi.get('us_esi_score', 0):+.1f}"
                )

                # LAYER 7: Risk Manager
                approved = risk_manager.evaluate_signal(signal)

                if not approved:
                    db.add(SignalModel(
                        symbol=symbol, direction=signal.get("direction"),
                        strategy_name=signal.get("strategy_name"),
                        entry_price=current_price, raw_score=signal.get("raw_score"),
                        reasoning=signal.get("reasoning"), status="rejected",
                    ))
                    db.commit()
                    continue

                # LAYER 8: Output
                signal_counter += 1
                approved["risk_pct"] = RISK_PER_TRADE * 100

                db.add(SignalModel(
                    symbol=symbol, direction=approved.get("direction"),
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

                telegram.send_signal(approved, signal_counter, market_context)
                logger.info(
                    f"🚀 #{signal_counter:04d} {symbol} {approved['direction']} "
                    f"via {approved.get('strategy_name')} "
                    f"(score: {approved.get('raw_score'):.0f})"
                )

            except Exception as e:
                logger.error(f"Errore {symbol}: {e}", exc_info=True)
                continue

    except Exception as e:
        logger.error(f"Errore ciclo MK3: {e}", exc_info=True)
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("palantir_mk3.log"),
        ]
    )

    logger.info("=" * 60)
    logger.info("  PALANTIR MK3 — SISTEMA COMPLETO")
    logger.info("=" * 60)
    logger.info(f"  Paper trading: {PAPER_TRADING}")
    logger.info(f"  Coppie: {', '.join(FOREX_PAIRS)}")
    logger.info(f"  Filtri attivi: Session + Structure + EMA200 + ADX + MTF + Intermarket + Institutional")
    logger.info(f"  Soglia segnale: 80/100")
    logger.info("=" * 60)

    init_database()
    run_mk3_cycle()
