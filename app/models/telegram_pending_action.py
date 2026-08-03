from sqlalchemy import Column, String, DateTime, Integer, Text, JSON, func
from app.config.database import Base


class TelegramPendingAction(Base):
    __tablename__ = "telegram_pending_actions"

    id          = Column(Integer, primary_key=True, index=True)

    # Telegram identifiers
    chat_id     = Column(String(100), nullable=True,  index=True)  # Telegram chat_id

    # Action context (parsed from callback_data or message text)
    pitch_id    = Column(Integer,     nullable=True,  index=True)  # FK to sales_pitches.id (not enforced — cross-service)
    profile_id  = Column(Integer,     nullable=True)               # which LinkedIn profile to send from
    action      = Column(Text,        nullable=False)              # "yes" | "no" | custom text

    # Lifecycle
    status      = Column(String(20),  nullable=False, index=True, default="pending")   # pending / processing / done / failed
    raw_payload = Column(JSON,        nullable=True)               # full Telegram update (debug)
    error_message = Column(Text,      nullable=True)               # set on failure

    # Timestamps
    created_at  = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    processed_at = Column(DateTime(timezone=True), nullable=True)  # set by local poller when done
