from __future__ import annotations

import os
import logging
from urllib.parse import quote_plus

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import Column, Integer, String, Text, JSON, DateTime, func, create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

log = logging.getLogger("bridge.db")

# ── Connection settings ───────────────────────────────────────────────────────

DB_USER     = os.getenv("DB_USER", "root")
DB_PASSWORD = quote_plus(os.getenv("DB_PASSWORD", ""))
DB_HOST     = os.getenv("DB_HOST", "localhost")
DB_PORT     = os.getenv("DB_PORT", "3306")
DB_NAME     = os.getenv("DB_NAME", "scraped_raw_data")

DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine       = create_engine(DATABASE_URL, pool_pre_ping=True, echo=False)
SessionLocal = sessionmaker(bind=engine)
Base         = declarative_base()


# ── ORM Model ─────────────────────────────────────────────────────────────────

class TelegramPendingAction(Base):
    __tablename__ = "telegram_pending_actions"

    id            = Column(Integer, primary_key=True, index=True)
    chat_id       = Column(String(100), nullable=True,  index=True)
    pitch_id      = Column(Integer,     nullable=True,  index=True)
    profile_id    = Column(Integer,     nullable=True)
    action        = Column(Text,        nullable=False)
    status        = Column(String(20),  nullable=False, default="pending", index=True)
    raw_payload   = Column(JSON,        nullable=True)
    error_message = Column(Text,        nullable=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    processed_at  = Column(DateTime(timezone=True), nullable=True)


# ── Schema bootstrap ──────────────────────────────────────────────────────────

def create_tables() -> None:
    log.info("[DB] Running create_tables() — ensuring schema is up to date …")
    Base.metadata.create_all(bind=engine)
    log.info("[DB] Schema ready.")
