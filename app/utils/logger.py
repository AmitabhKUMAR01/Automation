import logging
from logging.handlers import TimedRotatingFileHandler
import os

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

def setup_logger():
    logger = logging.getLogger("leadtracker")
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    # Use TimedRotatingFileHandler to rotate logs daily at midnight
    file_handler = TimedRotatingFileHandler(
        filename=os.path.join(LOG_DIR, "app.log"),
        when="midnight",
        interval=1,
        backupCount=30,      # Keep logs for 30 days
        encoding="utf-8",
        delay=True,
    )
    # Append the date suffix when rotating (e.g. app.log.04-08-2026)
    file_handler.suffix = "%d-%m-%Y"
    file_handler.setFormatter(formatter)

    logger.addHandler(file_handler)

    return logger


logger = setup_logger()
