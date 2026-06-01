import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional
import logging

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID

logger = logging.getLogger(__name__)


def format_signal_v2(signal: Dict, signal_id: int, market_context: Dict = None) -> str:
    """
    Formato segnale completo con reasoning dettagliato.
    Il trader capisce PERCHÉ il sistema è entrato — non solo dove.
    """
    symbol = signal.get("symbol", "N/A")
    direction = signal.get("direction", "N/A")
    entry = signal.get("entry_price", 0)
    sl = signal.get("stop_loss", 0)
    tp1 = signal.get("take_profit_1", 0)
    tp2 = signal.get("take_profit_2", 0)
    rr = signal.get("risk_reward", 0)
    strategy = signal.get("strategy_name", "N/A")
    reasoning = signal.get("reasoning", "N/A")
    score = signal.get("raw_score", 0)
    risk_pct = signal.get("risk_pct", 1.5)

    sl_pips = abs(entry - sl) * (10000 if "JPY" not in symbol else 100)
    tp1_pips = abs(entry - tp1) * (10000 if "JPY" not in symbol else 100)
    tp2_pips = abs(entry - tp2) * (10000 if "JPY" not in symbol else 100)

    dir_emoji = "📈" if direction == "LONG" else "📉"
    strategy_emoji = {
        "Stop Hunt Reversal": "🎯",
        "Intermarket Divergence": "🌍",
        "Macro Momentum MK2": "📊",
        "Technical Confluence MK2": "⚙️",
        "Sentiment Divergence": "💬",
    }.get(strategy, "📡")

    now = datetime.now(timezone.utc).strftime("%H:%M UTC")

    # Contesto di mercato
    context_text = ""
    if market_context:
        regime = market_context.get("regime", "N/A")
        dxy = market_context.get("dxy_bias", "neutral")
        session = market_context.get("session", "N/A")
        context_text = f"""
📋 <b>Contesto Mercato</b>
├ Regime: {regime}
├ DXY Bias: {dxy}
└ Sessione: {session}"""

    message = f"""{strategy_emoji} <b>SEGNALE #{signal_id:04d} — {symbol}</b>
━━━━━━━━━━━━━━━━━━━━━
{dir_emoji} <b>Direzione:</b> {direction}
💰 <b>Entry:</b> {entry:.5f}
🛑 <b>Stop Loss:</b> {sl:.5f} <i>(-{sl_pips:.0f} pip)</i>
🎯 <b>Target 1:</b> {tp1:.5f} <i>(+{tp1_pips:.0f} pip) [chiudi 50%]</i>
🚀 <b>Target 2:</b> {tp2:.5f} <i>(+{tp2_pips:.0f} pip) [resto]</i>
⚖️ <b>R:R:</b> 1:{rr:.1f}
📊 <b>Risk:</b> {risk_pct:.1f}% account
━━━━━━━━━━━━━━━━━━━━━
{strategy_emoji} <b>Strategia:</b> {strategy}
🧠 <b>Reasoning:</b>
<i>{reasoning[:200]}</i>
🎯 <b>Score AI:</b> {score:.0f}/100{context_text}
━━━━━━━━━━━━━━━━━━━━━
✅ <b>RISK MANAGER: APPROVATO</b>
<i>#{signal_id:04d} | {now} | Paper Trading</i>"""

    return message


def format_tp_hit(signal_id: int, symbol: str, tp_level: int, pnl_pct: float) -> str:
    emoji = "🎯" if tp_level == 1 else "🚀"
    return f"""{emoji} <b>TARGET {tp_level} RAGGIUNTO — {symbol}</b>
━━━━━━━━━━━━━━━━━━━
Signal #{signal_id:04d} ha raggiunto TP{tp_level}
💰 P&L parziale: <b>{pnl_pct:+.2f}%</b>
{'🔒 Stop Loss spostato a breakeven' if tp_level == 1 else '✅ Trade chiuso completamente'}
━━━━━━━━━━━━━━━━━━━"""


def format_sl_hit(signal_id: int, symbol: str, pnl_pct: float) -> str:
    return f"""🛑 <b>STOP LOSS — {symbol}</b>
━━━━━━━━━━━━━━━━━━━
Signal #{signal_id:04d} ha colpito lo stop loss
📉 P&L: <b>{pnl_pct:.2f}%</b>
<i>Perdita accettabile nel piano di gestione del rischio.
Il sistema continua a operare normalmente.</i>
━━━━━━━━━━━━━━━━━━━"""


def format_pre_news_alert(event_name: str, currency: str, minutes: int, open_signals: List[str]) -> str:
    signals_text = "\n".join([f"• {s}" for s in open_signals]) if open_signals else "Nessuna posizione aperta"
    return f"""⚠️ <b>ALERT PRE-NEWS</b>
━━━━━━━━━━━━━━━━━━━
📅 <b>{event_name}</b> ({currency})
⏰ Tra <b>{minutes} minuti</b>

🔒 Segnali bloccati per ±15 minuti
📋 Posizioni aperte:
{signals_text}
━━━━━━━━━━━━━━━━━━━
<i>Attenzione: alta volatilità prevista</i>"""


def format_daily_report(stats: Dict, top_signals: List[Dict] = None) -> str:
    date = datetime.now(timezone.utc).strftime("%d/%m/%Y")
    pnl = stats.get("daily_pnl_pct", 0)
    pnl_emoji = "🟢" if pnl >= 0 else "🔴"
    wr = stats.get("win_rate", 0)

    top_text = ""
    if top_signals:
        top_text = "\n📡 <b>Segnali del giorno:</b>"
        for s in top_signals[:3]:
            outcome_e = "✅" if s.get("outcome") == "win" else "❌" if s.get("outcome") == "loss" else "⏳"
            top_text += f"\n{outcome_e} {s.get('symbol')} {s.get('direction')} — {s.get('pnl', 0):+.1f}%"

    return f"""📊 <b>REPORT GIORNALIERO — {date}</b>
━━━━━━━━━━━━━━━━━━━
{pnl_emoji} <b>P&L Oggi:</b> {pnl:+.2f}%
🎯 <b>Win Rate:</b> {wr:.1f}%
📡 <b>Segnali inviati:</b> {stats.get('signals_sent', 0)}
✅ Win: {stats.get('wins', 0)} | ❌ Loss: {stats.get('losses', 0)}
💼 <b>Balance:</b> {stats.get('account_balance', 0):.2f}
📉 <b>Drawdown oggi:</b> {stats.get('daily_drawdown', 0):.2f}%
📈 <b>Drawdown totale:</b> {stats.get('total_drawdown', 0):.2f}%{top_text}
━━━━━━━━━━━━━━━━━━━
<i>Palantir AI Trading System</i>"""


def format_drawdown_alert(current_dd: float, limit_dd: float) -> str:
    pct_used = current_dd / limit_dd * 100
    return f"""🚨 <b>ALERT DRAWDOWN</b>
━━━━━━━━━━━━━━━━━━━
📉 Drawdown attuale: <b>{current_dd:.1f}%</b>
⚠️ Limite: {limit_dd:.1f}%
📊 Utilizzato: {pct_used:.0f}%

{'🔴 SISTEMA BLOCCATO — limite raggiunto' if pct_used >= 100 else '⚠️ Attenzione — avvicinamento al limite'}
━━━━━━━━━━━━━━━━━━━"""


class TelegramBotV2:
    """Bot Telegram potenziato con alert intelligenti e reasoning completo"""

    def __init__(self):
        self.token = TELEGRAM_BOT_TOKEN
        self.channel_id = TELEGRAM_CHANNEL_ID
        self.enabled = bool(self.token and self.channel_id)
        if not self.enabled:
            logger.warning("⚠️ Telegram non configurato — output solo in log")

    async def _send(self, text: str) -> bool:
        if not self.enabled:
            logger.info(f"[TELEGRAM]\n{text[:200]}...")
            return True
        try:
            from telegram import Bot
            bot = Bot(token=self.token)
            # Telegram ha limite 4096 char per messaggio
            if len(text) > 4096:
                text = text[:4090] + "..."
            await bot.send_message(chat_id=self.channel_id, text=text, parse_mode="HTML")
            return True
        except Exception as e:
            logger.error(f"Errore Telegram: {e}")
            return False

    def send_signal(self, signal: Dict, signal_id: int, market_context: Dict = None) -> bool:
        message = format_signal_v2(signal, signal_id, market_context)
        logger.info(f"📤 Segnale #{signal_id:04d}: {signal.get('symbol')} {signal.get('direction')}")
        return asyncio.run(self._send(message))

    def send_tp_hit(self, signal_id: int, symbol: str, tp_level: int, pnl_pct: float) -> bool:
        return asyncio.run(self._send(format_tp_hit(signal_id, symbol, tp_level, pnl_pct)))

    def send_sl_hit(self, signal_id: int, symbol: str, pnl_pct: float) -> bool:
        return asyncio.run(self._send(format_sl_hit(signal_id, symbol, pnl_pct)))

    def send_pre_news_alert(self, event_name: str, currency: str, minutes: int, open_signals: List[str]) -> bool:
        return asyncio.run(self._send(format_pre_news_alert(event_name, currency, minutes, open_signals)))

    def send_daily_report(self, stats: Dict, top_signals: List[Dict] = None) -> bool:
        return asyncio.run(self._send(format_daily_report(stats, top_signals)))

    def send_drawdown_alert(self, current_dd: float, limit_dd: float) -> bool:
        return asyncio.run(self._send(format_drawdown_alert(current_dd, limit_dd)))

    def send_text(self, text: str) -> bool:
        return asyncio.run(self._send(text))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    bot = TelegramBotV2()

    mock_signal = {
        "symbol": "EUR/USD", "direction": "LONG",
        "entry_price": 1.08450, "stop_loss": 1.08180,
        "take_profit_1": 1.08990, "take_profit_2": 1.09530,
        "risk_reward": 2.0, "risk_pct": 1.5,
        "strategy_name": "Stop Hunt Reversal",
        "reasoning": "Stop hunt bullish rilevato su 1.08180 (swing low testato 3 volte). Wick 3x il body. DXY in calo -0.3%. Regime risk-on. ADX 28 uptrend confermato da H4.",
        "raw_score": 95,
    }
    mock_context = {"regime": "risk_on", "dxy_bias": "mild_anti_usd", "session": "overlap"}

    print(format_signal_v2(mock_signal, 1, mock_context))
    bot.send_signal(mock_signal, 1, mock_context)
