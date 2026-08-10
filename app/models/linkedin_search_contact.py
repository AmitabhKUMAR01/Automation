import uuid
from sqlalchemy import Column, Integer, String, DateTime, Boolean, func
from app.config.database import Base


class LinkedinSearchContact(Base):
    """
    Tracks connections sent via the LinkedIn keyword search flow
    (positions + location from LinkedinSearchConfig).
    Not tied to a business_client — this is pure search-based outreach.
    """
    __tablename__ = "linkedin_search_contacts"

    id = Column(Integer, primary_key=True, index=True)
    uuid = Column(
        String(36),
        nullable=False,
        unique=True,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )

    # The position keyword that was used in the search (e.g. "CEO")
    position = Column(String(255), nullable=True)

    # The location used in the search (e.g. "Bahrain")
    location = Column(String(255), nullable=True)

    # The LinkedIn profile details
    name        = Column(String(255), nullable=True)
    profile_url = Column(String(500), nullable=False, unique=True)

    # Source: "search" (from search results) or "network" (from My Network tab)
    source = Column(String(50), nullable=False, default="search")

    # Connection tracking
    connection_sent    = Column(Boolean, default=True, nullable=False)
    connection_sent_at = Column(DateTime(timezone=True), nullable=True)

    is_connected = Column(Boolean, default=False, nullable=False)
    connected_at = Column(DateTime(timezone=True), nullable=True)


    contact_messaged_first = Column(Boolean, default=False, nullable=False)

    # Profile that sent this connection
    profile_id = Column(Integer, nullable=True)

    created_at  = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Export tracking — NULL = not yet exported to Google Sheets
    exported_at = Column(DateTime(timezone=True), nullable=True)
