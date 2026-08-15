import sys
from loguru import logger
from config.settings import settings

logger.remove()
logger.add(sys.stdout, level=settings.log_level,
           format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {message}")
logger.add("logs/scraper_{time:YYYY-MM-DD}.log", rotation="00:00",
           retention="14 days", level=settings.log_level)
