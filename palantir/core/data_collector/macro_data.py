import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import requests
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import logging
from bs4 import BeautifulSoup

from core.data_collector.base_collector import BaseCollector, retry, rate_limit

logger = logging.getLogger(__name__)

# Serie FRED più importanti per il forex
FRED_SERIES = {
    "US_CPI": "CPIAUCSL",           # Inflazione USA
    "US_CORE_CPI": "CPILFESL",      # Core CPI USA
    "US_NFP": "PAYEMS",             # Non-Farm Payrolls
    "US_UNEMPLOYMENT": "UNRATE",    # Tasso disoccupazione USA
    "US_GDP": "GDP",                # PIL USA
    "US_RATE": "FEDFUNDS",          # Fed Funds Rate
    "US_10Y_YIELD": "DGS10",        # Treasury 10Y
    "EU_RATE": "ECBDFR",            # BCE Deposit Rate
    "EU_CPI": "CP0000EZ19M086NEST", # Inflazione Eurozona
}


class MacroDataCollector(BaseCollector):

    def __init__(self):
        super().__init__("macro_data")
        self.fred_base = "https://fred.stlouisfed.org/graph/fredgraph.csv"

    @retry(max_attempts=3, delay=2.0)
    @rate_limit(calls_per_minute=5)
    def get_fred_series(self, series_id: str, periods: int = 12) -> Optional[Dict]:
        """
        Scarica una serie FRED — completamente gratuito, nessuna API key necessaria
        """
        try:
            url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
            response = requests.get(url, timeout=15)
            response.raise_for_status()

            lines = response.text.strip().split("\n")
            data_points = []
            for line in lines[1:]:  # Skip header
                parts = line.split(",")
                if len(parts) >= 2 and parts[1].strip() != ".":
                    try:
                        data_points.append({
                            "date": parts[0].strip(),
                            "value": float(parts[1].strip()),
                        })
                    except ValueError:
                        continue

            if not data_points:
                return None

            # Prendi solo gli ultimi N periodi
            recent = data_points[-periods:]
            latest = recent[-1]
            previous = recent[-2] if len(recent) >= 2 else None

            self.record_success()
            return {
                "series_id": series_id,
                "latest_value": latest["value"],
                "latest_date": latest["date"],
                "previous_value": previous["value"] if previous else None,
                "change": latest["value"] - previous["value"] if previous else None,
                "history": recent,
            }

        except Exception as e:
            logger.error(f"Errore FRED {series_id}: {e}")
            self.record_failure()
            return None

    def get_all_macro_data(self) -> Dict:
        """Scarica tutti i dati macro principali da FRED"""
        macro_data = {}
        for name, series_id in FRED_SERIES.items():
            data = self.get_fred_series(series_id)
            if data:
                macro_data[name] = data
                logger.info(f"✅ {name}: {data['latest_value']} ({data['latest_date']})")
        return macro_data

    @retry(max_attempts=3, delay=2.0)
    def get_forex_factory_calendar(self, days_ahead: int = 7) -> List[Dict]:
        """
        Scraping del calendario ForexFactory — fonte primaria per eventi HIGH impact
        """
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml",
            }
            response = requests.get("https://www.forexfactory.com/calendar", headers=headers, timeout=15)

            if response.status_code != 200:
                logger.warning(f"ForexFactory ha risposto con status {response.status_code}")
                return self._get_mock_calendar()

            soup = BeautifulSoup(response.text, "html.parser")
            events = []

            # Parsing della tabella del calendario
            rows = soup.find_all("tr", class_="calendar__row")
            for row in rows:
                try:
                    impact_cell = row.find("td", class_="calendar__impact")
                    if not impact_cell:
                        continue

                    impact_span = impact_cell.find("span")
                    if not impact_span:
                        continue

                    impact_class = impact_span.get("class", [])
                    if "high" in " ".join(impact_class).lower():
                        impact = "high"
                    elif "medium" in " ".join(impact_class).lower():
                        impact = "medium"
                    else:
                        impact = "low"

                    currency_cell = row.find("td", class_="calendar__currency")
                    event_cell = row.find("td", class_="calendar__event")

                    if currency_cell and event_cell:
                        events.append({
                            "name": event_cell.get_text(strip=True),
                            "currency": currency_cell.get_text(strip=True),
                            "impact": impact,
                            "scheduled_at": datetime.utcnow(),  # Semplificato
                        })
                except Exception:
                    continue

            if events:
                logger.info(f"✅ ForexFactory: {len(events)} eventi trovati")
                return events
            else:
                return self._get_mock_calendar()

        except Exception as e:
            logger.error(f"Errore ForexFactory scraping: {e}")
            return self._get_mock_calendar()

    def _get_mock_calendar(self) -> List[Dict]:
        """
        Calendario mock per quando ForexFactory non è accessibile.
        Contiene gli eventi HIGH impact tipici della settimana.
        """
        logger.info("ℹ️ Uso calendario mock (ForexFactory non accessibile)")
        now = datetime.utcnow()
        return [
            {"name": "US CPI", "currency": "USD", "impact": "high",
             "scheduled_at": now.replace(hour=12, minute=30)},
            {"name": "ECB Rate Decision", "currency": "EUR", "impact": "high",
             "scheduled_at": now.replace(hour=11, minute=45)},
            {"name": "UK Employment Change", "currency": "GBP", "impact": "high",
             "scheduled_at": now.replace(hour=6, minute=0)},
        ]

    def is_news_blackout(self, minutes_buffer: int = 15) -> Dict[str, bool]:
        """
        Verifica se siamo in una finestra di blackout (vicini a un evento HIGH impact).
        Ritorna un dict {currency: True/False}
        """
        blackout = {}
        events = self.get_forex_factory_calendar()
        now = datetime.utcnow()

        for event in events:
            if event["impact"] != "high":
                continue
            event_time = event.get("scheduled_at", now)
            time_diff = abs((event_time - now).total_seconds() / 60)

            if time_diff <= minutes_buffer:
                currency = event["currency"]
                blackout[currency] = True
                logger.warning(f"⚠️ BLACKOUT ATTIVO: {event['name']} ({currency}) in {time_diff:.0f} min")

        return blackout


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    collector = MacroDataCollector()

    print("\n📊 Test FRED — Fed Funds Rate:")
    data = collector.get_fred_series("FEDFUNDS", periods=3)
    if data:
        print(f"  Valore attuale: {data['latest_value']}%")
        print(f"  Data: {data['latest_date']}")

    print("\n📅 Calendario macro:")
    events = collector.get_forex_factory_calendar()
    for e in events[:3]:
        print(f"  [{e['impact'].upper()}] {e['currency']}: {e['name']}")
