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
import re
import time
import json
import psutil
import hashlib
from collections import OrderedDict, deque
from pathlib import Path
import asyncio
import aiohttp
from functools import wraps

from ..models.database import init_db
from ..core.persistence import get_persistence
from ..core.codefilter import extract_codes, shannon_entropy
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

# Global instances
ocr_cache = OCRCache()
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

    _boot_settings = get_settings()
    setup_logging(
        level=_boot_settings.log_level,
        log_file=_boot_settings.log_file,
        component='pinlives.backend',
    )
    logger.info("Starting PINLIVES Pro v3.1 backend")

    init_db()
    persistence = get_persistence()
    logger.info("Database ready")

    _background_tasks.append(asyncio.create_task(message_processor()))

    # The Telethon client runs in this process so the login endpoints can drive it.
    settings = get_settings()
    telethon_service = TelethonService(
        api_id=settings.telethon_api_id,
        api_hash=settings.telethon_api_hash,
        persistence=persistence,
        on_message=_enqueue_from_telethon,
        otp_timeout_seconds=settings.otp_timeout_seconds,
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
    """Persist one message. Raises on failure so the caller can retry."""
    if not persistence:
        raise RuntimeError("Persistence not initialized")

    phone = payload.get('phone')
    if not phone:
        raise ValueError("Message payload is missing 'phone'")

    msg_id = persistence.save_message(
        phone=phone,
        chat_id=payload.get('chat_id'),
        chat_name=payload.get('chat_name'),
        message_id=payload.get('message_id'),
        text=payload.get('text'),
        has_media=payload.get('has_media', False),
        media_path=payload.get('media_path'),
    )

    # A silent None here is how messages used to disappear while the API still
    # reported success. Fail loudly so the retry path runs.
    if msg_id is None:
        raise RuntimeError(
            f"save_message returned no row id for phone={phone} chat={payload.get('chat_id')}"
        )

    text = payload.get('text')
    if text:
        for code in extract_codes(text):
            persistence.save_code(msg_id, code, entropy=shannon_entropy(code))

    persistence.log_event(
        'INFO', 'backend', f"Message stored: {payload.get('chat_name')}",
        {'chat_id': payload.get('chat_id'), 'message_id': payload.get('message_id')},
    )

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
    return {
        'status': 'running',
        'version': '3.1',
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
    """Most recently extracted codes."""
    _require_ready()
    return {'success': True, 'codes': persistence.get_codes(limit=limit)}


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
