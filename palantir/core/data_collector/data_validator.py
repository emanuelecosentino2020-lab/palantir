import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pandas as pd
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Any
import logging

logger = logging.getLogger(__name__)

# Ultima lettura valida per ogni fonte — fallback se la fonte smette di funzionare
_last_valid_data: Dict[str, Any] = {}


class DataValidator:
    """
    Controlla qualità dei dati in ingresso.
    Se un collector fallisce, usa l'ultimo dato valido invece di crashare.
    """

    def validate_and_cache(self, key: str, data: Any) -> Any:
        """
        Valida i dati e li mette in cache.
        Se i dati sono None/invalidi, ritorna l'ultimo dato valido.
        """
        if data is not None and self._is_valid(data):
            _last_valid_data[key] = {
                "data": data,
                "timestamp": datetime.now(timezone.utc),
            }
            return data

        # Fallback all'ultimo dato valido
        if key in _last_valid_data:
            cached = _last_valid_data[key]
            age_minutes = (datetime.now(timezone.utc) - cached["timestamp"]).seconds / 60
            logger.warning(f"⚠️ {key}: dato invalido, uso cache di {age_minutes:.0f} min fa")
            return cached["data"]

        logger.error(f"❌ {key}: nessun dato valido disponibile")
        return None

    def _is_valid(self, data: Any) -> bool:
        """Controlla se il dato è valido"""
        if data is None:
            return False
        if isinstance(data, pd.DataFrame):
            return not data.empty and len(data) > 0
        if isinstance(data, list):
            return len(data) > 0
        if isinstance(data, dict):
            return len(data) > 0
        return True

    def check_data_freshness(self, key: str, max_age_minutes: int = 30) -> bool:
        """Verifica che i dati non siano troppo vecchi"""
        if key not in _last_valid_data:
            return False
        cached = _last_valid_data[key]
        age = (datetime.now(timezone.utc) - cached["timestamp"]).seconds / 60
        if age > max_age_minutes:
            logger.warning(f"⚠️ {key}: dati vecchi di {age:.0f} min (limite: {max_age_minutes} min)")
            return False
        return True

    def get_system_health(self) -> Dict:
        """Ritorna lo stato di salute di tutti i data feed"""
        health = {}
        now = datetime.now(timezone.utc)
        for key, cached in _last_valid_data.items():
            age_minutes = (now - cached["timestamp"]).seconds / 60
            health[key] = {
                "status": "ok" if age_minutes < 30 else "stale",
                "age_minutes": round(age_minutes, 1),
                "last_update": cached["timestamp"].isoformat(),
            }
        return health


# Istanza globale
validator = DataValidator()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    v = DataValidator()

    # Test fallback
    v.validate_and_cache("test_feed", {"price": 1.1})
    result = v.validate_and_cache("test_feed", None)  # Simula fallimento
    print(f"Fallback funziona: {result is not None}")

    health = v.get_system_health()
    print(f"Health: {health}")
