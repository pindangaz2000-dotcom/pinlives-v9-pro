"""
PINLIVES Pro v3.1 - Persistence Layer

Every operation runs in its own short-lived SQLAlchemy session. A Session is not
safe to share between concurrent tasks, and this layer is used from both request
handlers and the background message processor at the same time.
"""

import hashlib
import logging
import math
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from ..models.database import (
    EventJournal,
    ExtractedCode,
    MonitoredChannel,
    Provenance,
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
    # Event journal
    # ------------------------------------------------------------------

    def journal_event(self, payload: Dict[str, Any],
                      provenance: str = Provenance.REAL) -> Optional[int]:
        """Record an incoming event before anything processes it.

        Returns the journal row id, or the existing id when this event was
        already recorded. None means the write failed.
        """
        try:
            with self._session() as db:
                existing = db.query(EventJournal).filter_by(
                    chat_id=payload.get('chat_id'),
                    message_id=payload.get('message_id'),
                ).first()
                if existing:
                    return existing.id

                posted = payload.get('date')
                posted_at = None
                if posted:
                    try:
                        posted_at = datetime.fromisoformat(str(posted).replace('Z', '+00:00'))
                        if posted_at.tzinfo is not None:
                            posted_at = posted_at.replace(tzinfo=None)
                    except ValueError:
                        posted_at = None

                row = EventJournal(
                    phone=payload.get('phone') or '',
                    chat_id=payload.get('chat_id'),
                    chat_name=payload.get('chat_name'),
                    message_id=payload.get('message_id'),
                    site_id=(payload.get('site_id') or '').lower(),
                    text=payload.get('text'),
                    has_media=bool(payload.get('has_media')),
                    media_path=payload.get('media_path'),
                    posted_at=posted_at,
                    provenance=provenance,
                )
                db.add(row)
                db.flush()
                return row.id
        except IntegrityError:
            return None
        except SQLAlchemyError as e:
            logger.error("Error journalling event: %s: %s", type(e).__name__, e)
            return None

    def journal_and_store_message(self, payload: Dict[str, Any],
                                  provenance: str = Provenance.REAL) -> tuple:
        """Journal the event and store the message in ONE transaction.

        Both are up-front inserts with no slow work between them, so a single
        commit does the job two used to. Coalescing durable writes is the same
        principle a batched telemetry transport applies to log delivery — here
        it halves the fsync cost measured as the pipeline's dominant latency.

        Returns (journal_id, message_id). Either can be an existing id (redelivery)
        or None on failure.
        """
        phone = payload.get('phone') or ''
        chat_id = payload.get('chat_id')
        message_id = payload.get('message_id')
        site_id = (payload.get('site_id') or '').lower()
        try:
            with self._session() as db:
                # --- journal (idempotent) ---
                jrow = db.query(EventJournal).filter_by(
                    chat_id=chat_id, message_id=message_id).first()
                if jrow is None:
                    posted_at = None
                    posted = payload.get('date')
                    if posted:
                        try:
                            posted_at = datetime.fromisoformat(str(posted).replace('Z', '+00:00'))
                            if posted_at.tzinfo is not None:
                                posted_at = posted_at.replace(tzinfo=None)
                        except ValueError:
                            posted_at = None
                    jrow = EventJournal(
                        phone=phone, chat_id=chat_id, chat_name=payload.get('chat_name'),
                        message_id=message_id, site_id=site_id, text=payload.get('text'),
                        has_media=bool(payload.get('has_media')),
                        media_path=payload.get('media_path'), posted_at=posted_at,
                        provenance=provenance,
                    )
                    db.add(jrow)
                    db.flush()

                # --- account + channel (auto-register on first sight) ---
                session_row = db.query(TelegramSession).filter_by(phone=phone).first()
                if session_row is None:
                    logger.error("Cannot store message, no session for %s", phone)
                    return jrow.id, None
                channel = db.query(MonitoredChannel).filter_by(
                    session_id=session_row.id, channel_id=chat_id).first()
                if channel is None:
                    channel = MonitoredChannel(
                        session_id=session_row.id, channel_id=chat_id,
                        channel_name=payload.get('chat_name') or f"Chat_{chat_id}",
                        site_id=site_id)
                    db.add(channel)
                    db.flush()

                # --- message (idempotent on (channel, message_id)) ---
                existing = db.query(TelegramMessage).filter_by(
                    channel_id=channel.id, message_id=message_id).first()
                if existing:
                    return jrow.id, existing.id
                mrow = TelegramMessage(
                    session_id=session_row.id, channel_id=channel.id,
                    message_id=message_id, text=payload.get('text'),
                    has_media=bool(payload.get('has_media')),
                    media_path=payload.get('media_path'))
                db.add(mrow)
                db.flush()
                return jrow.id, mrow.id
        except IntegrityError:
            # Concurrent redelivery; fall back to the id lookups.
            jid = self.journal_event(payload, provenance)
            mid = self._find_message_id(phone, chat_id, message_id)
            return jid, mid
        except SQLAlchemyError as e:
            logger.error("Error in journal_and_store_message: %s: %s", type(e).__name__, e)
            return None, None

    def mark_journal_processed(self, journal_id: int, process_ms: float) -> bool:
        try:
            with self._session() as db:
                row = db.query(EventJournal).filter_by(id=journal_id).first()
                if row is None:
                    return False
                row.processed = True
                row.process_ms = process_ms
            return True
        except SQLAlchemyError as e:
            logger.error("Error updating journal: %s: %s", type(e).__name__, e)
            return False

    def get_journal(self, limit: int = 100, chat_id: Optional[int] = None,
                    provenance: Optional[str] = None) -> List[Dict[str, Any]]:
        """Recorded events, newest first. The source for replay."""
        try:
            with self._session() as db:
                q = db.query(EventJournal)
                if chat_id is not None:
                    q = q.filter_by(chat_id=chat_id)
                if provenance:
                    q = q.filter_by(provenance=provenance)
                rows = q.order_by(EventJournal.received_at.desc()).limit(limit).all()
                return [
                    {
                        'id': r.id,
                        'phone': r.phone,
                        'chat_id': r.chat_id,
                        'chat_name': r.chat_name,
                        'message_id': r.message_id,
                        'site_id': r.site_id,
                        'text': r.text,
                        'has_media': r.has_media,
                        'media_path': r.media_path,
                        'provenance': r.provenance,
                        'processed': r.processed,
                        'process_ms': r.process_ms,
                        'received_at': r.received_at.isoformat() if r.received_at else None,
                    }
                    for r in rows
                ]
        except SQLAlchemyError as e:
            logger.error("Error reading journal: %s: %s", type(e).__name__, e)
            return []

    def journal_stats(self) -> Dict[str, Any]:
        """Counts by provenance, plus processing-latency percentiles."""
        try:
            with self._session() as db:
                by_prov = {}
                for prov in Provenance.ALL:
                    by_prov[prov] = db.query(EventJournal).filter_by(provenance=prov).count()
                durations = [
                    r[0] for r in db.query(EventJournal.process_ms)
                    .filter(EventJournal.process_ms.isnot(None)).all()
                ]
            return {'by_provenance': by_prov, 'latency_ms': _percentiles(durations)}
        except SQLAlchemyError as e:
            logger.error("Error reading journal stats: %s: %s", type(e).__name__, e)
            return {'by_provenance': {}, 'latency_ms': {}}

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
        is_valid: bool = True,
    ) -> bool:
        """Store an extracted code. False when the code was already recorded.

        is_valid=False marks a code as needing review rather than confirmed —
        used for OCR readings, which are one wrong glyph away from a wrong code
        and must not be treated as certain.
        """
        try:
            code_hash = hashlib.sha256(code.encode()).hexdigest()
            with self._session() as db:
                db.add(ExtractedCode(
                    message_id=message_id,
                    code=code,
                    code_hash=code_hash,
                    is_valid=is_valid,
                    entropy=entropy,
                    pattern_score=pattern_score,
                    ocr_confidence=ocr_confidence,
                ))
            logger.info("Code saved: %s (valid=%s)", code[:20], is_valid)
            return True
        except IntegrityError:
            logger.warning("Code already recorded: %s", code)
            return False
        except SQLAlchemyError as e:
            logger.error("Error saving code: %s: %s", type(e).__name__, e)
            return False

    def save_codes(self, message_id: int, codes: List[Dict[str, Any]]) -> int:
        """Insert several codes in a single transaction.

        One commit for the batch instead of one per code — the write-coalescing
        principle behind batched telemetry transports. Duplicates are skipped
        individually without failing the batch.
        """
        if not codes:
            return 0
        # Deduplicate within the batch, then drop codes already stored — one
        # SELECT and one INSERT instead of a try/except commit per code. A
        # per-row rollback would abandon the whole transaction, losing the codes
        # already added; filtering up front avoids that entirely.
        by_hash: Dict[str, Dict[str, Any]] = {}
        for c in codes:
            by_hash[hashlib.sha256(c['code'].encode()).hexdigest()] = c
        try:
            with self._session() as db:
                existing = {
                    row[0] for row in db.query(ExtractedCode.code_hash)
                    .filter(ExtractedCode.code_hash.in_(list(by_hash)))
                    .all()
                }
                written = 0
                for h, c in by_hash.items():
                    if h in existing:
                        continue
                    db.add(ExtractedCode(
                        message_id=message_id,
                        code=c['code'],
                        code_hash=h,
                        is_valid=c.get('is_valid', True),
                        entropy=c.get('entropy'),
                        pattern_score=c.get('pattern_score'),
                        ocr_confidence=c.get('ocr_confidence'),
                    ))
                    written += 1
            return written
        except SQLAlchemyError as e:
            logger.error("Error bulk-saving codes: %s: %s", type(e).__name__, e)
            return 0

    def bulk_log(self, entries: List[Dict[str, Any]]) -> bool:
        """Write many audit-log rows in one commit (the Clearcut batch pattern)."""
        if not entries:
            return True
        try:
            with self._session() as db:
                for e in entries:
                    db.add(SystemLog(
                        level=e.get('level', 'INFO'),
                        component=e.get('component', 'backend'),
                        message=e.get('message', ''),
                        context=e.get('context') or {},
                    ))
            return True
        except SQLAlchemyError as e:
            logger.error("Error bulk-logging: %s: %s", type(e).__name__, e)
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
                        'needs_review': not r.is_valid,
                        'extracted_at': r.extracted_at.isoformat() if r.extracted_at else None,
                    }
                    for r in rows
                ]
        except SQLAlchemyError as e:
            logger.error("Error listing codes: %s: %s", type(e).__name__, e)
            return []

    def get_review_codes(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Codes awaiting human review — OCR readings, mostly."""
        return self.get_codes(limit=limit, valid_only=False)

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


def _percentiles(values: List[float]) -> Dict[str, Any]:
    """p50/p95/p99 over processing durations. Counts alone hide tail latency."""
    if not values:
        return {'count': 0}
    ordered = sorted(values)

    def pick(p: float) -> float:
        # Nearest-rank: ceil(p * n) - 1. Interpolating over (n-1) shifts the
        # median up by one sample, reporting a worse p50 than reality.
        idx = max(0, min(math.ceil(p * len(ordered)) - 1, len(ordered) - 1))
        return round(ordered[idx], 2)

    return {
        'count': len(ordered),
        'p50': pick(0.50),
        'p95': pick(0.95),
        'p99': pick(0.99),
        'max': round(ordered[-1], 2),
    }
