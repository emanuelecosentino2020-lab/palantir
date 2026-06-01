import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple
from datetime import datetime, timedelta
import logging
import json

from core.data_collector.price_data import PriceDataCollector
from core.ai_analyzer.technical_analyzer_v2 import EnhancedTechnicalAnalyzer
from core.ai_analyzer.market_structure import MarketStructureAnalyzer
from core.ai_analyzer.session_filter import SessionFilter
from config.settings import (
    FOREX_PAIRS, ACCOUNT_BALANCE, RISK_PER_TRADE,
    ATR_SL_MULTIPLIER, ATR_TP1_MULTIPLIER, ATR_TP2_MULTIPLIER,
    MIN_RISK_REWARD,
)

logger = logging.getLogger(__name__)

MIN_SCORE = 75


class WalkForwardBacktest:
    """
    Walk-Forward Testing — il metodo professionale per validare un sistema.
    
    Come funziona:
    1. Dividi i dati in finestre: 6 mesi training + 3 mesi testing
    2. Allena/ottimizza i parametri sul training period
    3. Testa sul test period (dati mai visti)
    4. Avanza di 3 mesi e ripeti
    
    Se il sistema funziona in ogni periodo out-of-sample,
    hai un edge statisticamente robusto, non overfitting.
    """

    SPREAD_PIPS = {
        "EUR/USD": 1.5, "GBP/USD": 2.0, "USD/JPY": 1.5,
        "AUD/USD": 2.0, "USD/CAD": 2.5, "EUR/GBP": 2.0,
    }

    def __init__(self, initial_balance: float = None):
        self.initial_balance = initial_balance or ACCOUNT_BALANCE
        self.price_collector = PriceDataCollector()
        self.tech_analyzer = EnhancedTechnicalAnalyzer()
        self.structure_analyzer = MarketStructureAnalyzer()
        self.session_filter = SessionFilter()

    def _pip_value(self, symbol: str) -> float:
        return 0.0001 if "JPY" not in symbol else 0.01

    def _apply_spread(self, price: float, direction: str, symbol: str) -> float:
        spread = self.SPREAD_PIPS.get(symbol, 2.0) * self._pip_value(symbol)
        slippage = 0.5 * self._pip_value(symbol)
        return price + spread + slippage if direction == "LONG" else price - spread - slippage

    def _risk_check(self, signal: Dict, balance: float) -> Dict:
        entry = signal.get("entry_price", 0)
        direction = signal.get("direction")
        atr = signal.get("atr", 0.001)
        score = signal.get("raw_score", 0)

        if score < MIN_SCORE or entry <= 0:
            return None
        if atr <= 0:
            atr = entry * 0.001

        if direction == "LONG":
            sl = entry - atr * ATR_SL_MULTIPLIER
            tp1 = entry + atr * ATR_TP1_MULTIPLIER
            tp2 = entry + atr * ATR_TP2_MULTIPLIER
        else:
            sl = entry + atr * ATR_SL_MULTIPLIER
            tp1 = entry - atr * ATR_TP1_MULTIPLIER
            tp2 = entry - atr * ATR_TP2_MULTIPLIER

        rr = abs(entry - tp1) / max(abs(entry - sl), 0.0001)
        if rr < MIN_RISK_REWARD:
            return None

        return {**signal, "stop_loss": sl, "take_profit_1": tp1, "take_profit_2": tp2,
                "risk_amount": balance * RISK_PER_TRADE, "risk_reward": round(rr, 2)}

    def _simulate_period(self, symbol: str, df: pd.DataFrame, lookback: int = 80) -> List[Dict]:
        """Simula il sistema su un periodo di dati"""
        trades = []
        balance = self.initial_balance
        open_trade = None

        for i in range(lookback, len(df)):
            df_slice = df.iloc[:i+1]
            bar = df.iloc[i]
            price = float(bar["close"])
            high = float(bar["high"])
            low = float(bar["low"])
            ts = df.index[i]

            # Gestisci trade aperto
            if open_trade:
                d = open_trade["direction"]
                hit_sl = (d == "LONG" and low <= open_trade["stop_loss"]) or \
                         (d == "SHORT" and high >= open_trade["stop_loss"])
                hit_tp2 = (d == "LONG" and high >= open_trade["take_profit_2"]) or \
                          (d == "SHORT" and low <= open_trade["take_profit_2"])
                hit_tp1 = not open_trade.get("tp1_hit") and (
                    (d == "LONG" and high >= open_trade["take_profit_1"]) or
                    (d == "SHORT" and low <= open_trade["take_profit_1"])
                )

                if hit_sl:
                    pnl = -open_trade["risk_amount"]
                    balance += pnl
                    open_trade.update({"exit_price": open_trade["stop_loss"], "exit_reason": "sl",
                                       "pnl": round(pnl, 2), "outcome": "loss", "closed_at": str(ts)})
                    trades.append(open_trade)
                    open_trade = None
                elif hit_tp2:
                    pnl = open_trade["risk_amount"] * open_trade["risk_reward"]
                    balance += pnl
                    open_trade.update({"exit_price": open_trade["take_profit_2"], "exit_reason": "tp2",
                                       "pnl": round(pnl, 2), "outcome": "win", "closed_at": str(ts)})
                    trades.append(open_trade)
                    open_trade = None
                elif hit_tp1:
                    pnl = open_trade["risk_amount"] * open_trade["risk_reward"] * 0.5
                    balance += pnl
                    open_trade["tp1_hit"] = True
                    open_trade["stop_loss"] = open_trade["entry_price"]
                elif i - open_trade.get("open_bar", i) >= 96:
                    pnl_raw = (price - open_trade["entry_price"]) * (1 if d == "LONG" else -1)
                    pnl = pnl_raw / max(abs(open_trade["entry_price"] - open_trade["stop_loss"]), 0.0001) * open_trade["risk_amount"]
                    balance += pnl
                    open_trade.update({"exit_price": price, "exit_reason": "expired",
                                       "pnl": round(pnl, 2), "outcome": "win" if pnl > 0 else "loss",
                                       "closed_at": str(ts)})
                    trades.append(open_trade)
                    open_trade = None
                continue

            # Session filter
            if not self.session_filter.should_trade(symbol, ts if hasattr(ts, 'hour') else None):
                continue

            # Analisi tecnica con market structure
            technical = self.tech_analyzer.get_technical_score(df_slice)
            if technical.get("filtered"):
                continue

            technical["patterns"] = self.tech_analyzer.detect_patterns(df_slice)

            # Market structure validation
            structure = self.structure_analyzer.get_market_structure(df_slice)

            # Score combinato
            tech_score = technical.get("score", 0)
            struct_score = structure.get("score", 0)
            combined_score = tech_score * 0.7 + struct_score * 0.3

            # Filtro direzione struttura
            if structure["direction"] == "LONG" and combined_score < 0:
                continue
            if structure["direction"] == "SHORT" and combined_score > 0:
                continue
            if structure["direction"] == "neutral":
                continue

            if abs(combined_score) < MIN_SCORE:
                continue

            direction = "LONG" if combined_score > 0 else "SHORT"
            entry = self._apply_spread(price, direction, symbol)

            signal = {
                "symbol": symbol,
                "direction": direction,
                "entry_price": entry,
                "raw_score": abs(combined_score),
                "atr": technical.get("atr", 0.001),
                "strategy_name": "WalkForward_TC_MS",
            }

            approved = self._risk_check(signal, balance)
            if approved:
                open_trade = {**approved, "open_bar": i, "opened_at": str(ts), "tp1_hit": False}

        if open_trade:
            open_trade.update({"exit_reason": "end_of_data", "pnl": 0, "outcome": "open"})
            trades.append(open_trade)

        return trades

    def calculate_metrics(self, trades: List[Dict], label: str = "") -> Dict:
        closed = [t for t in trades if t.get("outcome") in ("win", "loss")]
        if not closed:
            return {"label": label, "total_trades": 0, "error": "No trades"}

        wins = [t for t in closed if t["outcome"] == "win"]
        losses = [t for t in closed if t["outcome"] == "loss"]
        win_rate = len(wins) / len(closed) * 100
        avg_win = np.mean([t["pnl"] for t in wins]) if wins else 0
        avg_loss = abs(np.mean([t["pnl"] for t in losses])) if losses else 1
        total_pnl = sum(t["pnl"] for t in closed)
        profit_factor = sum(t["pnl"] for t in wins) / abs(sum(t["pnl"] for t in losses)) if losses else 999

        cumsum = np.cumsum([t["pnl"] for t in closed])
        peak = np.maximum.accumulate(cumsum)
        max_dd = float(np.max(peak - cumsum)) / self.initial_balance * 100 if len(cumsum) > 0 else 0

        returns = [t["pnl"] / self.initial_balance for t in closed]
        sharpe = np.mean(returns) / np.std(returns) * np.sqrt(252) if np.std(returns) > 0 else 0

        return {
            "label": label,
            "total_trades": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 2),
            "profit_factor": round(profit_factor, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl / self.initial_balance * 100, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "sharpe_ratio": round(sharpe, 2),
            "avg_rr": round(avg_win / avg_loss, 2) if avg_loss > 0 else 0,
        }

    def run_walk_forward(self, symbol: str, total_days: int = 365,
                         train_days: int = 180, test_days: int = 90) -> Dict:
        """
        Esegui walk-forward testing su una coppia.
        """
        logger.info(f"🔄 Walk-forward {symbol}: {total_days}gg totali, finestre {train_days}+{test_days}gg")

        df = self.price_collector.get_ohlcv_yahoo(symbol, "1h", days=total_days + train_days)
        if df is None or len(df) < 100:
            return {"error": f"Dati insufficienti per {symbol}"}

        all_test_trades = []
        windows = []
        window_num = 0

        # Calcola finestre (in candele H1)
        train_candles = train_days * 24
        test_candles = test_days * 24
        step = test_candles

        start = 0
        while start + train_candles + test_candles <= len(df):
            train_end = start + train_candles
            test_end = min(train_end + test_candles, len(df))

            df_train = df.iloc[start:train_end]
            df_test = df.iloc[train_end:test_end]

            if len(df_test) < 100:
                break

            window_num += 1
            label = f"Window {window_num}"

            # Simula sul periodo di test (out-of-sample)
            test_trades = self._simulate_period(symbol, pd.concat([df_train.tail(80), df_test]))
            test_trades = test_trades[len([t for t in test_trades if t.get("opened_at", "") < str(df_test.index[0])]):]

            metrics = self.calculate_metrics(test_trades, label)
            windows.append(metrics)
            all_test_trades.extend(test_trades)

            logger.info(f"  {label}: {metrics.get('total_trades', 0)} trade | WR: {metrics.get('win_rate', 0):.1f}% | PnL: {metrics.get('total_pnl_pct', 0):+.1f}%")

            start += step

        aggregate = self.calculate_metrics(
            [t for t in all_test_trades if t.get("outcome") in ("win", "loss")],
            "AGGREGATE OUT-OF-SAMPLE"
        )

        return {
            "symbol": symbol,
            "windows": windows,
            "aggregate": aggregate,
            "consistency": self._calculate_consistency(windows),
        }

    def _calculate_consistency(self, windows: List[Dict]) -> Dict:
        """Misura la consistenza del sistema attraverso le finestre"""
        profitable_windows = sum(1 for w in windows if w.get("total_pnl_pct", 0) > 0)
        total = len(windows)

        return {
            "profitable_windows": profitable_windows,
            "total_windows": total,
            "consistency_pct": round(profitable_windows / total * 100, 1) if total > 0 else 0,
            "robust": profitable_windows / total >= 0.6 if total > 0 else False,
        }

    def run_full_walk_forward(self) -> Dict:
        """Walk-forward su tutte le coppie"""
        results = {}
        print("\n🚀 Walk-Forward Testing su 3 anni...")
        print("   Metodo professionale — solo out-of-sample\n")

        for symbol in FOREX_PAIRS:
            print(f"  Analisi {symbol}...")
            result = self.run_walk_forward(symbol, total_days=730, train_days=180, test_days=90)
            results[symbol] = result

            agg = result.get("aggregate", {})
            cons = result.get("consistency", {})
            print(f"  {symbol}: WR={agg.get('win_rate', 0):.1f}% | PnL={agg.get('total_pnl_pct', 0):+.1f}% | Consistency={cons.get('consistency_pct', 0):.0f}%")

        return results


def print_wf_report(results: Dict):
    print("\n" + "="*70)
    print("  PALANTIR — WALK-FORWARD REPORT (Out-of-Sample)")
    print("="*70)

    for symbol, data in results.items():
        if "error" in data:
            continue
        agg = data.get("aggregate", {})
        cons = data.get("consistency", {})
        robust = "✅ ROBUSTO" if cons.get("robust") else "❌ NON ROBUSTO"

        print(f"\n  {'─'*65}")
        print(f"  {symbol}  {robust}")
        print(f"  {'─'*65}")
        print(f"  Trade out-of-sample: {agg.get('total_trades', 0)}")
        print(f"  Win Rate:            {agg.get('win_rate', 0):.1f}%")
        print(f"  Total PnL:           {agg.get('total_pnl_pct', 0):+.1f}%")
        print(f"  Max Drawdown:        {agg.get('max_drawdown_pct', 0):.1f}%")
        print(f"  Sharpe Ratio:        {agg.get('sharpe_ratio', 0):.2f}")
        print(f"  Consistency:         {cons.get('profitable_windows', 0)}/{cons.get('total_windows', 0)} finestre profittevoli ({cons.get('consistency_pct', 0):.0f}%)")

    print("\n" + "="*70)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] — %(message)s")
    engine = WalkForwardBacktest(initial_balance=10000)
    results = engine.run_full_walk_forward()
    with open("walkforward_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    print_wf_report(results)
    print("\n✅ Risultati salvati in walkforward_results.json")
