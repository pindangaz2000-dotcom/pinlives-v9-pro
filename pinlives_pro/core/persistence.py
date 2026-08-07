"""
PINLIVES Pro v3.1 - Persistence Layer
Handles all database operations with error recovery
"""

import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError, OperationalError
import hashlib

from ..models.database import (
    SessionLocal, TelegramSession, MonitoredChannel,
    TelegramMessage, ExtractedCode, SystemMetric, SystemLog
)

logger = logging.getLogger(__name__)

class PersistenceManager:
    """Unified database access layer with error handling"""

    def __init__(self):
        self.db: Session = SessionLocal()
        self.retry_attempts = 3
        self.retry_delay = 1

    def _execute_with_retry(self, operation, *args, **kwargs) -> Any:
        """Execute database operation with retry logic"""
        for attempt in range(self.retry_attempts):
            try:
                return operation(*args, **kwargs)
            except OperationalError as e:
                if attempt < self.retry_attempts - 1:
                    logger.warning(f"DB operation failed, retrying: {e}")
                    import asyncio
                    asyncio.sleep(self.retry_delay)
                else:
                    logger.error(f"DB operation failed after {self.retry_attempts} attempts: {e}")
                    raise
            except Exception as e:
                logger.error(f"Unexpected DB error: {type(e).__name__}: {e}")
                raise

    # ============================================================================
    # TELEGRAM SESSION OPERATIONS
    # ============================================================================

    def save_session(self, phone: str, session_string: str) -> bool:
        """Save or update Telethon session"""
        try:
            existing = self.db.query(TelegramSession).filter_by(phone=phone).first()
            if existing:
                existing.session_string = session_string
                existing.updated_at = datetime.utcnow()
                logger.info(f"✓ Session updated: {phone}")
            else:
                session = TelegramSession(phone=phone, session_string=session_string)
                self.db.add(session)
                logger.info(f"✓ Session created: {phone}")

            self.db.commit()
            return True
        except IntegrityError as e:
            self.db.rollback()
            logger.error(f"Integrity error saving session: {e}")
            return False
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error saving session: {type(e).__name__}: {e}")
            return False

    def load_session(self, phone: str) -> Optional[str]:
        """Load session string from database"""
        try:
            session = self.db.query(TelegramSession).filter_by(phone=phone).first()
            if session:
                logger.info(f"✓ Session loaded: {phone}")
                return session.session_string
            logger.debug(f"No session found: {phone}")
            return None
        except Exception as e:
            logger.error(f"Error loading session: {type(e).__name__}: {e}")
            return None

    def mark_authenticated(self, phone: str) -> bool:
        """Mark session as authenticated"""
        try:
            session = self.db.query(TelegramSession).filter_by(phone=phone).first()
            if session:
                session.is_active = True
                session.authenticated_at = datetime.utcnow()
                self.db.commit()
                logger.info(f"✓ Session authenticated: {phone}")
                return True
            return False
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error marking session authenticated: {e}")
            return False

    # ============================================================================
    # CHANNEL OPERATIONS
    # ============================================================================

    def add_channel(self, phone: str, channel_id: int, channel_name: str) -> bool:
        """Add channel to monitoring list"""
        try:
            session = self.db.query(TelegramSession).filter_by(phone=phone).first()
            if not session:
                logger.error(f"Session not found: {phone}")
                return False

            # Check if already exists
            existing = self.db.query(MonitoredChannel).filter_by(
                session_id=session.id, channel_id=channel_id
            ).first()
            if existing:
                logger.warning(f"Channel already monitored: {channel_name}")
                return False

            channel = MonitoredChannel(
                session_id=session.id,
                channel_id=channel_id,
                channel_name=channel_name
            )
            self.db.add(channel)
            self.db.commit()
            logger.info(f"✓ Channel added: {channel_name} ({channel_id})")
            return True
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error adding channel: {type(e).__name__}: {e}")
            return False

    def get_channels(self, phone: str) -> List[Dict[str, Any]]:
        """Get all channels for a session"""
        try:
            session = self.db.query(TelegramSession).filter_by(phone=phone).first()
            if not session:
                return []

            channels = self.db.query(MonitoredChannel).filter_by(session_id=session.id).all()
            return [
                {
                    'channel_id': c.channel_id,
                    'channel_name': c.channel_name,
                    'added_at': c.added_at.isoformat()
                }
                for c in channels
            ]
        except Exception as e:
            logger.error(f"Error getting channels: {type(e).__name__}: {e}")
            return []

    # ============================================================================
    # MESSAGE OPERATIONS
    # ============================================================================

    def save_message(self, phone: str, chat_id: int, chat_name: str,
                     message_id: int, text: str, has_media: bool = False,
                     media_path: Optional[str] = None) -> Optional[int]:
        """Save received message to database"""
        try:
            session = self.db.query(TelegramSession).filter_by(phone=phone).first()
            if not session:
                logger.error(f"Session not found: {phone}")
                return None

            channel = self.db.query(MonitoredChannel).filter_by(
                session_id=session.id, channel_id=chat_id
            ).first()
            if not channel:
                logger.error(f"Channel not found: {chat_id}")
                return None

            message = TelegramMessage(
                session_id=session.id,
                channel_id=channel.id,
                message_id=message_id,
                text=text,
                has_media=has_media,
                media_path=media_path
            )
            self.db.add(message)
            self.db.commit()
            logger.info(f"✓ Message saved: {chat_name} - {text[:50] if text else '(media)'}")
            return message.id
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error saving message: {type(e).__name__}: {e}")
            return None

    # ============================================================================
    # CODE EXTRACTION OPERATIONS
    # ============================================================================

    def save_code(self, message_id: int, code: str, entropy: float = None,
                  pattern_score: float = None, ocr_confidence: float = None) -> bool:
        """Save extracted code"""
        try:
            code_hash = hashlib.sha256(code.encode()).hexdigest()

            extracted = ExtractedCode(
                message_id=message_id,
                code=code,
                code_hash=code_hash,
                is_valid=True,
                entropy=entropy,
                pattern_score=pattern_score,
                ocr_confidence=ocr_confidence
            )
            self.db.add(extracted)
            self.db.commit()
            logger.info(f"✓ Code saved: {code[:20]}... (entropy={entropy:.2f})")
            return True
        except IntegrityError:
            self.db.rollback()
            logger.warning(f"Code already exists: {code}")
            return False
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error saving code: {type(e).__name__}: {e}")
            return False

    def get_codes(self, limit: int = 100, valid_only: bool = True) -> List[Dict[str, Any]]:
        """Get extracted codes with filtering"""
        try:
            query = self.db.query(ExtractedCode)
            if valid_only:
                query = query.filter_by(is_valid=True)

            codes = query.order_by(ExtractedCode.extracted_at.desc()).limit(limit).all()
            return [
                {
                    'code': c.code,
                    'entropy': c.entropy,
                    'pattern_score': c.pattern_score,
                    'confidence': c.ocr_confidence,
                    'extracted_at': c.extracted_at.isoformat()
                }
                for c in codes
            ]
        except Exception as e:
            logger.error(f"Error getting codes: {type(e).__name__}: {e}")
            return []

    # ============================================================================
    # METRIC LOGGING
    # ============================================================================

    def log_metric(self, metric_name: str, metric_value: float, tags: Dict = None) -> bool:
        """Log system metric for monitoring"""
        try:
            metric = SystemMetric(
                metric_name=metric_name,
                metric_value=metric_value,
                tags=tags or {}
            )
            self.db.add(metric)
            self.db.commit()
            return True
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error logging metric: {type(e).__name__}: {e}")
            return False

    def log_event(self, level: str, component: str, message: str, context: Dict = None) -> bool:
        """Log system event for audit trail"""
        try:
            log = SystemLog(
                level=level,
                component=component,
                message=message,
                context=context or {}
            )
            self.db.add(log)
            self.db.commit()
            return True
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error logging event: {type(e).__name__}: {e}")
            return False

    def cleanup_old_metrics(self, days: int = 7) -> int:
        """Delete metrics older than N days"""
        try:
            cutoff = datetime.utcnow() - timedelta(days=days)
            deleted = self.db.query(SystemMetric).filter(
                SystemMetric.timestamp < cutoff
            ).delete()
            self.db.commit()
            logger.info(f"✓ Cleaned up {deleted} old metrics")
            return deleted
        except Exception as e:
            self.db.rollback()
            logger.error(f"Error cleaning metrics: {type(e).__name__}: {e}")
            return 0

    def close(self):
        """Close database connection"""
        try:
            self.db.close()
            logger.info("✓ Database connection closed")
        except Exception as e:
            logger.error(f"Error closing database: {e}")

# Singleton instance
_persistence_manager = None

def get_persistence() -> PersistenceManager:
    """Get or create persistence manager singleton"""
    global _persistence_manager
    if _persistence_manager is None:
        _persistence_manager = PersistenceManager()
    return _persistence_manager
