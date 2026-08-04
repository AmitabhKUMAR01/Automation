import logging
import os
from datetime import datetime

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)


class DailyRotatingHandler(logging.FileHandler):
    """
    A FileHandler that writes to logs/app-DD-MM-YYYY.log.
    On every log record it checks if the calendar date has changed;
    if so it closes the old file and opens a new dated file automatically —
    even when the app runs continuously across midnight.
    """

    def __init__(self, log_dir: str, encoding: str = "utf-8"):
        self.log_dir  = log_dir
        self._current_date = self._today()
        super().__init__(self._build_path(), encoding=encoding, delay=True)

    def _today(self) -> str:
        return datetime.now().strftime("%d-%m-%Y")

    def _build_path(self) -> str:
        return os.path.join(self.log_dir, f"app-{self._current_date}.log")

    def emit(self, record: logging.LogRecord) -> None:
        today = self._today()
        if today != self._current_date:
            # Date has changed — roll over to a new file
            self.close()
            self._current_date = today
            self.baseFilename   = os.path.abspath(self._build_path())
            self.stream         = None      # reopen on next write
        super().emit(record)


def setup_logger() -> logging.Logger:
    logger = logging.getLogger("leadtracker")
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    file_handler = DailyRotatingHandler(LOG_DIR, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


logger = setup_logger()
