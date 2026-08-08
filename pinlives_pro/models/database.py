"""
PINLIVES Pro v3.1 - Database Models & Schema
Production-grade persistence with SQLAlchemy ORM
"""

from sqlalchemy import (
    create_engine, Column, Integer, BigInteger, String, Text, DateTime,
    Float, Boolean, ForeignKey, UniqueConstraint,
    Index, JSON
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
import os
from pathlib import Path

Base = declarative_base()

# ============================================================================
# DATABASE CONFIGURATION
# ============================================================================

def get_db_url() -> str:
    """Get database URL from environment or use default SQLite"""
    db_type = os.getenv('DB_TYPE', 'sqlite')

    if db_type == 'postgres':
        user = os.getenv('DB_USER', 'pinlives')
        password = os.getenv('DB_PASSWORD', 'pinlives')
        host = os.getenv('DB_HOST', 'localhost')
        port = os.getenv('DB_PORT', '5432')
        name = os.getenv('DB_NAME', 'pinlives_pro')
        return f"postgresql://{user}:{password}@{host}:{port}/{name}"

    # SQLite default
    db_path = Path(os.getenv('DB_PATH', '/tmp/pinlives_pro.db'))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{db_path}"

engine = create_engine(
    get_db_url(),
    echo=os.getenv('SQL_ECHO', 'false').lower() == 'true',
    pool_pre_ping=True,  # Validate connections before using
    connect_args={"check_same_thread": False} if 'sqlite' in get_db_url() else {}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# ============================================================================
# DATABASE MODELS
# ============================================================================

class TelegramSession(Base):
    """Stores Telethon session strings for account authentication"""
    __tablename__ = "telegram_sessions"
    __table_args__ = (
        UniqueConstraint('phone', name='uq_session_phone'),
        Index('idx_session_created_at', 'created_at'),
    )

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String(20), unique=True, nullable=False)
    session_string = Column(Text, nullable=False)  # Telethon StringSession
    is_active = Column(Boolean, default=False)
    authenticated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    messages = relationship("TelegramMessage", back_populates="session")
    channels = relationship("MonitoredChannel", back_populates="session")

class MonitoredChannel(Base):
    """Channels being monitored for messages"""
    __tablename__ = "monitored_channels"
    __table_args__ = (
        UniqueConstraint('session_id', 'channel_id', name='uq_session_channel'),
        Index('idx_channel_session_id', 'session_id'),
        Index('idx_channel_tg_id', 'channel_id'),
    )

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey('telegram_sessions.id'), nullable=False)
    # Telegram channel IDs exceed 32-bit range (e.g. -1001234567890)
    channel_id = Column(BigInteger, nullable=False)
    channel_name = Column(String(255), nullable=False)
    # Which site's code format this channel publishes. Selects the extractor:
    # every site has its own shape, so the wrong one yields nothing or garbage.
    site_id = Column(String(32), nullable=False, default='')
    added_at = Column(DateTime, default=datetime.utcnow)

    session = relationship("TelegramSession", back_populates="channels")
    messages = relationship("TelegramMessage", back_populates="channel")

class TelegramMessage(Base):
    """Stores received Telegram messages"""
    __tablename__ = "telegram_messages"
    __table_args__ = (
        UniqueConstraint('channel_id', 'message_id', name='uq_channel_message'),
        Index('idx_message_session_id', 'session_id'),
        Index('idx_message_channel_id', 'channel_id'),
        Index('idx_message_received_at', 'received_at'),
    )

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey('telegram_sessions.id'), nullable=False)
    channel_id = Column(Integer, ForeignKey('monitored_channels.id'), nullable=False)
    message_id = Column(BigInteger, nullable=False)
    text = Column(Text, nullable=True)
    has_media = Column(Boolean, default=False)
    media_path = Column(String(500), nullable=True)
    received_at = Column(DateTime, default=datetime.utcnow)

    session = relationship("TelegramSession", back_populates="messages")
    channel = relationship("MonitoredChannel", back_populates="messages")
    codes = relationship("ExtractedCode", back_populates="message")

class ExtractedCode(Base):
    """Code extracted from messages with metadata"""
    __tablename__ = "extracted_codes"
    __table_args__ = (
        Index('idx_code_message_id', 'message_id'),
        Index('idx_code_hash', 'code_hash'),
        Index('idx_code_extracted_at', 'extracted_at'),
        Index('idx_code_is_valid', 'is_valid'),
    )

    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(Integer, ForeignKey('telegram_messages.id'), nullable=False)
    code = Column(String(255), nullable=False, unique=True)
    code_hash = Column(String(64), nullable=False, unique=True)  # SHA256
    is_valid = Column(Boolean, default=True)
    entropy = Column(Float, nullable=True)
    pattern_score = Column(Float, nullable=True)
    ocr_confidence = Column(Float, nullable=True)
    extracted_at = Column(DateTime, default=datetime.utcnow)

    message = relationship("TelegramMessage", back_populates="codes")

class Provenance:
    """Where a record came from. Production results must be REAL.

    Without this, a replayed or hand-entered record is indistinguishable from a
    live capture, and a dashboard can read healthy while real ingestion is dead.
    """
    REAL = 'REAL'            # captured live from Telegram
    REPLAY = 'REPLAY'        # re-processed from the journal
    BACKFILL = 'BACKFILL'    # pulled from channel history
    MANUAL = 'MANUAL'        # entered by an operator
    SIMULATED = 'SIMULATED'  # synthetic, for tests

    ALL = (REAL, REPLAY, BACKFILL, MANUAL, SIMULATED)


class EventJournal(Base):
    """Immutable record of every event received, written before processing.

    Journaling first means a slow or broken extractor cannot lose a post, and
    that a new OCR or filter can be measured by replaying real traffic instead
    of waiting for channels to post again.
    """
    __tablename__ = "event_journal"
    __table_args__ = (
        UniqueConstraint('chat_id', 'message_id', name='uq_journal_event'),
        Index('idx_journal_received_at', 'received_at'),
        Index('idx_journal_chat', 'chat_id'),
        Index('idx_journal_provenance', 'provenance'),
        Index('idx_journal_processed', 'processed'),
    )

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String(20), nullable=False)
    chat_id = Column(BigInteger, nullable=False)
    chat_name = Column(String(255), nullable=True)
    message_id = Column(BigInteger, nullable=False)
    site_id = Column(String(32), nullable=False, default='')
    text = Column(Text, nullable=True)
    has_media = Column(Boolean, default=False)
    media_path = Column(String(500), nullable=True)
    posted_at = Column(DateTime, nullable=True)
    received_at = Column(DateTime, default=datetime.utcnow)
    provenance = Column(String(16), nullable=False, default=Provenance.REAL)
    processed = Column(Boolean, default=False)
    # Milliseconds from journal write to extraction finishing.
    process_ms = Column(Float, nullable=True)


class SystemMetric(Base):
    """System performance metrics for monitoring"""
    __tablename__ = "system_metrics"
    __table_args__ = (
        Index('idx_metric_timestamp', 'timestamp'),
        Index('idx_metric_name', 'metric_name'),
    )

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    metric_name = Column(String(100), nullable=False)
    metric_value = Column(Float, nullable=False)
    tags = Column(JSON, nullable=True)  # Additional context

class SystemLog(Base):
    """Structured logging for audit trail"""
    __tablename__ = "system_logs"
    __table_args__ = (
        Index('idx_log_timestamp', 'timestamp'),
        Index('idx_log_level', 'level'),
    )

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    level = Column(String(20), nullable=False)  # INFO, WARNING, ERROR
    component = Column(String(100), nullable=False)  # bot, daemon, backend
    message = Column(Text, nullable=False)
    context = Column(JSON, nullable=True)  # Additional data

# ============================================================================
# DATABASE INITIALIZATION
# ============================================================================

def init_db():
    """Create all tables"""
    Base.metadata.create_all(bind=engine)
    print("✓ Database initialized")

def get_session():
    """Get database session for dependency injection"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

if __name__ == '__main__':
    init_db()
