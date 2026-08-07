"""
PINLIVES Pro v3.1 - Database Models & Schema
Production-grade persistence with SQLAlchemy ORM
"""

from sqlalchemy import (
    create_engine, Column, Integer, String, Text, DateTime,
    Float, Boolean, LargeBinary, ForeignKey, UniqueConstraint,
    Index, JSON
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
from typing import Optional
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
        UniqueConstraint('phone', name='uq_phone'),
        Index('idx_phone', 'phone'),
        Index('idx_created_at', 'created_at'),
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
        Index('idx_session_id', 'session_id'),
        Index('idx_channel_id', 'channel_id'),
    )

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey('telegram_sessions.id'), nullable=False)
    channel_id = Column(Integer, nullable=False)
    channel_name = Column(String(255), nullable=False)
    added_at = Column(DateTime, default=datetime.utcnow)

    session = relationship("TelegramSession", back_populates="channels")
    messages = relationship("TelegramMessage", back_populates="channel")

class TelegramMessage(Base):
    """Stores received Telegram messages"""
    __tablename__ = "telegram_messages"
    __table_args__ = (
        Index('idx_session_id', 'session_id'),
        Index('idx_channel_id', 'channel_id'),
        Index('idx_received_at', 'received_at'),
        Index('idx_has_code', 'has_code'),
    )

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey('telegram_sessions.id'), nullable=False)
    channel_id = Column(Integer, ForeignKey('monitored_channels.id'), nullable=False)
    message_id = Column(Integer, nullable=False)
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
        Index('idx_message_id', 'message_id'),
        Index('idx_code_hash', 'code_hash'),
        Index('idx_extracted_at', 'extracted_at'),
        Index('idx_is_valid', 'is_valid'),
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

class SystemMetric(Base):
    """System performance metrics for monitoring"""
    __tablename__ = "system_metrics"
    __table_args__ = (
        Index('idx_timestamp', 'timestamp'),
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
        Index('idx_timestamp', 'timestamp'),
        Index('idx_level', 'level'),
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
