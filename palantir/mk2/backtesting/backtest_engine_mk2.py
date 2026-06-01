import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from typing import Dict, List, Optional
import logging
import json

from core.data_collector.price_data import PriceDataCollector
from core.ai_analyzer.technical_analyzer_v2 import EnhancedTechnicalAnalyzer
from core.ai_analyzer.order_flow import OrderFlowAnalyzer
from core.ai_analyzer.signal_generator_v2 import SignalGeneratorMK2
from config.settings import (
    FOREX_PAIRS, ACCOUNT_BALANCE, RISK_PER_TRADE,
    ATR_SL_MULTIPLIER, ATR_TP1_MULTIPLIER, ATR_TP2_MULTIPLIER,
    MIN_RISK_REWARD,
)

logger = logging.getLogger(__name__)

MIN_SCORE_MK2 = 80


class BacktestEngineMK2:
    """
    Backtest engine per il sistema MK2.
    Include tutti i nuovi filtri e strategie.
    """

    SPREAD_PIPS = {
        "EUR/USD": 1.5, "GBP/USD": 2.0, "USD/JPY": 1.5,
        "AUD/USD": 2.0, "USD/CAD": 2.5, "EUR/GBP": 2.0,
    }

    def __init__(self, initial_balance: float = None):
        self.initial_balance = initial_balance or ACCOUNT_BALANCE
        self.price_collector = PriceDataCollector()
        self.tech_analyzer = EnhancedTechnicalAnalyzer()
        self.order_flow = OrderFlowAnalyzer()
        self.signal_gen = SignalGeneratorMK2()

    def _pip_value(self, symbol: str) -> float:
        return 0.0001 if "JPY" not in symbol else 0.01

    def _apply_spread(self, price: float, direction: str, symbol: str) -> float:
        spread = self.SPREAD_PIPS.get(symbol, 2.0) * self._pip_value(symbol)
        slippage = 0.5 * self._pip_value(symbol)
        return price + spread + slippage if direction == "LONG" else price - spread - slippage

    def _risk_check(self, signal: Dict, balance: float) -> Optional[Dict]:
        entry_price = signal.get("entry_price", 0)
        direction = signal.get("direction")
        atr = signal.get("atr", 0.001)
        raw_score = signal.get("raw_score", 0)

        if raw_score < MIN_SCORE_MK2:
            return None
        if entry_price <= 0:
            return None
        if atr <= 0:
            atr = entry_price * 0.001

        if direction == "LONG":
            stop_loss = entry_price - (atr * ATR_SL_MULTIPLIER)
            take_profit_1 = entry_price + (atr * ATR_TP1_MULTIPLIER)
            take_profit_2 = entry_price + (atr * ATR_TP2_MULTIPLIER)
        else:
            stop_loss = entry_price + (atr * ATR_SL_MULTIPLIER)
            take_profit_1 = entry_price - (atr * ATR_TP1_MULTIPLIER)
            take_profit_2 = entry_price - (atr * ATR_TP2_MULTIPLIER)

        sl_distance = abs(entry_price - stop_loss)
        tp1_distance = abs(entry_price - take_profit_1)
        risk_reward = tp1_distance / sl_distance if sl_distance > 0 else 0

        if risk_reward < MIN_RISK_REWARD:
            return None

        risk_amount = balance * RISK_PER_TRADE

        return {
            **signal,
            "stop_loss": round(stop_loss, 5),
            "take_profit_1": round(take_profit_1, 5),
            "take_profit_2": round(take_profit_2, 5),
            "risk_amount": round(risk_amount, 2),
            "risk_reward": round(risk_reward, 2),
        }

    def run_backtest(self, symbol: str, df_h1: pd.DataFrame, df_h4: pd.DataFrame = None, lookback: int = 80) -> List[Dict]:
        trades = []
        balance = self.initial_balance
        open_trade = None

        logger.info(f"📊 Backtest MK2 {symbol}: {len(df_h1)} candele H1")

        for i in range(lookback, len(df_h1)):
            df_slice_h1 = df_h1.iloc[:i+1]
            current_bar = df_h1.iloc[i]
            current_price = float(current_bar["close"])
            current_high = float(current_bar["high"])
            current_low = float(current_bar["low"])

            # Gestisci trade aperto
            if open_trade:
                hit_sl = hit_tp1 = hit_tp2 = False

                if open_trade["direction"] == "LONG":
                    if current_low <= open_trade["stop_loss"]:
                        hit_sl = True
                    elif current_high >= open_trade["take_profit_2"]:
                        hit_tp2 = True
                    elif current_high >= open_trade["take_profit_1"] and not open_trade.get("tp1_hit"):
                        hit_tp1 = True
                else:
                    if current_high >= open_trade["stop_loss"]:
                        hit_sl = True
                    elif current_low <= open_trade["take_profit_2"]:
                        hit_tp2 = True
                    elif current_low <= open_trade["take_profit_1"] and not open_trade.get("tp1_hit"):
                        hit_tp1 = True

                if hit_sl:
                    pnl = -open_trade["risk_amount"]
                    balance += pnl
                    open_trade.update({"exit_price": open_trade["stop_loss"], "exit_reason": "sl",
                                       "pnl": round(pnl, 2), "outcome": "loss", "closed_at": str(df_h1.index[i])})
                    trades.append(open_trade)
                    open_trade = None
                elif hit_tp2:
                    pnl = open_trade["risk_amount"] * open_trade["risk_reward"]
                    balance += pnl
                    open_trade.update({"exit_price": open_trade["take_profit_2"], "exit_reason": "tp2",
                                       "pnl": round(pnl, 2), "outcome": "win", "closed_at": str(df_h1.index[i])})
                    trades.append(open_trade)
                    open_trade = None
                elif hit_tp1:
                    pnl = open_trade["risk_amount"] * open_trade["risk_reward"] * 0.5
                    balance += pnl
                    open_trade["tp1_hit"] = True
                    open_trade["stop_loss"] = open_trade["entry_price"]
                elif i - open_trade.get("open_bar", i) >= 96:
                    pnl = (current_price - open_trade["entry_price"]) * (1 if open_trade["direction"] == "LONG" else -1)
                    pnl_norm = pnl / max(abs(open_trade["entry_price"] - open_trade["stop_loss"]), 0.0001) * open_trade["risk_amount"]
                    balance += pnl_norm
                    open_trade.update({"exit_price": current_price, "exit_reason": "expired",
                                       "pnl": round(pnl_norm, 2), "outcome": "win" if pnl_norm > 0 else "loss",
                                       "closed_at": str(df_h1.index[i])})
                    trades.append(open_trade)
                    open_trade = None
                continue

            # Genera segnale MK2
            if df_h4 is not None and len(df_h4) >= 60:
                h4_slice = df_h4.iloc[:min(i + 1, len(df_h4))]
                if len(h4_slice) >= 60:
                    technical = self.tech_analyzer.get_multiframe_score(df_slice_h1, h4_slice)
                else:
                    technical = self.tech_analyzer.get_technical_score(df_slice_h1)
                    technical["confirmed_by_h4"] = False
            else:
                technical = self.tech_analyzer.get_technical_score(df_slice_h1)
                technical["confirmed_by_h4"] = False

            technical["patterns"] = self.tech_analyzer.detect_patterns(df_slice_h1)

            # Order flow
            of = {"score": 0, "direction": "neutral", "signals": [], "stop_hunt": None}(df_slice_h1, current_bar=i)

            # Sentiment mock (basato su tecnica)
            mock_sentiment = {"combined_score": technical.get("score", 0) * 0.3}

            signal = self.signal_gen.check_all_strategies(
                symbol=symbol,
                technical=technical,
                sentiment=mock_sentiment,
                order_flow=of,
                current_price=current_price,
            )

            if signal:
                entry = self._apply_spread(current_price, signal["direction"], symbol)
                signal["entry_price"] = entry
                approved = self._risk_check(signal, balance)
                if approved:
                    open_trade = {**approved, "open_bar": i, "opened_at": str(df_h1.index[i]), "tp1_hit": False}

        if open_trade:
            open_trade.update({"exit_reason": "end_of_data", "pnl": 0, "outcome": "open"})
            trades.append(open_trade)

        return trades

    def calculate_metrics(self, trades: List[Dict], symbol: str = "") -> Dict:
        closed = [t for t in trades if t.get("outcome") in ("win", "loss")]
        if not closed:
            return {"symbol": symbol, "error": "Nessun trade chiuso", "total_trades": 0}

        wins = [t for t in closed if t["outcome"] == "win"]
        losses = [t for t in closed if t["outcome"] == "loss"]
        win_rate = len(wins) / len(closed) * 100
        avg_win = np.mean([t["pnl"] for t in wins]) if wins else 0
        avg_loss = abs(np.mean([t["pnl"] for t in losses])) if losses else 0
        total_win = sum(t["pnl"] for t in wins)
        total_loss = abs(sum(t["pnl"] for t in losses))
        profit_factor = total_win / total_loss if total_loss > 0 else 999
        total_pnl = sum(t["pnl"] for t in closed)
        total_pnl_pct = total_pnl / self.initial_balance * 100

        cumulative = np.cumsum([t["pnl"] for t in closed])
        peak = np.maximum.accumulate(cumulative)
        max_dd = float(np.max(peak - cumulative)) / self.initial_balance * 100 if len(cumulative) > 0 else 0

        returns = [t["pnl"] / self.initial_balance for t in closed]
        sharpe = (np.mean(returns) / np.std(returns) * np.sqrt(252)) if np.std(returns) > 0 else 0

        consec = max_consec = 0
        for t in closed:
            consec = consec + 1 if t["outcome"] == "loss" else 0
            max_consec = max(max_consec, consec)

        return {
            "symbol": symbol,
            "total_trades": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl_pct, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "sharpe_ratio": round(sharpe, 2),
            "max_consecutive_losses": max_consec,
            "avg_rr": round(avg_win / avg_loss, 2) if avg_loss > 0 else 0,
        }

    def run_full_backtest(self, days: int = 365) -> Dict:
        all_results = {}
        all_trades = []
        logger.info(f"🚀 Backtest MK2 — {days} giorni su {len(FOREX_PAIRS)} coppie")

        for symbol in FOREX_PAIRS:
            logger.info(f"  Analisi {symbol}...")
            df_h1 = self.price_collector.get_ohlcv_yahoo(symbol, "1h", days=days)
            df_h4 = self.price_collector.get_ohlcv_yahoo(symbol, "4h", days=days + 90)

            if df_h1 is None or len(df_h1) < 100:
                logger.warning(f"  {symbol}: dati insufficienti")
                continue

            trades = self.run_backtest(symbol, df_h1, df_h4)
            metrics = self.calculate_metrics(trades, symbol)
            all_results[symbol] = metrics
            all_trades.extend(trades)
            logger.info(f"  {symbol}: {metrics.get('total_trades', 0)} trade | WR: {metrics.get('win_rate', 0):.1f}% | PnL: {metrics.get('total_pnl_pct', 0):+.1f}% | DD: {metrics.get('max_drawdown_pct', 0):.1f}%")

        if all_trades:
            all_results["AGGREGATE"] = self.calculate_metrics(
                [t for t in all_trades if t.get("outcome") in ("win", "loss")], "ALL_PAIRS"
            )

        return all_results


def print_report_mk2(results: Dict):
    print("\n" + "="*65)
    print("  PALANTIR MK2 — BACKTESTING REPORT")
    print("="*65)
    for symbol, metrics in results.items():
        if metrics.get("total_trades", 0) == 0:
            continue
        ok = lambda v, t, gt=True: "✅" if (v >= t if gt else v <= t) else "❌"
        print(f"\n  {'─'*60}")
        print(f"  {symbol}")
        print(f"  {'─'*60}")
        print(f"  Trade totali:        {metrics.get('total_trades', 0)}")
        print(f"  Win Rate:            {metrics.get('win_rate', 0):.1f}%  {ok(metrics.get('win_rate', 0), 55)}")
        print(f"  Profit Factor:       {metrics.get('profit_factor', 0):.2f}")
        print(f"  Total P&L:           {metrics.get('total_pnl_pct', 0):+.1f}%")
        print(f"  Max Drawdown:        {metrics.get('max_drawdown_pct', 0):.1f}%  {ok(metrics.get('max_drawdown_pct', 0), 8, gt=False)}")
        print(f"  Sharpe Ratio:        {metrics.get('sharpe_ratio', 0):.2f}  {ok(metrics.get('sharpe_ratio', 0), 1.2)}")
        print(f"  Avg R:R:             1:{metrics.get('avg_rr', 0):.1f}  {ok(metrics.get('avg_rr', 0), 1.8)}")
        print(f"  Max Consec. Losses:  {metrics.get('max_consecutive_losses', 0)}")
    print("\n" + "="*65)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] — %(message)s")
    engine = BacktestEngineMK2(initial_balance=10000)
    print("🚀 Backtest MK2 su 12 mesi — attendere...\n")
    results = engine.run_full_backtest(days=365)
    with open("backtest_mk2_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print_report_mk2(results)
    print("\n✅ Risultati salvati in backtest_mk2_results.json")
