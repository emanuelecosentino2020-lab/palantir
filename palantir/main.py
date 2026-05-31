import logging
import sys
import os

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("palantir.log"),
    ]
)

logger = logging.getLogger("palantir")


def main():
    logger.info("🚀 PALANTIR TRADING SYSTEM — Avvio")
    logger.info("=" * 50)

    # 1. Inizializza database
    logger.info("📦 Inizializzazione database...")
    from database.init_db import init_database
    init_database()

    # 2. Avvia scheduler (blocca il processo)
    logger.info("⏰ Avvio scheduler...")
    from core.scheduler import start_scheduler
    start_scheduler()


if __name__ == "__main__":
    main()
