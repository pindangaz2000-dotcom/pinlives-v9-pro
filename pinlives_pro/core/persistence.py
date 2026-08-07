"""
PINLIVES Pro v3.1 - Persistence Layer

Every operation runs in its own short-lived SQLAlchemy session. A Session is not
safe to share between concurrent tasks, and this layer is used from both request
handlers and the background message processor at the same time.
"""

import hashlib
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from ..models.database import (
    ExtractedCode,
    MonitoredChannel,
    SessionLocal,
    SystemLog,
    SystemMetric,
    TelegramMessage,
    TelegramSession,
)

logger = logging.getLogger(__name__)


class PersistenceManager:
    """Database access layer. Safe to share across concurrent tasks."""

    @contextmanager
    def _session(self):
        """One unit of work: commit on success, roll back on failure, always close."""
        session = SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Telethon sessions
    # ------------------------------------------------------------------

    def save_session(self, phone: str, session_string: str) -> bool:
        """Create or update the stored StringSession for an account."""
        try:
            with self._session() as db:
                existing = db.query(TelegramSession).filter_by(phone=phone).first()
                if existing:
                    existing.session_string = session_string
                    existing.updated_at = datetime.utcnow()
                    logger.info("Session updated: %s", phone)
                else:
                    db.add(TelegramSession(phone=phone, session_string=session_string))
                    logger.info("Session created: %s", phone)
            return True
        except SQLAlchemyError as e:
            logger.error("Error saving session for %s: %s: %s", phone, type(e).__name__, e)
            return False

    def load_session(self, phone: str) -> Optional[str]:
        """Return the stored StringSession, or None when there is none."""
        try:
            with self._session() as db:
                row = db.query(TelegramSession).filter_by(phone=phone).first()
                if row is None:
                    logger.debug("No session stored for %s", phone)
                    return None
                logger.info("Session loaded: %s", phone)
                return row.session_string
        except SQLAlchemyError as e:
            logger.error("Error loading session for %s: %s: %s", phone, type(e).__name__, e)
            return None

    def mark_authenticated(self, phone: str) -> bool:
        try:
            with self._session() as db:
                row = db.query(TelegramSession).filter_by(phone=phone).first()
                if row is None:
                    logger.error("Cannot mark authenticated, no session for %s", phone)
                    return False
                row.is_active = True
                row.authenticated_at = datetime.utcnow()
            logger.info("Session authenticated: %s", phone)
            return True
        except SQLAlchemyError as e:
            logger.error("Error marking authenticated: %s: %s", type(e).__name__, e)
            return False

    def is_authenticated(self, phone: str) -> bool:
        try:
            with self._session() as db:
                row = db.query(TelegramSession).filter_by(phone=phone).first()
                return bool(row and row.is_active)
        except SQLAlchemyError as e:
            logger.error("Error checking auth state: %s: %s", type(e).__name__, e)
            return False

    # ------------------------------------------------------------------
    # Channels
    # ------------------------------------------------------------------

    def add_channel(self, phone: str, channel_id: int, channel_name: str,
                    site_id: str = '') -> bool:
        """Register a channel. False when the account is unknown or it already exists."""
        try:
            with self._session() as db:
                session_row = db.query(TelegramSession).filter_by(phone=phone).first()
                if session_row is None:
                    logger.error("Cannot add channel, no session for %s", phone)
                    return False

                exists = db.query(MonitoredChannel).filter_by(
                    session_id=session_row.id, channel_id=channel_id
                ).first()
                if exists:
                    logger.warning("Channel already monitored: %s", channel_name)
                    return False

                db.add(MonitoredChannel(
                    session_id=session_row.id,
                    channel_id=channel_id,
                    channel_name=channel_name,
                    site_id=(site_id or '').strip().lower(),
                ))
            logger.info("Channel added: %s (%s)", channel_name, channel_id)
            return True
        except IntegrityError:
            logger.warning("Channel already monitored (race): %s", channel_name)
            return False
        except SQLAlchemyError as e:
            logger.error("Error adding channel: %s: %s", type(e).__name__, e)
            return False

    def get_channels(self, phone: str) -> List[Dict[str, Any]]:
        try:
            with self._session() as db:
                session_row = db.query(TelegramSession).filter_by(phone=phone).first()
                if session_row is None:
                    return []
                rows = db.query(MonitoredChannel).filter_by(session_id=session_row.id).all()
                return [
                    {
                        'channel_id': r.channel_id,
                        'channel_name': r.channel_name,
                        'site_id': r.site_id or '',
                        'added_at': r.added_at.isoformat() if r.added_at else None,
                    }
                    for r in rows
                ]
        except SQLAlchemyError as e:
            logger.error("Error listing channels: %s: %s", type(e).__name__, e)
            return []

    def get_channel_site(self, phone: str, channel_id: int) -> str:
        """The site_id configured for a channel, or '' when it has none."""
        try:
            with self._session() as db:
                session_row = db.query(TelegramSession).filter_by(phone=phone).first()
                if session_row is None:
                    return ''
                row = db.query(MonitoredChannel).filter_by(
                    session_id=session_row.id, channel_id=channel_id
                ).first()
                return (row.site_id or '') if row else ''
        except SQLAlchemyError as e:
            logger.error("Error reading channel site: %s: %s", type(e).__name__, e)
            return ''

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    def save_message(
        self,
        phone: str,
        chat_id: int,
        chat_name: str,
        message_id: int,
        text: Optional[str],
        has_media: bool = False,
        media_path: Optional[str] = None,
        auto_register_channel: bool = True,
    ) -> Optional[int]:
        """Store a received message and return its row id.

        The listener subscribes to every incoming message, so a chat is often seen
        before anyone registers it. With auto_register_channel the chat is recorded
        on first sight instead of the message being dropped.

        Returns None on failure. Returns the existing row id when the same
        (channel, message_id) has already been stored.
        """
        try:
            with self._session() as db:
                session_row = db.query(TelegramSession).filter_by(phone=phone).first()
                if session_row is None:
                    logger.error("Cannot save message, no session for %s", phone)
                    return None

                channel = db.query(MonitoredChannel).filter_by(
                    session_id=session_row.id, channel_id=chat_id
                ).first()

                if channel is None:
                    if not auto_register_channel:
                        logger.error("Cannot save message, channel %s not registered", chat_id)
                        return None
                    channel = MonitoredChannel(
                        session_id=session_row.id,
                        channel_id=chat_id,
                        channel_name=chat_name or f"Chat_{chat_id}",
                    )
                    db.add(channel)
                    db.flush()
                    logger.info("Auto-registered channel: %s (%s)", chat_name, chat_id)

                duplicate = db.query(TelegramMessage).filter_by(
                    channel_id=channel.id, message_id=message_id
                ).first()
                if duplicate:
                    logger.debug("Message already stored: %s/%s", chat_id, message_id)
                    return duplicate.id

                message = TelegramMessage(
                    session_id=session_row.id,
                    channel_id=channel.id,
                    message_id=message_id,
                    text=text,
                    has_media=has_media,
                    media_path=media_path,
                )
                db.add(message)
                db.flush()
                new_id = message.id

            logger.info("Message saved: %s - %s", chat_name, (text or '(media)')[:50])
            return new_id
        except IntegrityError:
            logger.debug("Message already stored (race): %s/%s", chat_id, message_id)
            return self._find_message_id(phone, chat_id, message_id)
        except SQLAlchemyError as e:
            logger.error("Error saving message: %s: %s", type(e).__name__, e)
            return None

    def _find_message_id(self, phone: str, chat_id: int, message_id: int) -> Optional[int]:
        try:
            with self._session() as db:
                session_row = db.query(TelegramSession).filter_by(phone=phone).first()
                if session_row is None:
                    return None
                channel = db.query(MonitoredChannel).filter_by(
                    session_id=session_row.id, channel_id=chat_id
                ).first()
                if channel is None:
                    return None
                row = db.query(TelegramMessage).filter_by(
                    channel_id=channel.id, message_id=message_id
                ).first()
                return row.id if row else None
        except SQLAlchemyError:
            return None

    def get_messages(self, limit: int = 50) -> List[Dict[str, Any]]:
        try:
            with self._session() as db:
                rows = (
                    db.query(TelegramMessage)
                    .order_by(TelegramMessage.received_at.desc())
                    .limit(limit)
                    .all()
                )
                return [
                    {
                        'id': r.id,
                        'message_id': r.message_id,
                        'text': r.text,
                        'has_media': r.has_media,
                        'received_at': r.received_at.isoformat() if r.received_at else None,
                    }
                    for r in rows
                ]
        except SQLAlchemyError as e:
            logger.error("Error listing messages: %s: %s", type(e).__name__, e)
            return []

    # ------------------------------------------------------------------
    # Extracted codes
    # ------------------------------------------------------------------

    def save_code(
        self,
        message_id: int,
        code: str,
        entropy: Optional[float] = None,
        pattern_score: Optional[float] = None,
        ocr_confidence: Optional[float] = None,
    ) -> bool:
        """Store an extracted code. False when the code was already recorded."""
        try:
            code_hash = hashlib.sha256(code.encode()).hexdigest()
            with self._session() as db:
                db.add(ExtractedCode(
                    message_id=message_id,
                    code=code,
                    code_hash=code_hash,
                    is_valid=True,
                    entropy=entropy,
                    pattern_score=pattern_score,
                    ocr_confidence=ocr_confidence,
                ))
            logger.info("Code saved: %s", code[:20])
            return True
        except IntegrityError:
            logger.warning("Code already recorded: %s", code)
            return False
        except SQLAlchemyError as e:
            logger.error("Error saving code: %s: %s", type(e).__name__, e)
            return False

    def get_codes(self, limit: int = 100, valid_only: bool = True) -> List[Dict[str, Any]]:
        try:
            with self._session() as db:
                query = db.query(ExtractedCode)
                if valid_only:
                    query = query.filter_by(is_valid=True)
                rows = query.order_by(ExtractedCode.extracted_at.desc()).limit(limit).all()
                return [
                    {
                        'code': r.code,
                        'entropy': r.entropy,
                        'pattern_score': r.pattern_score,
                        'confidence': r.ocr_confidence,
                        'extracted_at': r.extracted_at.isoformat() if r.extracted_at else None,
                    }
                    for r in rows
                ]
        except SQLAlchemyError as e:
            logger.error("Error listing codes: %s: %s", type(e).__name__, e)
            return []

    def count_codes(self) -> int:
        try:
            with self._session() as db:
                return db.query(ExtractedCode).count()
        except SQLAlchemyError:
            return 0

    def count_messages(self) -> int:
        try:
            with self._session() as db:
                return db.query(TelegramMessage).count()
        except SQLAlchemyError:
            return 0

    # ------------------------------------------------------------------
    # Metrics and audit log
    # ------------------------------------------------------------------

    def log_metric(self, metric_name: str, metric_value: float, tags: Dict = None) -> bool:
        try:
            with self._session() as db:
                db.add(SystemMetric(
                    metric_name=metric_name,
                    metric_value=metric_value,
                    tags=tags or {},
                ))
            return True
        except SQLAlchemyError as e:
            logger.error("Error logging metric: %s: %s", type(e).__name__, e)
            return False

    def log_event(self, level: str, component: str, message: str, context: Dict = None) -> bool:
        try:
            with self._session() as db:
                db.add(SystemLog(
                    level=level,
                    component=component,
                    message=message,
                    context=context or {},
                ))
            return True
        except SQLAlchemyError as e:
            logger.error("Error logging event: %s: %s", type(e).__name__, e)
            return False

    def get_logs(self, limit: int = 20, level: Optional[str] = None) -> List[Dict[str, Any]]:
        try:
            with self._session() as db:
                query = db.query(SystemLog)
                if level:
                    query = query.filter_by(level=level)
                rows = query.order_by(SystemLog.timestamp.desc()).limit(limit).all()
                return [
                    {
                        'timestamp': r.timestamp.isoformat() if r.timestamp else None,
                        'level': r.level,
                        'component': r.component,
                        'message': r.message,
                    }
                    for r in rows
                ]
        except SQLAlchemyError as e:
            logger.error("Error listing logs: %s: %s", type(e).__name__, e)
            return []

    def cleanup_old_metrics(self, days: int = 7) -> int:
        try:
            cutoff = datetime.utcnow() - timedelta(days=days)
            with self._session() as db:
                deleted = db.query(SystemMetric).filter(SystemMetric.timestamp < cutoff).delete()
            logger.info("Cleaned up %s old metrics", deleted)
            return deleted
        except SQLAlchemyError as e:
            logger.error("Error cleaning metrics: %s: %s", type(e).__name__, e)
            return 0

    def health_check(self) -> bool:
        """True when the database answers a trivial query."""
        try:
            with self._session() as db:
                db.query(TelegramSession).limit(1).all()
            return True
        except SQLAlchemyError as e:
            logger.error("Database health check failed: %s: %s", type(e).__name__, e)
            return False

    def close(self):
        """No-op: sessions are per-operation and already closed."""
        return None


_persistence_manager: Optional[PersistenceManager] = None


def get_persistence() -> PersistenceManager:
    global _persistence_manager
    if _persistence_manager is None:
        _persistence_manager = PersistenceManager()
    return _persistence_manager
