import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from datetime import datetime, timezone, time
from typing import Dict, Optional
import logging

logger = logging.getLogger(__name__)

# Sessioni di mercato in UTC
SESSIONS = {
    "asia":     {"start": time(0, 0),  "end": time(9, 0)},
    "europe":   {"start": time(7, 0),  "end": time(16, 0)},
    "us":       {"start": time(13, 0), "end": time(22, 0)},
    "overlap":  {"start": time(13, 0), "end": time(16, 0)},  # EU + US overlap
}

# Quali coppie funzionano meglio in quale sessione
SESSION_PAIR_AFFINITY = {
    "EUR/USD": ["europe", "overlap"],
    "GBP/USD": ["europe", "overlap"],
    "USD/JPY": ["asia", "overlap"],
    "AUD/USD": ["asia", "europe"],
    "USD/CAD": ["us", "overlap"],
    "EUR/GBP": ["europe"],
}

# Orari con edge storico dimostrato (apertura sessioni)
HIGH_EDGE_HOURS_UTC = [8, 9, 13, 14]  # Apertura Londra e NY

# Orari da evitare (bassa liquidità)
LOW_LIQUIDITY_HOURS_UTC = [0, 1, 2, 3, 4, 5, 6, 21, 22, 23]


class SessionFilter:
    """
    Filtra i segnali in base alla sessione di mercato e all'orario.
    
    Concetto chiave: un setup perfetto al momento sbagliato è una perdita.
    L'apertura di Londra (08:00 UTC) è il momento con più liquidità istituzionale.
    L'overlap EU/US (13:00-16:00 UTC) è il momento con i movimenti più grandi.
    """

    def get_active_sessions(self, dt: datetime = None) -> Dict:
        """Ritorna le sessioni attive in questo momento"""
        if dt is None:
            dt = datetime.now(timezone.utc)

        current_time = dt.time()
        active = {}

        for session_name, session_times in SESSIONS.items():
            start = session_times["start"]
            end = session_times["end"]
            is_active = start <= current_time <= end
            active[session_name] = is_active

        return active

    def get_session_quality(self, symbol: str, dt: datetime = None) -> Dict:
        """
        Calcola la qualità della sessione per una coppia specifica.
        Score da 0 a 100: 100 = momento ottimale, 0 = evitare.
        """
        if dt is None:
            dt = datetime.now(timezone.utc)

        current_hour = dt.hour
        current_time = dt.time()
        weekday = dt.weekday()

        # Weekend — no trading
        if weekday >= 5:
            return {
                "score": 0,
                "tradeable": False,
                "reason": "Weekend — mercato chiuso",
                "session": "closed",
            }

        # Venerdì dopo le 20:00 UTC — evita gap weekend
        if weekday == 4 and current_hour >= 20:
            return {
                "score": 0,
                "tradeable": False,
                "reason": "Venerdì sera — rischio gap weekend",
                "session": "pre_weekend",
            }

        active_sessions = self.get_active_sessions(dt)

        # Calcola score base dalla sessione
        score = 0
        session_name = "none"
        preferred_sessions = SESSION_PAIR_AFFINITY.get(symbol, ["europe", "overlap"])

        if active_sessions.get("overlap"):
            score = 100
            session_name = "overlap"
        elif active_sessions.get("europe") and "europe" in preferred_sessions:
            score = 80
            session_name = "europe"
        elif active_sessions.get("us") and "us" in preferred_sessions:
            score = 75
            session_name = "us"
        elif active_sessions.get("asia") and "asia" in preferred_sessions:
            score = 60
            session_name = "asia"
        elif active_sessions.get("europe"):
            score = 50
            session_name = "europe_generic"
        else:
            score = 20
            session_name = "off_hours"

        # Bonus per orari ad alto edge (apertura sessioni)
        if current_hour in HIGH_EDGE_HOURS_UTC:
            score = min(100, score + 15)

        # Penalità per bassa liquidità
        if current_hour in LOW_LIQUIDITY_HOURS_UTC:
            score = max(0, score - 40)

        tradeable = score >= 50

        return {
            "score": score,
            "tradeable": tradeable,
            "session": session_name,
            "active_sessions": [k for k, v in active_sessions.items() if v],
            "reason": f"Sessione {session_name} — score {score}/100",
            "high_edge_hour": current_hour in HIGH_EDGE_HOURS_UTC,
        }

    def should_trade(self, symbol: str, dt: datetime = None) -> bool:
        """Risposta semplice: si o no per tradare questa coppia adesso"""
        quality = self.get_session_quality(symbol, dt)
        return quality["tradeable"]

    def get_all_pairs_session_scores(self, pairs: list, dt: datetime = None) -> Dict:
        """Score sessione per tutte le coppie"""
        results = {}
        for pair in pairs:
            results[pair] = self.get_session_quality(pair, dt)
        return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from config.settings import FOREX_PAIRS

    sf = SessionFilter()
    now = datetime.now(timezone.utc)
    print(f"\n⏰ Session Analysis — {now.strftime('%H:%M UTC')}")

    for pair in FOREX_PAIRS:
        quality = sf.get_session_quality(pair)
        status = "✅" if quality["tradeable"] else "❌"
        print(f"  {status} {pair}: score={quality['score']} | {quality['reason']}")
