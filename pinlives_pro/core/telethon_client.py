"""
PINLIVES Pro v3.1 - Telethon service.

The Telethon client lives inside the backend process. Telethon and FastAPI are
both asyncio, so they share one event loop and the HTTP handlers can drive the
login flow directly. An earlier design ran the client in a separate container,
which left the login endpoints with no way to reach it — they returned success
without ever contacting Telegram.
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from telethon import TelegramClient, events
from telethon.errors import (
    ApiIdInvalidError,
    FloodWaitError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession

logger = logging.getLogger(__name__)


class LoginState:
    """Where an account is in the login flow."""
    LOGGED_OUT = "logged_out"
    AWAITING_CODE = "awaiting_code"
    AWAITING_PASSWORD = "awaiting_password"
    AUTHENTICATED = "authenticated"


class TelethonError(Exception):
    """A login or listening failure with a message safe to show the operator."""


# Telethon retries a failed connection forever by default. Left unbounded, a
# network problem wedges the HTTP request that triggered it and, because the
# login lock is still held, every later login attempt too.
CONNECT_TIMEOUT = 20.0
REQUEST_TIMEOUT = 30.0
LOCK_TIMEOUT = 45.0
CONNECTION_RETRIES = 2
REQUEST_RETRIES = 2


def _build_client(session, api_id: int, api_hash: str) -> TelegramClient:
    return TelegramClient(
        session,
        api_id,
        api_hash,
        connection_retries=CONNECTION_RETRIES,
        request_retries=REQUEST_RETRIES,
        timeout=int(CONNECT_TIMEOUT),
    )


async def _bounded(awaitable, timeout: float, what: str):
    """Run an awaitable under a deadline, reporting timeouts in operator terms."""
    try:
        return await asyncio.wait_for(awaitable, timeout=timeout)
    except asyncio.TimeoutError:
        raise TelethonError(f"{what} timed out after {timeout:.0f}s. Check network access to Telegram.")


class TelethonService:
    """Owns the Telethon client, the login flow, and the message subscription."""

    def __init__(
        self,
        api_id: int,
        api_hash: str,
        persistence,
        on_message: Optional[Callable[[Dict[str, Any]], Any]] = None,
        media_dir: str = "/tmp/telethon_media",
        otp_timeout_seconds: int = 600,
        bot_user_id: Optional[int] = None,
    ):
        self.api_id = api_id
        self.api_hash = api_hash
        self.persistence = persistence
        self.on_message = on_message
        self.media_dir = Path(media_dir)
        self.otp_timeout_seconds = otp_timeout_seconds

        # Only these chats are listened to; everything else is discarded.
        self.allowed_channels: set = set()
        # Our own bot, so its reports are not re-ingested as new codes.
        self.bot_user_id = bot_user_id
        self.self_user_id: Optional[int] = None

        self.ignored_unconfigured = 0
        self.ignored_own_bot = 0
        self.ignored_self = 0

        self.client: Optional[TelegramClient] = None
        self.phone: Optional[str] = None
        self.state: str = LoginState.LOGGED_OUT
        self.user_display: Optional[str] = None

        self._phone_code_hash: Optional[str] = None
        self._code_requested_at: Optional[float] = None
        self._listeners_ready = False
        self._lock = asyncio.Lock()

        self.messages_received = 0
        self.last_message_time: Optional[datetime] = None

    @asynccontextmanager
    async def _acquire(self):
        """Take the login lock, refusing rather than queueing behind a stuck call."""
        try:
            await asyncio.wait_for(self._lock.acquire(), timeout=LOCK_TIMEOUT)
        except asyncio.TimeoutError:
            raise TelethonError("Another login attempt is still running. Try again shortly.")
        try:
            yield
        finally:
            self._lock.release()

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    @property
    def authenticated(self) -> bool:
        return self.state == LoginState.AUTHENTICATED

    def status(self) -> Dict[str, Any]:
        remaining = None
        if self._code_requested_at and self.state == LoginState.AWAITING_CODE:
            remaining = max(0, int(self.otp_timeout_seconds - (time.time() - self._code_requested_at)))
        return {
            'state': self.state,
            'authenticated': self.authenticated,
            'phone': self.phone,
            'user': self.user_display,
            'listeners_ready': self._listeners_ready,
            'messages_received': self.messages_received,
            'last_message': self.last_message_time.isoformat() if self.last_message_time else None,
            'otp_seconds_remaining': remaining,
            'allowed_channels': sorted(self.allowed_channels),
            'ignored': {
                'unconfigured_chat': self.ignored_unconfigured,
                'own_bot': self.ignored_own_bot,
                'self': self.ignored_self,
            },
        }

    # ------------------------------------------------------------------
    # Session restore
    # ------------------------------------------------------------------

    async def restore(self, phone: str) -> bool:
        """Reconnect using a stored session. False when there is none or it expired."""
        session_string = self.persistence.load_session(phone)
        if not session_string:
            logger.info("No stored session for %s", phone)
            return False

        client = _build_client(StringSession(session_string), self.api_id, self.api_hash)
        try:
            await _bounded(client.connect(), CONNECT_TIMEOUT, "Connecting to Telegram")
            if not await _bounded(client.is_user_authorized(), REQUEST_TIMEOUT, "Authorization check"):
                logger.warning("Stored session for %s is no longer authorized", phone)
                await client.disconnect()
                return False

            me = await _bounded(client.get_me(), REQUEST_TIMEOUT, "Fetching account details")
            self.client = client
            self.phone = phone
            self.state = LoginState.AUTHENTICATED
            self.user_display = getattr(me, 'first_name', None) or phone
            self.self_user_id = getattr(me, 'id', None)
            await self._attach_listeners()
            self.persistence.mark_authenticated(phone)
            logger.info("Session restored for %s (%s)", phone, self.user_display)
            return True
        except Exception as e:
            logger.warning("Could not restore session for %s: %s: %s", phone, type(e).__name__, e)
            try:
                await client.disconnect()
            except Exception:
                pass
            return False

    # ------------------------------------------------------------------
    # Login flow
    # ------------------------------------------------------------------

    async def start_login(self, phone: str) -> Dict[str, Any]:
        """Send a login code, or report that a stored session already works."""
        async with self._acquire():
            if await self.restore(phone):
                return {'state': self.state, 'message': 'Already authenticated from stored session'}

            client = _build_client(StringSession(), self.api_id, self.api_hash)
            try:
                await _bounded(client.connect(), CONNECT_TIMEOUT, "Connecting to Telegram")
                sent = await _bounded(
                    client.send_code_request(phone), REQUEST_TIMEOUT, "Requesting a login code"
                )
            except PhoneNumberInvalidError:
                await self._safe_disconnect(client)
                raise TelethonError(f"Telegram rejected the phone number: {phone}")
            except ApiIdInvalidError:
                await self._safe_disconnect(client)
                raise TelethonError("TELETHON_API_ID / TELETHON_API_HASH are not a valid pair")
            except FloodWaitError as e:
                await self._safe_disconnect(client)
                raise TelethonError(f"Rate limited by Telegram, retry in {e.seconds}s")
            except TelethonError:
                await self._safe_disconnect(client)
                raise
            except Exception as e:
                await self._safe_disconnect(client)
                raise TelethonError(f"{type(e).__name__}: {e}")

            self.client = client
            self.phone = phone
            self._phone_code_hash = sent.phone_code_hash
            self._code_requested_at = time.time()
            self.state = LoginState.AWAITING_CODE
            logger.info("Login code sent to %s", phone)
            return {'state': self.state, 'message': f'Code sent to {phone}'}

    async def verify_code(self, code: str) -> Dict[str, Any]:
        """Complete sign-in with the code Telegram sent."""
        async with self._acquire():
            if self.state != LoginState.AWAITING_CODE or not self.client:
                raise TelethonError("No login is awaiting a code. Start the login flow first.")

            if self._code_expired():
                await self._reset()
                raise TelethonError("The login code expired. Start the login flow again.")

            try:
                me = await _bounded(
                    self.client.sign_in(
                        phone=self.phone, code=code, phone_code_hash=self._phone_code_hash
                    ),
                    REQUEST_TIMEOUT, "Signing in",
                )
            except PhoneCodeInvalidError:
                raise TelethonError("That code is not correct.")
            except PhoneCodeExpiredError:
                await self._reset()
                raise TelethonError("The login code expired. Start the login flow again.")
            except SessionPasswordNeededError:
                self.state = LoginState.AWAITING_PASSWORD
                raise TelethonError("This account has 2FA enabled. Send the password to finish.")
            except Exception as e:
                raise TelethonError(f"{type(e).__name__}: {e}")

            return await self._finish_login(me)

    async def verify_password(self, password: str) -> Dict[str, Any]:
        """Finish sign-in for an account protected by a 2FA password."""
        async with self._acquire():
            if self.state != LoginState.AWAITING_PASSWORD or not self.client:
                raise TelethonError("No login is awaiting a password.")
            try:
                me = await _bounded(
                    self.client.sign_in(password=password), REQUEST_TIMEOUT, "Signing in"
                )
            except Exception as e:
                raise TelethonError(f"{type(e).__name__}: {e}")
            return await self._finish_login(me)

    async def _finish_login(self, me) -> Dict[str, Any]:
        session_string = self.client.session.save()
        if not self.persistence.save_session(self.phone, session_string):
            # Authentication succeeded; only the durable copy failed. Keep running
            # in memory rather than forcing the operator through the flow again.
            logger.error("Authenticated but could not store the session for %s", self.phone)
        else:
            self.persistence.mark_authenticated(self.phone)

        self.state = LoginState.AUTHENTICATED
        self.user_display = getattr(me, 'first_name', None) or self.phone
        self.self_user_id = getattr(me, 'id', None)
        self._phone_code_hash = None
        self._code_requested_at = None
        await self._attach_listeners()
        logger.info("Authenticated as %s (%s)", self.user_display, self.phone)
        return {'state': self.state, 'message': f'Signed in as {self.user_display}'}

    def _code_expired(self) -> bool:
        if self._code_requested_at is None:
            return True
        return (time.time() - self._code_requested_at) > self.otp_timeout_seconds

    async def _reset(self):
        await self._safe_disconnect(self.client)
        self.client = None
        self.state = LoginState.LOGGED_OUT
        self._phone_code_hash = None
        self._code_requested_at = None
        self._listeners_ready = False

    @staticmethod
    async def _safe_disconnect(client):
        if client is None:
            return
        try:
            await client.disconnect()
        except Exception:
            pass

    async def logout(self) -> None:
        async with self._acquire():
            await self._reset()
            self.phone = None
            self.user_display = None

    # ------------------------------------------------------------------
    # Listening
    # ------------------------------------------------------------------

    def refresh_allowed_channels(self) -> int:
        """Reload the monitored-channel allowlist from storage. Returns its size."""
        if not self.phone:
            self.allowed_channels = set()
            return 0
        try:
            rows = self.persistence.get_channels(self.phone)
        except Exception as e:
            logger.error("Could not load the channel allowlist: %s: %s", type(e).__name__, e)
            return len(self.allowed_channels)
        self.allowed_channels = {r['channel_id'] for r in rows}
        logger.info("Monitoring %d configured channel(s)", len(self.allowed_channels))
        return len(self.allowed_channels)

    def _should_handle(self, chat_id: Optional[int], sender_id: Optional[int]) -> bool:
        """Whether a message belongs to the configured set.

        Two rejections matter beyond the allowlist. Messages from our own bot
        would feed its own reports back in as fresh codes, and messages the
        account sent itself (Saved Messages) are not channel traffic.
        """
        if sender_id is not None:
            if self.bot_user_id is not None and sender_id == self.bot_user_id:
                self.ignored_own_bot += 1
                return False
            if self.self_user_id is not None and sender_id == self.self_user_id:
                self.ignored_self += 1
                return False

        if chat_id is None or chat_id not in self.allowed_channels:
            self.ignored_unconfigured += 1
            return False
        return True

    async def _attach_listeners(self):
        """Subscribe to messages from the configured channels only."""
        if self._listeners_ready or not self.client:
            return

        self.refresh_allowed_channels()

        # The predicate runs inside Telethon's dispatch, so traffic from every
        # other chat is discarded on an O(1) set lookup before any work starts.
        def _wanted(event) -> bool:
            return self._should_handle(
                getattr(event, 'chat_id', None), getattr(event, 'sender_id', None)
            )

        @self.client.on(events.NewMessage(incoming=True, func=_wanted))
        async def _handler(event):
            try:
                await self._handle_message(event)
            except Exception as e:
                # One bad message must never take the subscription down.
                logger.error("Message handler failed: %s: %s", type(e).__name__, e)

        self._listeners_ready = True
        logger.info("Listening on %d configured channel(s)", len(self.allowed_channels))

    async def _handle_message(self, event):
        message = event.message

        # event.chat_id is the -100-prefixed form used everywhere else: what
        # get_entity accepts, what dialogs report, what operators paste in.
        # chat.id is the bare id and would never match a stored channel.
        chat_id = getattr(event, 'chat_id', None)
        sender_id = getattr(event, 'sender_id', None)

        # Checked again here: the allowlist can change between dispatch and now,
        # and this path is also reached from the backfill.
        if not self._should_handle(chat_id, sender_id):
            logger.debug("Ignoring message from chat %s", chat_id)
            return

        chat = await event.get_chat()
        chat_name = (
            getattr(chat, 'title', None)
            or getattr(chat, 'first_name', None)
            or f"Chat_{chat_id}"
        )

        payload: Dict[str, Any] = {
            'phone': self.phone,
            'chat_id': chat_id,
            'chat_name': chat_name,
            'message_id': message.id,
            'text': message.text or '',
            'date': message.date.isoformat() if message.date else None,
            'has_media': bool(message.media),
        }

        if message.media:
            try:
                self.media_dir.mkdir(parents=True, exist_ok=True)
                target = self.media_dir / f"{chat_id}_{message.id}"
                saved = await message.download_media(file=str(target))
                if saved and Path(saved).exists():
                    payload['media_path'] = str(saved)
                else:
                    logger.warning("Media download produced no file for %s/%s", chat_id, message.id)
            except Exception as e:
                logger.warning("Media download failed: %s: %s", type(e).__name__, e)

        self.messages_received += 1
        self.last_message_time = datetime.now()

        if self.on_message:
            await self.on_message(payload)

    async def resolve_channel(self, channel_id: int) -> Optional[str]:
        """Return a channel's title, confirming the account can actually see it."""
        if not self.client or not self.authenticated:
            raise TelethonError("Not authenticated.")
        try:
            entity = await self.client.get_entity(channel_id)
            return getattr(entity, 'title', None) or getattr(entity, 'first_name', None) or str(channel_id)
        except Exception as e:
            raise TelethonError(f"Cannot access channel {channel_id}: {type(e).__name__}: {e}")

    async def list_dialogs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Channels and groups the account belongs to, for picking what to monitor."""
        if not self.client or not self.authenticated:
            raise TelethonError("Not authenticated.")
        out: List[Dict[str, Any]] = []
        async for dialog in self.client.iter_dialogs(limit=limit):
            if dialog.is_channel or dialog.is_group:
                out.append({
                    'channel_id': dialog.id,
                    'channel_name': dialog.name,
                    'is_channel': dialog.is_channel,
                })
        return out

    async def fetch_history(self, chat_id: int, limit: int = 5) -> List[Dict[str, Any]]:
        """Recent posts from a channel, shaped like live events.

        Used to seed the journal so a fresh install has real traffic to measure
        against rather than waiting for the next post.
        """
        if not self.client or not self.authenticated:
            raise TelethonError("Not authenticated.")
        if chat_id not in self.allowed_channels:
            raise TelethonError(f"Channel {chat_id} is not configured.")

        out: List[Dict[str, Any]] = []
        try:
            async for message in self.client.iter_messages(chat_id, limit=limit):
                if self.bot_user_id and getattr(message, 'sender_id', None) == self.bot_user_id:
                    continue
                out.append({
                    'phone': self.phone,
                    'chat_id': chat_id,
                    'chat_name': f"Chat_{chat_id}",
                    'message_id': message.id,
                    'text': message.text or '',
                    'date': message.date.isoformat() if message.date else None,
                    'has_media': bool(message.media),
                })
        except Exception as e:
            raise TelethonError(f"Could not read history for {chat_id}: {type(e).__name__}: {e}")
        return out
