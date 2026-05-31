import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from datetime import datetime, timezone
from typing import Dict, Optional, List
import logging

from config.settings import (
    ACCOUNT_BALANCE, RISK_PER_TRADE, MAX_DAILY_LOSS,
    MAX_OVERALL_DRAWDOWN, MAX_POSITIONS, ATR_SL_MULTIPLIER,
    ATR_TP1_MULTIPLIER, ATR_TP2_MULTIPLIER, MIN_RISK_REWARD,
    WEEKEND_CLOSE, NEWS_BLACKOUT_MINUTES, FOREX_PAIRS,
)

logger = logging.getLogger(__name__)

# Coppie altamente correlate — non aprire entrambe nella stessa direzione
CORRELATED_PAIRS = [
    ("EUR/USD", "GBP/USD"),   # Correlazione ~0.85
    ("AUD/USD", "EUR/USD"),   # Correlazione ~0.70
]


class RiskManager:
    """
    The Gatekeeper — nessun segnale passa senza approvazione del Risk Manager.
    Calcola size, SL/TP dinamici, controlla limiti prop firm.
    """

    def __init__(self, account_balance: float = None):
        self.account_balance = account_balance or ACCOUNT_BALANCE
        self.peak_balance = self.account_balance
        self.daily_start_balance = self.account_balance
        self.open_positions: List[Dict] = []
        self.daily_pnl = 0.0
        self.total_pnl = 0.0

    def evaluate_signal(self, signal: Dict) -> Optional[Dict]:
        """
        Valuta un segnale e decide se approvarlo o rifiutarlo.
        Ritorna il segnale arricchito oppure None se rifiutato.
        """
        symbol = signal.get("symbol")
        direction = signal.get("direction")
        entry_price = signal.get("entry_price")
        atr = signal.get("atr", 0.001)
        raw_score = signal.get("raw_score", 0)

        # ── CHECK 1: Score minimo ────────────────────────────
        if raw_score < 65:
            return self._reject(signal, f"Score troppo basso: {raw_score} < 65")

        # ── CHECK 2: Max daily loss ──────────────────────────
        daily_loss_pct = abs(min(0, self.daily_pnl)) / self.daily_start_balance
        if daily_loss_pct >= MAX_DAILY_LOSS:
            return self._reject(signal, f"Max daily loss raggiunto: {daily_loss_pct:.1%}")

        # ── CHECK 3: Max overall drawdown ────────────────────
        current_drawdown = (self.peak_balance - self.account_balance) / self.peak_balance
        if current_drawdown >= MAX_OVERALL_DRAWDOWN:
            return self._reject(signal, f"Max drawdown raggiunto: {current_drawdown:.1%}")

        # ── CHECK 4: Max positions ───────────────────────────
        if len(self.open_positions) >= MAX_POSITIONS:
            return self._reject(signal, f"Max posizioni aperte: {len(self.open_positions)}/{MAX_POSITIONS}")

        # ── CHECK 5: Una posizione per coppia ────────────────
        for pos in self.open_positions:
            if pos["symbol"] == symbol:
                return self._reject(signal, f"Posizione già aperta su {symbol}")

        # ── CHECK 6: Correlation filter ──────────────────────
        for pair1, pair2 in CORRELATED_PAIRS:
            if symbol in (pair1, pair2):
                other = pair2 if symbol == pair1 else pair1
                for pos in self.open_positions:
                    if pos["symbol"] == other and pos["direction"] == direction:
                        return self._reject(signal, f"Correlazione: {symbol} e {other} già entrambi {direction}")

        # ── CHECK 7: Weekend filter ───────────────────────────
        if WEEKEND_CLOSE:
            now = datetime.now(timezone.utc)
            if now.weekday() == 4 and now.hour >= 21:  # Venerdì dopo le 21 UTC
                return self._reject(signal, "Weekend filter: mercato chiude tra poco")
            if now.weekday() in (5, 6):  # Sabato/Domenica
                return self._reject(signal, "Weekend filter: mercato chiuso")

        # ── CALCOLA SL/TP DINAMICI ──────────────────────────
        if atr <= 0:
            atr = entry_price * 0.001  # Fallback: 0.1% del prezzo

        if direction == "LONG":
            stop_loss = entry_price - (atr * ATR_SL_MULTIPLIER)
            take_profit_1 = entry_price + (atr * ATR_TP1_MULTIPLIER)
            take_profit_2 = entry_price + (atr * ATR_TP2_MULTIPLIER)
        else:  # SHORT
            stop_loss = entry_price + (atr * ATR_SL_MULTIPLIER)
            take_profit_1 = entry_price - (atr * ATR_TP1_MULTIPLIER)
            take_profit_2 = entry_price - (atr * ATR_TP2_MULTIPLIER)

        # ── CALCOLA RISK/REWARD ──────────────────────────────
        sl_distance = abs(entry_price - stop_loss)
        tp1_distance = abs(entry_price - take_profit_1)
        risk_reward = tp1_distance / sl_distance if sl_distance > 0 else 0

        if risk_reward < MIN_RISK_REWARD:
            return self._reject(signal, f"R:R troppo basso: {risk_reward:.2f} < {MIN_RISK_REWARD}")

        # ── CALCOLA POSITION SIZE ────────────────────────────
        risk_amount = self.account_balance * RISK_PER_TRADE
        pip_value = 10  # Standard per lot da 100k — approssimazione
        sl_pips = sl_distance * 10000 if "JPY" not in symbol else sl_distance * 100
        position_size = risk_amount / (sl_pips * pip_value) if sl_pips > 0 else 0.01
        position_size = round(max(0.01, min(position_size, 10.0)), 2)

        # ── SEGNALE APPROVATO ────────────────────────────────
        enriched = {
            **signal,
            "stop_loss": round(stop_loss, 5),
            "take_profit_1": round(take_profit_1, 5),
            "take_profit_2": round(take_profit_2, 5),
            "position_size": position_size,
            "risk_amount": round(risk_amount, 2),
            "risk_reward": round(risk_reward, 2),
            "sl_pips": round(sl_pips, 1),
            "tp1_pips": round(tp1_distance * 10000 if "JPY" not in symbol else tp1_distance * 100, 1),
            "approved": True,
            "approved_at": datetime.now(timezone.utc).isoformat(),
        }

        logger.info(f"✅ SEGNALE APPROVATO: {symbol} {direction} | Entry: {entry_price} | SL: {stop_loss:.5f} | TP1: {take_profit_1:.5f} | R:R {risk_reward:.2f} | Size: {position_size}")
        return enriched

    def _reject(self, signal: Dict, reason: str) -> None:
        logger.warning(f"❌ SEGNALE RIFIUTATO [{signal.get('symbol')}]: {reason}")
        return None

    def update_balance(self, pnl: float):
        """Aggiorna il bilancio dopo la chiusura di un trade"""
        self.account_balance += pnl
        self.daily_pnl += pnl
        self.total_pnl += pnl
        if self.account_balance > self.peak_balance:
            self.peak_balance = self.account_balance

    def reset_daily(self):
        """Reset giornaliero — da chiamare a mezzanotte UTC"""
        self.daily_pnl = 0.0
        self.daily_start_balance = self.account_balance
        logger.info(f"🔄 Reset giornaliero. Balance: {self.account_balance:.2f}")

    def get_status(self) -> Dict:
        """Stato attuale del risk manager"""
        daily_loss_pct = abs(min(0, self.daily_pnl)) / self.daily_start_balance
        drawdown = (self.peak_balance - self.account_balance) / self.peak_balance
        return {
            "account_balance": self.account_balance,
            "daily_pnl": round(self.daily_pnl, 2),
            "daily_loss_pct": round(daily_loss_pct * 100, 2),
            "total_drawdown_pct": round(drawdown * 100, 2),
            "open_positions": len(self.open_positions),
            "daily_limit_used_pct": round(daily_loss_pct / MAX_DAILY_LOSS * 100, 1),
            "drawdown_limit_used_pct": round(drawdown / MAX_OVERALL_DRAWDOWN * 100, 1),
        }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    rm = RiskManager(account_balance=10000)

    # Test segnale normale
    signal = {
        "symbol": "EUR/USD",
        "direction": "LONG",
        "entry_price": 1.0850,
        "atr": 0.0012,
        "raw_score": 72,
        "strategy_name": "Technical Confluence",
    }

    result = rm.evaluate_signal(signal)
    if result:
        print(f"\n✅ Segnale approvato:")
        print(f"  SL: {result['stop_loss']}")
        print(f"  TP1: {result['take_profit_1']}")
        print(f"  TP2: {result['take_profit_2']}")
        print(f"  Size: {result['position_size']} lots")
        print(f"  R:R: {result['risk_reward']}")

    print(f"\n📊 Risk Status: {rm.get_status()}")
