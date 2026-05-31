import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.models import Base, engine
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def init_database():
    logger.info("Inizializzazione database...")
    Base.metadata.create_all(engine)
    logger.info("✅ Tabelle create:")
    for table in Base.metadata.tables:
        logger.info(f"   - {table}")
    logger.info("✅ Database pronto!")


if __name__ == "__main__":
    init_database()
