from sqlalchemy import create_engine, Column, Integer, Float, String, DateTime, Boolean, Text, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import DATABASE_URL

Base = declarative_base()
engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)


class Price(Base):
    """Dati OHLCV per ogni coppia forex"""
    __tablename__ = "prices"

    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False, index=True)
    timeframe = Column(String, nullable=False)
    timestamp = Column(DateTime, nullable=False, index=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)


class News(Base):
    """News finanziarie da tutte le fonti"""
    __tablename__ = "news"

    id = Column(Integer, primary_key=True)
    source = Column(String)
    title = Column(String, nullable=False)
    summary = Column(Text)
    url = Column(String, unique=True)
    published_at = Column(DateTime, index=True)
    related_symbols = Column(JSON)                  # ["EUR/USD", "GBP/USD"]
    sentiment_score = Column(Float)                 # -100 a +100, calcolato da AI
    sentiment_analyzed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class MacroEvent(Base):
    """Calendario eventi macroeconomici"""
    __tablename__ = "macro_events"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    country = Column(String)
    currency = Column(String, index=True)
    impact = Column(String)                         # "high", "medium", "low"
    scheduled_at = Column(DateTime, nullable=False, index=True)
    actual_value = Column(Float)
    forecast_value = Column(Float)
    previous_value = Column(Float)
    surprise = Column(Float)                        # actual - forecast
    created_at = Column(DateTime, default=datetime.utcnow)


class Signal(Base):
    """Segnali generati dal sistema"""
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True)
    symbol = Column(String, nullable=False, index=True)
    direction = Column(String)                      # "LONG" o "SHORT"
    strategy_name = Column(String)
    entry_price = Column(Float)
    stop_loss = Column(Float)
    take_profit_1 = Column(Float)
    take_profit_2 = Column(Float)
    position_size = Column(Float)
    risk_amount = Column(Float)
    risk_reward = Column(Float)
    raw_score = Column(Float)
    technical_score = Column(Float)
    sentiment_score = Column(Float)
    reasoning = Column(Text)
    status = Column(String, default="pending")      # "pending", "sent", "rejected", "expired"
    rejection_reason = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    sent_at = Column(DateTime)


class Trade(Base):
    """Trade eseguiti (paper o reali)"""
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True)
    signal_id = Column(Integer)
    symbol = Column(String, nullable=False)
    direction = Column(String)
    entry_price = Column(Float)
    exit_price = Column(Float)
    stop_loss = Column(Float)
    take_profit_1 = Column(Float)
    take_profit_2 = Column(Float)
    position_size = Column(Float)
    pnl = Column(Float)
    pnl_pct = Column(Float)
    outcome = Column(String)                        # "win", "loss", "breakeven"
    exit_reason = Column(String)                    # "tp1", "tp2", "sl", "manual"
    opened_at = Column(DateTime)
    closed_at = Column(DateTime)
    paper_trade = Column(Boolean, default=True)


class DailyStats(Base):
    """Statistiche giornaliere del sistema"""
    __tablename__ = "daily_stats"

    id = Column(Integer, primary_key=True)
    date = Column(DateTime, nullable=False, unique=True)
    total_signals = Column(Integer, default=0)
    signals_sent = Column(Integer, default=0)
    signals_rejected = Column(Integer, default=0)
    trades_opened = Column(Integer, default=0)
    trades_closed = Column(Integer, default=0)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    daily_pnl = Column(Float, default=0.0)
    daily_pnl_pct = Column(Float, default=0.0)
    cumulative_pnl = Column(Float, default=0.0)
    max_drawdown = Column(Float, default=0.0)
    account_balance = Column(Float)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
