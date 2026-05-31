import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime


class TestPriceDataCollector:

    def test_get_ohlcv_yahoo_returns_dataframe(self):
        from core.data_collector.price_data import PriceDataCollector
        collector = PriceDataCollector()
        df = collector.get_ohlcv_yahoo("EUR/USD", "1h", days=5)
        assert df is not None, "DataFrame non deve essere None"
        assert len(df) > 0, "DataFrame non deve essere vuoto"
        assert "open" in df.columns
        assert "high" in df.columns
        assert "low" in df.columns
        assert "close" in df.columns

    def test_invalid_symbol_returns_none(self):
        from core.data_collector.price_data import PriceDataCollector
        collector = PriceDataCollector()
        df = collector.get_ohlcv_yahoo("INVALID/PAIR", "1h", days=5)
        assert df is None, "Simbolo invalido deve ritornare None"

    def test_all_pairs_returns_dict(self):
        from core.data_collector.price_data import PriceDataCollector
        collector = PriceDataCollector()
        result = collector.get_all_pairs("1h", days=3)
        assert isinstance(result, dict)
        assert len(result) > 0, "Almeno una coppia deve essere scaricata"


class TestNewsCollector:

    def test_rss_news_returns_list(self):
        from core.data_collector.news_collector import NewsCollector
        collector = NewsCollector()
        news = collector.get_rss_news(max_per_feed=5)
        assert isinstance(news, list)

    def test_news_has_required_fields(self):
        from core.data_collector.news_collector import NewsCollector
        collector = NewsCollector()
        news = collector.get_rss_news(max_per_feed=3)
        if news:
            item = news[0]
            assert "title" in item
            assert "url" in item
            assert "source" in item

    def test_forex_relevance_filter(self):
        from core.data_collector.news_collector import NewsCollector
        collector = NewsCollector()
        assert collector._is_forex_relevant("EUR/USD rally as Fed signals rate cut")
        assert not collector._is_forex_relevant("Local bakery opens new branch in town")


class TestMacroDataCollector:

    def test_fred_series_returns_data(self):
        from core.data_collector.macro_data import MacroDataCollector
        collector = MacroDataCollector()
        data = collector.get_fred_series("FEDFUNDS", periods=3)
        assert data is not None
        assert "latest_value" in data
        assert "latest_date" in data

    def test_calendar_returns_list(self):
        from core.data_collector.macro_data import MacroDataCollector
        collector = MacroDataCollector()
        events = collector.get_forex_factory_calendar()
        assert isinstance(events, list)

    def test_news_blackout_returns_dict(self):
        from core.data_collector.macro_data import MacroDataCollector
        collector = MacroDataCollector()
        blackout = collector.is_news_blackout()
        assert isinstance(blackout, dict)


class TestBaseCollector:

    def test_circuit_breaker_disables_after_failures(self):
        from core.data_collector.base_collector import CircuitBreaker
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=1)
        assert cb.is_available("test_source")
        cb.record_failure("test_source")
        cb.record_failure("test_source")
        cb.record_failure("test_source")
        assert not cb.is_available("test_source")

    def test_circuit_breaker_resets_on_success(self):
        from core.data_collector.base_collector import CircuitBreaker
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure("test_source")
        cb.record_success("test_source")
        assert cb.failures.get("test_source", 0) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
