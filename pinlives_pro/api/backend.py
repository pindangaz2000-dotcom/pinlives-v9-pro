"""
PINLIVES Pro v3.1 - FastAPI Backend
Production-grade with persistence, monitoring, security, and error handling
"""

from fastapi import FastAPI, HTTPException, Depends, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator
from typing import Optional, Dict, List, Any
from datetime import datetime, timedelta
import logging
import os
import re
import time
import json
import psutil
import hashlib
from collections import OrderedDict, deque, defaultdict
from pathlib import Path
import asyncio
import aiohttp
from functools import wraps

from ..models.database import init_db, Provenance
from ..core.persistence import get_persistence
from ..core.codefilter import extract_codes, shannon_entropy
from concurrent.futures import ThreadPoolExecutor as _TPE
from ..core.filters import extract_codes_for_site
from ..core.config import get_settings
from ..core.logging_setup import setup_logging
from ..core.telethon_client import TelethonService, TelethonError

# ============================================================================
# CONFIGURATION
# ============================================================================

logger = logging.getLogger(__name__)

app = FastAPI(
    title="PINLIVES Pro API",
    version="3.1",
    description="Production-grade Telegram code distribution system"
)

# ============================================================================
# PYDANTIC MODELS WITH VALIDATION
# ============================================================================

class TelethonLoginStartRequest(BaseModel):
    phone: str = Field(..., min_length=7, max_length=20, description="Phone number")

    @validator('phone')
    def validate_phone(cls, v):
        clean = re.sub(r'[\s\-().]', '', v)
        if not clean.isdigit() and not (clean.startswith('+') and clean[1:].isdigit()):
            raise ValueError(f'Invalid phone format: {v}')
        return clean

class TelethonLoginVerifyRequest(BaseModel):
    phone: str = Field(..., min_length=7, max_length=20)
    otp: str = Field(..., min_length=4, max_length=8)

    @validator('otp')
    def validate_otp(cls, v):
        clean = re.sub(r'[\s\-,_.]', '', v)
        if not clean.isdigit():
            raise ValueError('OTP must contain only digits')
        return clean

class TelethonPasswordRequest(BaseModel):
    password: str = Field(..., min_length=1, max_length=256)

class AddChannelRequest(BaseModel):
    phone: str = Field(..., min_length=7, max_length=20)
    # Telegram supergroup/channel IDs are negative (e.g. -1001234567890), so this
    # must not be constrained to positive values.
    channel_id: int
    channel_name: str = Field(..., min_length=1, max_length=255)

class MessagePayload(BaseModel):
    # phone identifies the receiving account; without it the message cannot be
    # attributed to a stored session and is silently dropped.
    phone: str = Field(..., min_length=7, max_length=20)
    chat_id: int
    chat_name: str
    message_id: int
    text: Optional[str] = None
    date: Optional[str] = None
    has_media: bool = False
    media_path: Optional[str] = None

# ============================================================================
# GLOBAL STATE & MANAGERS
# ============================================================================

class OCRCache:
    """Bounded OCR cache with LRU eviction"""
    def __init__(self, max_size: int = 500, ttl_seconds: int = 3600):
        self.cache = OrderedDict()
        self.metadata = {}
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds

    def get(self, key: str) -> Optional[str]:
        if key not in self.cache:
            return None
        entry = self.metadata[key]
        if time.time() - entry['created_at'] > self.ttl_seconds:
            del self.cache[key]
            del self.metadata[key]
            return None
        self.cache.move_to_end(key)
        return self.cache[key]

    def set(self, key: str, value: str):
        if key in self.cache:
            self.cache.move_to_end(key)
        else:
            if len(self.cache) >= self.max_size:
                oldest = next(iter(self.cache))
                del self.cache[oldest]
                del self.metadata[oldest]
            self.cache[key] = value
        self.metadata[key] = {'created_at': time.time()}

class MessageQueue:
    """Message queue with retry logic"""
    def __init__(self, max_size: int = 5000):
        self.queue = deque(maxlen=max_size)
        self.lock = asyncio.Lock()
        self.processing = False

    async def enqueue(self, data: Dict, attempt: int = 0):
        async with self.lock:
            self.queue.append({
                'data': data,
                'attempts': attempt,
                'timestamp': time.time()
            })

    async def dequeue(self) -> Optional[Dict]:
        async with self.lock:
            if self.queue:
                return self.queue.popleft()
            return None

    def size(self) -> int:
        return len(self.queue)

class SystemMonitor:
    """Monitor system health metrics"""
    def __init__(self):
        self.start_time = time.time()
        self.message_count = 0
        self.error_count = 0
        self.last_message_time = datetime.utcnow()

    def get_uptime(self) -> float:
        return time.time() - self.start_time

    def get_memory_usage(self) -> Dict[str, Any]:
        process = psutil.Process()
        memory_info = process.memory_info()
        return {
            'rss_mb': memory_info.rss / 1024 / 1024,
            'vms_mb': memory_info.vms / 1024 / 1024,
            'percent': process.memory_percent()
        }

    def get_cpu_usage(self) -> float:
        return psutil.cpu_percent(interval=0.1)

    def get_disk_usage(self) -> Dict[str, Any]:
        disk = psutil.disk_usage('/')
        return {
            'total_gb': disk.total / 1024**3,
            'used_gb': disk.used / 1024**3,
            'free_gb': disk.free / 1024**3,
            'percent': disk.percent
        }

def _build_identity() -> Dict[str, Any]:
    """Identify the running code so a screenshot maps to a commit.

    Several versions of this system have existed side by side; without this a
    bug report cannot be tied to the code that produced it.
    """
    import subprocess
    sha = os.environ.get('GIT_SHA', '')
    if not sha:
        try:
            sha = subprocess.check_output(
                ['git', 'rev-parse', '--short', 'HEAD'],
                cwd=Path(__file__).resolve().parents[2], stderr=subprocess.DEVNULL, timeout=5,
            ).decode().strip()
        except Exception:
            sha = 'unknown'
    return {
        'version': '3.1',
        'git_sha': sha,
        'schema_version': SCHEMA_VERSION,
        'environment': os.environ.get('ENVIRONMENT', 'development'),
        'started_at': datetime.utcnow().isoformat(),
    }


def _bot_user_id_from_token(token: Optional[str]) -> Optional[int]:
    """The bot's user id is the numeric part of "<id>:<secret>". None if malformed."""
    if not token or ':' not in token:
        return None
    head = token.split(':', 1)[0]
    return int(head) if head.isdigit() else None


SCHEMA_VERSION = 2
BUILD = None

class AuditBatcher:
    """Buffer INFO audit logs and flush in one write (the Clearcut batch model).

    ERROR-level events flush immediately — an operator needs failures now — while
    routine INFO lines are coalesced and shipped in bulk. Buffered INFO can be
    lost on a hard crash; that is the same bounded-loss tradeoff Clearcut makes
    for low-priority telemetry (its own metadata carries a LOG_LOSS counter).
    """
    def __init__(self, flush_size: int = 50):
        self._buf: List[Dict[str, Any]] = []
        self._flush_size = flush_size
        self.dropped = 0

    def add(self, level: str, component: str, message: str, context: Dict = None):
        entry = {'level': level, 'component': component, 'message': message,
                 'context': context or {}}
        if level == 'ERROR':
            # Priority path: flush the backlog and this event immediately.
            self._buf.append(entry)
            self.flush()
            return
        self._buf.append(entry)
        if len(self._buf) >= self._flush_size:
            self.flush()

    def flush(self):
        if not self._buf or persistence is None:
            return
        batch, self._buf = self._buf, []
        if not persistence.bulk_log(batch):
            self.dropped += len(batch)


class StageStats:
    """Per-stage latency accounting (the PRIMES instrumentation model).

    Counts alone hide where time goes; this attributes it to journal / extract /
    ocr / db so the slow stage is visible rather than guessed at."""
    def __init__(self):
        self.count: Dict[str, int] = defaultdict(int)
        self.total_ms: Dict[str, float] = defaultdict(float)
        self.max_ms: Dict[str, float] = defaultdict(float)

    def record(self, stage: str, ms: float):
        self.count[stage] += 1
        self.total_ms[stage] += ms
        self.max_ms[stage] = max(self.max_ms[stage], ms)

    def snapshot(self) -> Dict[str, Any]:
        return {
            stage: {
                'count': self.count[stage],
                'avg_ms': round(self.total_ms[stage] / self.count[stage], 2),
                'max_ms': round(self.max_ms[stage], 2),
            }
            for stage in self.count
        }


# Global instances
# A small pool: OCR is CPU-bound, and more threads than cores only thrash.
_ocr_pool = _TPE(max_workers=2)
OCR_ENABLED = os.environ.get('OCR_ENABLED', 'true').lower() == 'true'
ocr_cache = OCRCache()
audit = AuditBatcher()
stage_stats = StageStats()
message_queue = MessageQueue()
system_monitor = SystemMonitor()
persistence = None
telethon_service = None
_background_tasks = []

# ============================================================================
# STARTUP & SHUTDOWN
# ============================================================================

async def _enqueue_from_telethon(payload: Dict[str, Any]) -> None:
    """Hand a message from the listener to the durable queue."""
    await message_queue.enqueue(payload)


def _require_ready() -> None:
    """Reject requests that arrive before startup finished wiring things up."""
    if persistence is None:
        raise HTTPException(status_code=503, detail="Database not ready")
    if telethon_service is None:
        raise HTTPException(status_code=503, detail="Telethon service not ready")


@app.on_event("startup")
async def startup():
    global persistence, telethon_service

    global BUILD
    BUILD = _build_identity()

    _boot_settings = get_settings()
    setup_logging(
        level=_boot_settings.log_level,
        log_file=_boot_settings.log_file,
        component='pinlives.backend',
    )
    logger.info("Starting PINLIVES Pro backend %s", BUILD)

    init_db()
    persistence = get_persistence()
    logger.info("Database ready")

    _background_tasks.append(asyncio.create_task(message_processor()))

    # The Telethon client runs in this process so the login endpoints can drive it.
    settings = get_settings()
    # A bot token is "<bot_user_id>:<secret>"; the numeric prefix is the bot's
    # own Telegram user id. Passing it arms the guard that drops the bot's own
    # posts, so a detection notice the bot emits is never re-ingested as a code.
    bot_user_id = _bot_user_id_from_token(settings.bot_token)
    sink_chat_ids = {settings.notify_chat_id} if settings.notify_chat_id else set()
    telethon_service = TelethonService(
        api_id=settings.telethon_api_id,
        api_hash=settings.telethon_api_hash,
        persistence=persistence,
        on_message=_enqueue_from_telethon,
        otp_timeout_seconds=settings.otp_timeout_seconds,
        bot_user_id=bot_user_id,
        sink_chat_ids=sink_chat_ids,
    )

    # Reconnect without operator involvement when a session survives a restart.
    try:
        if await telethon_service.restore(settings.telethon_phone):
            logger.info("Reconnected to Telegram from the stored session")
        else:
            logger.info("No usable stored session; waiting for a login")
    except Exception as e:
        logger.error("Session restore failed: %s: %s", type(e).__name__, e)

    logger.info("Startup complete")


@app.on_event("shutdown")
async def shutdown():
    audit.flush()
    for task in _background_tasks:
        task.cancel()
    if telethon_service:
        try:
            await telethon_service.logout()
        except Exception as e:
            logger.error("Error disconnecting Telethon: %s", e)
    logger.info("Shutdown complete")

# ============================================================================
# MESSAGE PROCESSING BACKGROUND TASK
# ============================================================================

MAX_ATTEMPTS = 3
RETRY_BASE_DELAY = 2.0


async def _requeue_after_delay(payload: Dict, attempt: int, delay: float) -> None:
    """Put a failed message back after a delay, without stalling the processor."""
    try:
        await asyncio.sleep(delay)
        await message_queue.enqueue(payload, attempt)
    except asyncio.CancelledError:
        logger.warning("Retry cancelled during shutdown, message dropped: %s", payload.get('message_id'))


async def message_processor():
    """Drain the queue, retrying failures with exponential backoff."""
    logger.info("Starting message processor...")
    while True:
        try:
            item = await message_queue.dequeue()
            if not item:
                # Idle: ship any buffered audit logs so they are not held
                # indefinitely at low volume.
                audit.flush()
                await asyncio.sleep(0.5)
                continue

            payload = item['data']
            attempt = item['attempts']

            try:
                await process_message(payload)
                system_monitor.message_count += 1
                system_monitor.last_message_time = datetime.utcnow()

            except Exception as e:
                system_monitor.error_count += 1
                logger.error(
                    "Error processing message (attempt %s/%s): %s: %s",
                    attempt, MAX_ATTEMPTS, type(e).__name__, e,
                )

                if attempt < MAX_ATTEMPTS:
                    # Without a growing delay all retries burn off in milliseconds
                    # and a transient fault never gets a chance to clear.
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
                    logger.info(
                        "Retrying message %s in %.0fs (attempt %s/%s)",
                        payload.get('message_id'), delay, attempt + 1, MAX_ATTEMPTS,
                    )
                    _background_tasks.append(
                        asyncio.create_task(_requeue_after_delay(payload, attempt + 1, delay))
                    )
                else:
                    # Dead letter: keep the payload so nothing disappears silently.
                    logger.error("Message dropped after %s attempts: %s", MAX_ATTEMPTS, payload)
                    if persistence:
                        persistence.log_event(
                            'ERROR', 'backend',
                            f"Message dropped after {MAX_ATTEMPTS} attempts",
                            {
                                'phone': payload.get('phone'),
                                'chat_id': payload.get('chat_id'),
                                'message_id': payload.get('message_id'),
                                'text': (payload.get('text') or '')[:500],
                                'last_error': f"{type(e).__name__}: {e}",
                            },
                        )

        except asyncio.CancelledError:
            logger.info("Message processor stopping")
            raise
        except Exception as e:
            logger.error(f"Message processor error: {type(e).__name__}: {e}")
            await asyncio.sleep(5)

async def process_message(payload: Dict) -> None:
    """Persist one message and extract its codes. Raises so the caller retries."""
    if not persistence:
        raise RuntimeError("Persistence not initialized")

    phone = payload.get('phone')
    if not phone:
        raise ValueError("Message payload is missing 'phone'")

    started = time.perf_counter()

    # Resolve the site before journalling: replay reads site_id back from the
    # journal, so recording it empty would make a replay run a different
    # extractor than the live pass it is meant to reproduce.
    site_id = payload.get('site_id')
    if site_id is None:
        site_id = persistence.get_channel_site(phone, payload.get('chat_id'))
    payload = {**payload, 'site_id': site_id}

    # Journal + store the message in one transaction: both are up-front inserts
    # with no slow work between them, so a single commit replaces the two that
    # PRIMES-style timing showed were the pipeline's dominant cost.
    t_persist = time.perf_counter()
    journal_id, msg_id = persistence.journal_and_store_message(
        payload, provenance=payload.get('provenance', Provenance.REAL)
    )
    stage_stats.record('journal+store', (time.perf_counter() - t_persist) * 1000)

    # A silent None here is how messages used to disappear while the API still
    # reported success. Fail loudly so the retry path runs.
    if msg_id is None:
        raise RuntimeError(
            f"save_message returned no row id for phone={phone} chat={payload.get('chat_id')}"
        )

    text = payload.get('text') or ''
    t_extract = time.perf_counter()
    found = extract_codes_for_text(text, site_id)
    stage_stats.record('extract', (time.perf_counter() - t_extract) * 1000)

    # One bulk insert for the message's text codes, not a commit per code.
    if found:
        t_db = time.perf_counter()
        persistence.save_codes(
            msg_id, [{'code': c, 'entropy': shannon_entropy(c)} for c in found]
        )
        stage_stats.record('save_codes', (time.perf_counter() - t_db) * 1000)

    # Codes in an image. OCR is one wrong glyph from a wrong code, so these are
    # stored as needs-review (is_valid=False), never as confirmed alongside text
    # codes. They surface at /api/codes/review, not /api/codes.
    media_path = payload.get('media_path')
    if media_path and OCR_ENABLED:
        try:
            t_ocr = time.perf_counter()
            ocr_codes = await extract_codes_from_media(media_path, site_id)
            stage_stats.record('ocr', (time.perf_counter() - t_ocr) * 1000)
            if ocr_codes:
                persistence.save_codes(msg_id, [
                    {'code': c, 'ocr_confidence': conf, 'is_valid': False}
                    for c, conf in ocr_codes
                ])
                logger.info("OCR queued %d code(s) for review from %s",
                            len(ocr_codes), media_path)
        except Exception as e:
            # OCR failure must not lose the message; the text codes are stored.
            logger.error("OCR failed for %s: %s: %s", media_path, type(e).__name__, e)

    # Batched audit line instead of a commit-per-message log write.
    audit.add('INFO', 'backend', f"stored {payload.get('chat_name')}",
              {'codes': len(found), 'site': site_id})

    elapsed_ms = (time.perf_counter() - started) * 1000
    stage_stats.record('total', elapsed_ms)
    if journal_id:
        t_mark = time.perf_counter()
        persistence.mark_journal_processed(journal_id, elapsed_ms)
        stage_stats.record('mark_processed', (time.perf_counter() - t_mark) * 1000)


async def extract_codes_from_media(media_path: str, site_id: Optional[str]) -> List[tuple]:
    """OCR an image and return (code, confidence) pairs the site format accepts.

    Runs in a thread: tesseract is blocking and would otherwise stall the queue.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_ocr_pool, _ocr_blocking, media_path, site_id)


def _ocr_blocking(media_path: str, site_id: Optional[str]) -> List[tuple]:
    from ..core.ocr import get_ocr, confusion_candidates

    def accepts(token: str) -> bool:
        # A candidate is a code only if the site's own filter would keep it.
        if site_id:
            return bool(extract_codes_for_text(token, site_id))
        return bool(extract_codes(token))

    ocr = get_ocr()
    result = ocr.extract(media_path, validator=accepts)

    out: List[tuple] = []
    seen = set()
    for candidate in result.codes:
        if candidate.code not in seen:
            seen.add(candidate.code)
            out.append((candidate.code, candidate.confidence))
        # A near-miss the site rejects can still be the true code with one
        # confusable glyph swapped; offer those the site accepts, at lower rank.
        for alt in confusion_candidates(candidate.code, max_swaps=1):
            if alt not in seen and accepts(alt):
                seen.add(alt)
                out.append((alt, candidate.confidence * 0.75))
    return out

    persistence.log_event(
        'INFO', 'backend', f"Message stored: {payload.get('chat_name')}",
        {
            'chat_id': payload.get('chat_id'),
            'message_id': payload.get('message_id'),
            'site_id': site_id,
            'codes': len(found),
            'ms': round(elapsed_ms, 1),
        },
    )


def extract_codes_for_text(text: str, site_id: Optional[str]) -> List[str]:
    """Route to the site's extractor, falling back to the generic filter."""
    if not text:
        return []
    if site_id:
        try:
            return extract_codes_for_site(text, site_id, source='text')
        except Exception as e:
            logger.error("Site extractor %s failed: %s: %s", site_id, type(e).__name__, e)
    return extract_codes(text)

# ============================================================================
# HEALTH CHECK & MONITORING ENDPOINTS
# ============================================================================

@app.get("/api/health")
async def health_check() -> Dict[str, Any]:
    """Health probe. Reports degraded rather than healthy when a dependency is down."""
    db_ok = persistence.health_check() if persistence else False
    telethon_state = telethon_service.state if telethon_service else 'not_initialized'

    # The database is the only hard dependency: without it messages are lost.
    # Being signed out is an expected state an operator can fix, not an outage.
    status_value = 'healthy' if db_ok else 'degraded'

    return {
        'status': status_value,
        'build': BUILD or {},
        'timestamp': datetime.utcnow().isoformat(),
        'uptime_seconds': system_monitor.get_uptime(),
        'database': 'connected' if db_ok else 'disconnected',
        'telethon_state': telethon_state,
        'telethon_authenticated': telethon_service.authenticated if telethon_service else False,
        'memory': system_monitor.get_memory_usage(),
        'cpu_percent': system_monitor.get_cpu_usage(),
        'disk': system_monitor.get_disk_usage(),
        'queue_depth': message_queue.size(),
        'message_count': system_monitor.message_count,
        'error_count': system_monitor.error_count,
        'cache_size': len(ocr_cache.cache),
    }

@app.get("/api/status")
async def status() -> Dict[str, Any]:
    """Operational status, including counts read back from the database."""
    stored_messages = persistence.count_messages() if persistence else 0
    stored_codes = persistence.count_codes() if persistence else 0
    journal = persistence.journal_stats() if persistence else {}
    return {
        'status': 'running',
        'build': BUILD or {},
        'journal': journal,
        'stage_latency_ms': stage_stats.snapshot(),
        'audit_logs_dropped': audit.dropped,
        'uptime_seconds': system_monitor.get_uptime(),
        'messages_processed': system_monitor.message_count,
        'messages_stored': stored_messages,
        'codes_extracted': stored_codes,
        'errors': system_monitor.error_count,
        'queue_depth': message_queue.size(),
        'telethon': telethon_service.status() if telethon_service else {'state': 'not_initialized'},
        'last_message': system_monitor.last_message_time.isoformat(),
        'timestamp': datetime.utcnow().isoformat()
    }

@app.get("/api/metrics")
async def get_metrics() -> Dict[str, Any]:
    """Get detailed system metrics"""
    return {
        'timestamp': datetime.utcnow().isoformat(),
        'uptime': system_monitor.get_uptime(),
        'memory': system_monitor.get_memory_usage(),
        'cpu': system_monitor.get_cpu_usage(),
        'disk': system_monitor.get_disk_usage(),
        'queue': {
            'depth': message_queue.size(),
            'max_size': message_queue.queue.maxlen
        },
        'cache': {
            'size': len(ocr_cache.cache),
            'max_size': ocr_cache.max_size
        },
        'messages': {
            'processed': system_monitor.message_count,
            'errors': system_monitor.error_count,
            'error_rate': (system_monitor.error_count / max(system_monitor.message_count, 1)) * 100
        }
    }

# ============================================================================
# TELETHON LOGIN ENDPOINTS
# ============================================================================

@app.post("/api/telethon/login/start")
async def telethon_login_start(request: TelethonLoginStartRequest):
    """Ask Telegram to send a login code to the account."""
    _require_ready()
    phone = request.phone
    try:
        result = await telethon_service.start_login(phone)
    except TelethonError as e:
        persistence.log_event('ERROR', 'backend', f'Login start failed for {phone}: {e}')
        system_monitor.error_count += 1
        raise HTTPException(status_code=400, detail=str(e))

    persistence.log_event('INFO', 'backend', f'Login started: {phone}')
    return {
        'success': True,
        'phone': phone,
        'state': result['state'],
        'message': result['message'],
        'authenticated': telethon_service.authenticated,
    }


@app.post("/api/telethon/login/verify")
async def telethon_login_verify(request: TelethonLoginVerifyRequest):
    """Complete sign-in with the code Telegram sent."""
    _require_ready()
    try:
        result = await telethon_service.verify_code(request.otp)
    except TelethonError as e:
        persistence.log_event('ERROR', 'backend', f'Login verify failed: {e}')
        system_monitor.error_count += 1
        raise HTTPException(status_code=400, detail=str(e))

    persistence.log_event('INFO', 'backend', f'Login verified: {request.phone}')
    return {
        'success': True,
        'state': result['state'],
        'message': result['message'],
        'authenticated': telethon_service.authenticated,
    }


@app.post("/api/telethon/login/password")
async def telethon_login_password(request: TelethonPasswordRequest):
    """Finish sign-in for an account protected by a 2FA password."""
    _require_ready()
    try:
        result = await telethon_service.verify_password(request.password)
    except TelethonError as e:
        system_monitor.error_count += 1
        raise HTTPException(status_code=400, detail=str(e))
    return {
        'success': True,
        'state': result['state'],
        'message': result['message'],
        'authenticated': telethon_service.authenticated,
    }


@app.get("/api/telethon/state")
async def telethon_state():
    """Where the account currently sits in the login flow."""
    _require_ready()
    return telethon_service.status()


@app.get("/api/telethon/dialogs")
async def telethon_dialogs(limit: int = 50):
    """Channels and groups the signed-in account can see."""
    _require_ready()
    try:
        return {'success': True, 'dialogs': await telethon_service.list_dialogs(limit)}
    except TelethonError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/api/telethon/channel/add")
async def add_channel(request: AddChannelRequest):
    """Register a channel after confirming the account can actually read it."""
    _require_ready()

    # Resolving first means an unreadable channel is rejected here rather than
    # silently producing a monitored channel that never yields a message.
    channel_name = request.channel_name
    if telethon_service.authenticated:
        try:
            channel_name = await telethon_service.resolve_channel(request.channel_id)
        except TelethonError as e:
            raise HTTPException(status_code=409, detail=str(e))

    if not persistence.add_channel(request.phone, request.channel_id, channel_name):
        raise HTTPException(
            status_code=409,
            detail=f"Channel not added: unknown account {request.phone} or already monitored",
        )

    persistence.log_event('INFO', 'backend', f'Channel added: {channel_name}')
    return {'success': True, 'channel_name': channel_name, 'message': f'Added {channel_name}'}


@app.get("/api/channels")
async def list_channels(phone: str):
    """Channels registered for an account."""
    _require_ready()
    return {'success': True, 'channels': persistence.get_channels(phone)}


@app.get("/api/codes")
async def list_codes(limit: int = 50):
    """Confirmed codes (from text). OCR codes are not here — see /api/codes/review."""
    _require_ready()
    return {'success': True, 'codes': persistence.get_codes(limit=limit)}


@app.get("/api/codes/review")
async def list_review_codes(limit: int = 50):
    """Codes awaiting review — OCR readings that may be one glyph wrong."""
    _require_ready()
    return {'success': True, 'codes': persistence.get_review_codes(limit=limit)}


@app.get("/api/logs")
async def list_logs(limit: int = 20, level: Optional[str] = None):
    """Recent audit-log entries."""
    _require_ready()
    return {'success': True, 'logs': persistence.get_logs(limit=limit, level=level)}

# ============================================================================
# MESSAGE INGESTION ENDPOINT
# ============================================================================

@app.post("/api/telethon/message")
async def receive_message(payload: MessagePayload):
    """Accept a message for processing.

    Used by external producers and by the test suite; the in-process listener
    enqueues directly. The response says the message was queued, not stored —
    persistence failures surface through /api/status error counts and the logs.
    """
    _require_ready()
    await message_queue.enqueue(payload.model_dump())
    return {
        'success': True,
        'message_id': payload.message_id,
        'queued': True,
        'queue_depth': message_queue.size(),
    }

# ============================================================================
# REPLAY & BACKFILL
# ============================================================================

class ReplayRequest(BaseModel):
    limit: int = Field(50, ge=1, le=5000)
    chat_id: Optional[int] = None
    site_id: Optional[str] = None


@app.get("/api/journal")
async def list_journal(limit: int = 50, chat_id: Optional[int] = None,
                       provenance: Optional[str] = None):
    """Recorded events. This is the replay source."""
    _require_ready()
    return {'success': True, 'events': persistence.get_journal(limit, chat_id, provenance)}


@app.post("/api/replay")
async def replay(request: ReplayRequest):
    """Re-run extraction over journalled events and report what changed.

    This is how a filter or OCR change is measured: against real recorded
    traffic, without waiting for channels to post again. It does not write
    codes; it reports what the current extractor would find.
    """
    _require_ready()
    events = persistence.get_journal(limit=request.limit, chat_id=request.chat_id)

    started = time.perf_counter()
    per_event = []
    durations = []
    total_codes = 0

    for event in events:
        site = request.site_id or event.get('site_id') or ''
        t0 = time.perf_counter()
        codes = extract_codes_for_text(event.get('text') or '', site)
        took = (time.perf_counter() - t0) * 1000
        durations.append(took)
        total_codes += len(codes)
        per_event.append({
            'journal_id': event['id'],
            'chat_id': event['chat_id'],
            'message_id': event['message_id'],
            'site_id': site,
            'codes': codes,
            'ms': round(took, 3),
        })

    from ..core.persistence import _percentiles
    return {
        'success': True,
        'events_replayed': len(events),
        'codes_found': total_codes,
        'wall_ms': round((time.perf_counter() - started) * 1000, 1),
        'per_event_ms': _percentiles(durations),
        'results': per_event,
    }


class BackfillRequest(BaseModel):
    chat_id: Optional[int] = None
    per_channel: int = Field(5, ge=1, le=100)


@app.post("/api/backfill")
async def backfill(request: BackfillRequest):
    """Pull recent history from configured channels into the journal.

    Gives the pipeline real traffic to be measured against on a fresh install,
    instead of waiting for the next live post.
    """
    _require_ready()
    if not telethon_service.authenticated:
        raise HTTPException(status_code=409, detail="Not signed in to Telegram")

    targets = (
        [request.chat_id] if request.chat_id
        else sorted(telethon_service.allowed_channels)
    )
    if not targets:
        raise HTTPException(status_code=409, detail="No configured channels")

    summary = []
    for chat_id in targets:
        try:
            fetched = await telethon_service.fetch_history(chat_id, request.per_channel)
        except TelethonError as e:
            summary.append({'chat_id': chat_id, 'error': str(e)})
            continue
        for payload in fetched:
            payload['provenance'] = Provenance.BACKFILL
            await message_queue.enqueue(payload)
        summary.append({'chat_id': chat_id, 'queued': len(fetched)})

    return {'success': True, 'channels': summary, 'queue_depth': message_queue.size()}


# ============================================================================
# ERROR HANDLERS
# ============================================================================

def _audit(level: str, message: str) -> None:
    """Write to the audit log when it exists. Never raises from a handler."""
    if persistence is None:
        return
    try:
        persistence.log_event(level, 'backend', message)
    except Exception as e:
        logger.error("Could not write audit log: %s: %s", type(e).__name__, e)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    _audit('WARNING', f'HTTP {exc.status_code} on {request.url.path}: {exc.detail}')
    return JSONResponse(
        status_code=exc.status_code,
        content={'error': exc.detail, 'timestamp': datetime.utcnow().isoformat()}
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {type(exc).__name__}: {exc}")
    _audit('ERROR', f'Unhandled {type(exc).__name__} on {request.url.path}: {exc}')
    system_monitor.error_count += 1
    return JSONResponse(
        status_code=500,
        content={'error': 'Internal server error', 'timestamp': datetime.utcnow().isoformat()}
    )

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8000)
