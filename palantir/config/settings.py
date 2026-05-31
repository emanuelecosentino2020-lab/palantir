import os
from dotenv import load_dotenv

load_dotenv()

# ── API KEYS ────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY")
ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")
OANDA_API_KEY = os.getenv("OANDA_API_KEY")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "palantir_trading_bot/1.0")
TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

# ── DATABASE ────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///palantir.db")

# ── SISTEMA ─────────────────────────────────────────────
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() == "true"

# ── COPPIE FOREX TARGET ─────────────────────────────────
FOREX_PAIRS = [
    "EUR/USD",
    "GBP/USD",
    "USD/JPY",
    "AUD/USD",
    "USD/CAD",
    "EUR/GBP",
]

# ── TIMEFRAME ───────────────────────────────────────────
TIMEFRAMES = ["1min", "5min", "15min", "1h", "4h", "1day"]
PRIMARY_TIMEFRAME = "1h"
SIGNAL_TIMEFRAME = "15min"

# ── RISK MANAGEMENT ─────────────────────────────────────
ACCOUNT_BALANCE = float(os.getenv("ACCOUNT_BALANCE", 10000))
RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", 0.015))       # 1.5%
MAX_DAILY_LOSS = float(os.getenv("MAX_DAILY_LOSS", 0.04))          # 4%
MAX_OVERALL_DRAWDOWN = float(os.getenv("MAX_OVERALL_DRAWDOWN", 0.07))  # 7%
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", 3))
ATR_SL_MULTIPLIER = 1.5
ATR_TP1_MULTIPLIER = 3.0
ATR_TP2_MULTIPLIER = 4.5
MIN_RISK_REWARD = 1.8
WEEKEND_CLOSE = True
NEWS_BLACKOUT_MINUTES = 15                                          # Blocco pre/post news HIGH

# ── AI ANALYSIS ─────────────────────────────────────────
MIN_SIGNAL_SCORE = 65                                               # Score minimo per generare segnale
LLM_MODEL = "claude-opus-4-6"
SENTIMENT_WEIGHTS = {
    "technical": 0.60,
    "sentiment": 0.30,
    "macro": 0.10,
}

# ── SCHEDULER ───────────────────────────────────────────
PRICE_DATA_INTERVAL_MINUTES = 5
NEWS_INTERVAL_MINUTES = 10
MACRO_INTERVAL_MINUTES = 60
SIGNAL_CHECK_INTERVAL_MINUTES = 15

# ── PROP FIRM PRESETS ───────────────────────────────────
PROP_FIRM_PRESETS = {
    "ftmo_25k": {
        "account_balance": 25000,
        "profit_target": 0.10,
        "max_daily_loss": 0.05,
        "max_overall_loss": 0.10,
        "risk_per_trade": 0.015,
        "max_positions": 2,
    },
    "funding_pips_25k": {
        "account_balance": 25000,
        "profit_target": 0.08,
        "max_daily_loss": 0.04,
        "max_overall_loss": 0.08,
        "risk_per_trade": 0.010,
        "max_positions": 2,
    },
}
