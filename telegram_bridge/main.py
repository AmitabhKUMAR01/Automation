from __future__ import annotations

import logging

from fastapi import FastAPI

from .db import create_tables
from .router import router

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("bridge")

# ── App factory ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="Telegram Webhook Bridge",
    description=(
        "Standalone microservice that receives Telegram bot replies via webhook "
        "and queues them as telegram_pending_actions rows for the local PC poller."
    ),
    version="1.1.0",
)

app.include_router(router)


@app.on_event("startup")
def on_startup() -> None:
    """Ensure the telegram_pending_actions table exists before serving requests."""
    log.info("[STARTUP] Telegram Bridge starting …")
    create_tables()
    log.info("[STARTUP] Ready.")
