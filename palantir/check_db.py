import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from database.models import SessionLocal, Price, News, MacroEvent, Signal, Trade
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def check_db():
    db = SessionLocal()
    try:
        print("\n" + "="*50)
        print("  PALANTIR — DB HEALTH CHECK")
        print("="*50)

        tables = [
            ("Price", Price),
            ("News", News),
            ("MacroEvent", MacroEvent),
            ("Signal", Signal),
            ("Trade", Trade),
        ]

        for name, Model in tables:
            count = db.query(Model).count()
            print(f"  {name:<15} {count:>6} righe")

        # Ultima news
        last_news = db.query(News).order_by(News.created_at.desc()).first()
        if last_news:
            print(f"\n  Ultima news: {last_news.title[:50]}...")
            print(f"  Aggiornato: {last_news.created_at}")

        # Ultimo prezzo
        last_price = db.query(Price).order_by(Price.timestamp.desc()).first()
        if last_price:
            print(f"\n  Ultimo prezzo: {last_price.symbol} @ {last_price.close}")
            print(f"  Timestamp: {last_price.timestamp}")

        print("="*50)
        print("  ✅ Database OK")
        print("="*50 + "\n")

    finally:
        db.close()


if __name__ == "__main__":
    check_db()
