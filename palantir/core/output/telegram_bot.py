import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import asyncio
from datetime import datetime, timezone
from typing import Dict, Optional
import logging

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID

logger = logging.getLogger(__name__)


def format_signal_message(signal: Dict, signal_id: int) -> str:
    """Formatta il segnale nel formato professionale per Telegram"""
    symbol = signal.get("symbol", "N/A")
    direction = signal.get("direction", "N/A")
    entry = signal.get("entry_price", 0)
    sl = signal.get("stop_loss", 0)
    tp1 = signal.get("take_profit_1", 0)
    tp2 = signal.get("take_profit_2", 0)
    rr = signal.get("risk_reward", 0)
    size_pct = signal.get("risk_amount", 0) / signal.get("account_balance", 10000) * 100 if signal.get("account_balance") else 1.5
    strategy = signal.get("strategy_name", "N/A")
    reasoning = signal.get("reasoning", "N/A")
    sl_pips = signal.get("sl_pips", 0)
    tp1_pips = signal.get("tp1_pips", 0)

    direction_emoji = "📈" if direction == "LONG" else "📉"
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")

    message = f"""🎯 <b>SEGNALE #{signal_id:04d} — {symbol}</b>
━━━━━━━━━━━━━━━━━━━
{direction_emoji} <b>Direzione:</b> {direction}
💰 <b>Entry:</b> {entry:.5f}
🛑 <b>Stop Loss:</b> {sl:.5f} ({sl_pips:.0f} pip)
🎯 <b>Target 1:</b> {tp1:.5f} (+{tp1_pips:.0f} pip) [50%]
🎯 <b>Target 2:</b> {tp2:.5f} [50%]
⚖️ <b>Risk/Reward:</b> 1:{rr:.1f}
📊 <b>Risk:</b> {size_pct:.1f}% account

🔍 <b>Strategia:</b> {strategy}
📰 <b>Driver:</b> {reasoning[:100]}
⏰ <b>Validità:</b> 4 ore

✅ <b>RISK MANAGER: APPROVATO</b>
━━━━━━━━━━━━━━━━━━━
<i>Signal #{signal_id:04d} | {now}</i>"""

    return message


def format_daily_report(stats: Dict) -> str:
    """Formatta il report giornaliero"""
    date = datetime.now(timezone.utc).strftime("%d/%m/%Y")
    pnl_emoji = "🟢" if stats.get("daily_pnl", 0) >= 0 else "🔴"

    return f"""📊 <b>REPORT GIORNALIERO — {date}</b>
━━━━━━━━━━━━━━━━━━━
{pnl_emoji} <b>P&L Oggi:</b> {stats.get('daily_pnl', 0):+.2f} ({stats.get('daily_pnl_pct', 0):+.2f}%)
📡 <b>Segnali inviati:</b> {stats.get('signals_sent', 0)}
✅ <b>Win:</b> {stats.get('wins', 0)} | ❌ <b>Loss:</b> {stats.get('losses', 0)}
💼 <b>Balance:</b> {stats.get('account_balance', 0):.2f}
📉 <b>Max Drawdown:</b> {stats.get('max_drawdown', 0):.2f}%
━━━━━━━━━━━━━━━━━━━
<i>Palantir Trading System</i>"""


class TelegramBot:

    def __init__(self):
        self.token = TELEGRAM_BOT_TOKEN
        self.channel_id = TELEGRAM_CHANNEL_ID
        self.enabled = bool(self.token and self.channel_id)
        if not self.enabled:
            logger.warning("⚠️ Telegram non configurato — segnali solo in log")

    async def send_message(self, text: str) -> bool:
        """Invia un messaggio al canale Telegram"""
        if not self.enabled:
            logger.info(f"[TELEGRAM MOCK] {text[:100]}...")
            return True

        try:
            from telegram import Bot
            bot = Bot(token=self.token)
            await bot.send_message(
                chat_id=self.channel_id,
                text=text,
                parse_mode="HTML",
            )
            return True
        except Exception as e:
            logger.error(f"Errore Telegram: {e}")
            return False

    def send_signal(self, signal: Dict, signal_id: int) -> bool:
        """Invia segnale in modo sincrono"""
        message = format_signal_message(signal, signal_id)
        logger.info(f"📤 Invio segnale #{signal_id:04d} — {signal.get('symbol')} {signal.get('direction')}")
        return asyncio.run(self.send_message(message))

    def send_daily_report(self, stats: Dict) -> bool:
        """Invia report giornaliero"""
        message = format_daily_report(stats)
        return asyncio.run(self.send_message(message))

    def send_alert(self, text: str) -> bool:
        """Invia alert urgente"""
        message = f"🚨 <b>ALERT URGENTE</b>\n{text}"
        return asyncio.run(self.send_message(message))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    bot = TelegramBot()

    # Test con segnale mock
    mock_signal = {
        "symbol": "EUR/USD",
        "direction": "LONG",
        "entry_price": 1.08450,
        "stop_loss": 1.08180,
        "take_profit_1": 1.08990,
        "take_profit_2": 1.09260,
        "risk_reward": 2.0,
        "risk_amount": 150,
        "account_balance": 10000,
        "strategy_name": "Macro Momentum",
        "reasoning": "CPI US above consensus + Sentiment +78",
        "sl_pips": 27,
        "tp1_pips": 54,
    }

    print(format_signal_message(mock_signal, 142))
    bot.send_signal(mock_signal, 142)
