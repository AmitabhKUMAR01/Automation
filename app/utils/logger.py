import logging
import os
from datetime import datetime

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

def get_log_file():
    today = datetime.now().strftime("%d-%m-%Y")
    return f"{LOG_DIR}/app-{today}.log"

def setup_logger():
    logger = logging.getLogger("leadtracker")
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    file_handler = logging.FileHandler(
        get_log_file(),
        encoding="utf-8",
        delay=True, 
    )
    file_handler.setFormatter(formatter)

    logger.addHandler(file_handler)

    return logger


logger = setup_logger()
