import time
import logging
import functools
from datetime import datetime, timedelta
from typing import Optional, Any
import httpx
import asyncio

logger = logging.getLogger(__name__)


def rate_limit(calls_per_minute: int = 10):
    """Decorator per limitare le chiamate API e non bruciare le quote"""
    min_interval = 60.0 / calls_per_minute
    last_called = [0.0]

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            elapsed = time.time() - last_called[0]
            wait_time = min_interval - elapsed
            if wait_time > 0:
                time.sleep(wait_time)
            last_called[0] = time.time()
            return func(*args, **kwargs)
        return wrapper
    return decorator


def retry(max_attempts: int = 3, delay: float = 2.0, backoff: float = 2.0):
    """Decorator retry con backoff esponenziale"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            attempt = 0
            current_delay = delay
            while attempt < max_attempts:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    attempt += 1
                    if attempt == max_attempts:
                        logger.error(f"❌ {func.__name__} fallito dopo {max_attempts} tentativi: {e}")
                        return None
                    logger.warning(f"⚠️ {func.__name__} tentativo {attempt} fallito: {e}. Retry in {current_delay}s")
                    time.sleep(current_delay)
                    current_delay *= backoff
        return wrapper
    return decorator


class CircuitBreaker:
    """Disabilita automaticamente una fonte che fallisce ripetutamente"""

    def __init__(self, failure_threshold: int = 3, recovery_timeout: int = 1800):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout  # 30 minuti
        self.failures = {}
        self.disabled_until = {}

    def is_available(self, source_name: str) -> bool:
        if source_name in self.disabled_until:
            if datetime.utcnow() < self.disabled_until[source_name]:
                return False
            else:
                # Recovery: riabilita la fonte
                del self.disabled_until[source_name]
                self.failures[source_name] = 0
                logger.info(f"✅ Fonte {source_name} riabilitata")
        return True

    def record_failure(self, source_name: str):
        self.failures[source_name] = self.failures.get(source_name, 0) + 1
        if self.failures[source_name] >= self.failure_threshold:
            self.disabled_until[source_name] = datetime.utcnow() + timedelta(seconds=self.recovery_timeout)
            logger.warning(f"⛔ Fonte {source_name} disabilitata per 30 minuti (troppi errori)")

    def record_success(self, source_name: str):
        self.failures[source_name] = 0


# Istanza globale del circuit breaker
circuit_breaker = CircuitBreaker()


class BaseCollector:
    """Classe base per tutti i data collector"""

    def __init__(self, source_name: str):
        self.source_name = source_name
        self.logger = logging.getLogger(f"collector.{source_name}")
        self.client = httpx.Client(timeout=30.0, headers={"User-Agent": "PalantirBot/1.0"})

    def is_available(self) -> bool:
        return circuit_breaker.is_available(self.source_name)

    def record_success(self):
        circuit_breaker.record_success(self.source_name)

    def record_failure(self):
        circuit_breaker.record_failure(self.source_name)

    def get(self, url: str, params: dict = None, headers: dict = None) -> Optional[Any]:
        """Esegue una GET con gestione errori automatica"""
        if not self.is_available():
            self.logger.warning(f"Fonte {self.source_name} temporaneamente disabilitata")
            return None
        try:
            response = self.client.get(url, params=params, headers=headers)
            response.raise_for_status()
            self.record_success()
            return response.json()
        except httpx.HTTPStatusError as e:
            self.logger.error(f"HTTP error {e.response.status_code}: {url}")
            self.record_failure()
            return None
        except Exception as e:
            self.logger.error(f"Errore richiesta: {e}")
            self.record_failure()
            return None

    def close(self):
        self.client.close()
