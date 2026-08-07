"""
PINLIVES Pro v3.1 - Enhanced Telethon Listener Daemon
Production-grade with persistence, error handling, and monitoring
"""

import logging
import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError
import aiohttp

from ..core.config import Settings
from ..core.persistence import get_persistence

# ============================================================================
# CONFIGURATION
# ============================================================================

settings = Settings()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(settings.log_file),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============================================================================
# GLOBAL STATE
# ============================================================================

class DaemonState:
    def __init__(self):
        self.client = None
        self.session_string = None
        self.phone = None
        self.authenticated = False
        self.channels = []
        self.messages_received = 0
        self.last_message_time = datetime.now()
        self.aiohttp_session = None

listener_state = DaemonState()

# ============================================================================
# SESSION MANAGEMENT
# ============================================================================

async def init_aiohttp():
    """Initialize aiohttp session for backend communication"""
    listener_state.aiohttp_session = aiohttp.ClientSession()
    logger.info("✓ Aiohttp session initialized")

async def close_aiohttp():
    """Close aiohttp session"""
    if listener_state.aiohttp_session:
        try:
            await listener_state.aiohttp_session.close()
            logger.info("✓ Aiohttp session closed")
        except Exception as e:
            logger.error(f"Error closing aiohttp: {e}")

# ============================================================================
# TELETHON LOGIN FLOW
# ============================================================================

async def start_login(phone: str) -> str:
    """Start login flow - returns result message"""
    persistence = get_persistence()
    try:
        logger.info(f"Starting login for {phone}...")

        # Try to load existing session from database
        session_string = persistence.load_session(phone)

        if session_string:
            logger.info(f"Attempting to restore session from DB: {phone}...")
            try:
                client = TelegramClient(
                    StringSession(session_string),
                    settings.telethon_api_id,
                    settings.telethon_api_hash
                )
                await client.connect()

                if await client.is_user_authorized():
                    listener_state.client = client
                    listener_state.session_string = session_string
                    listener_state.phone = phone
                    listener_state.authenticated = True

                    await setup_listeners()
                    persistence.log_event('INFO', 'daemon', f'Session restored: {phone}')
                    logger.info(f"✓ Logged in with existing session: {phone}")
                    return f"✓ LOGGED IN (existing session)"
                else:
                    await client.disconnect()
                    logger.warning(f"Existing session invalid/expired for {phone}")
            except Exception as e:
                logger.warning(f"Failed to use existing session: {type(e).__name__}: {e}")
                try:
                    await client.disconnect()
                except:
                    pass

        # Create new session and request code
        logger.info(f"Creating new session for {phone}...")
        client = TelegramClient(StringSession(), settings.telethon_api_id, settings.telethon_api_hash)
        await client.connect()

        await client.send_code_request(phone)
        listener_state.client = client
        listener_state.phone = phone

        logger.info(f"✓ Code sent to {phone}, awaiting OTP...")
        persistence.log_event('INFO', 'daemon', f'Login started: {phone}')

        return f"✓ CODE SENT - Awaiting OTP"

    except Exception as e:
        logger.error(f"❌ Login start error: {type(e).__name__}: {e}")
        persistence.log_event('ERROR', 'daemon', f'Login failed: {str(e)}')
        return f"❌ Error: {str(e)}"

async def verify_otp(otp: str) -> str:
    """Verify OTP and complete login"""
    persistence = get_persistence()
    if not listener_state.client or not listener_state.phone:
        logger.error("No client or phone set for OTP verification")
        return "❌ No login session in progress"

    try:
        logger.info(f"Verifying OTP for {listener_state.phone}...")
        user = await listener_state.client.sign_in(listener_state.phone, otp)
        logger.info(f"✓ Successfully signed in as {user.first_name}")

        # Save session to database
        session_string = listener_state.client.session.save()
        if not persistence.save_session(listener_state.phone, session_string):
            logger.error("Failed to save session to database")
            return "❌ Session save failed"

        # Mark as authenticated in database
        persistence.mark_authenticated(listener_state.phone)

        listener_state.session_string = session_string
        listener_state.authenticated = True

        await setup_listeners()
        persistence.log_event('INFO', 'daemon', f'Login verified: {listener_state.phone}')

        return f"✓ LOGIN SUCCESS\n\nUser: {user.first_name}\nListener starting..."

    except SessionPasswordNeededError:
        logger.error("2FA enabled but password mode not supported")
        persistence.log_event('ERROR', 'daemon', '2FA required (not supported)')
        return "❌ 2FA enabled (password mode not yet supported)"
    except Exception as e:
        logger.error(f"❌ OTP verification error: {type(e).__name__}: {e}")
        persistence.log_event('ERROR', 'daemon', f'OTP error: {str(e)}')
        return f"❌ Error: {str(e)}"

# ============================================================================
# MESSAGE LISTENING
# ============================================================================

async def setup_listeners():
    """Setup event listeners for incoming messages"""
    persistence = get_persistence()

    if not listener_state.client:
        logger.error("Client not initialized for listeners")
        return

    try:
        @listener_state.client.on(events.NewMessage)
        async def handler(event):
            """Handle each new message"""
            try:
                # Skip outgoing messages
                if event.out:
                    return

                message = event.message
                chat = await event.get_chat()
                chat_id = chat.id
                chat_name = chat.title or chat.first_name or f"Chat_{chat_id}"
                text = message.text or ""

                # Build payload
                payload = {
                    'phone': listener_state.phone,
                    'chat_id': chat_id,
                    'chat_name': chat_name,
                    'message_id': message.id,
                    'text': text,
                    'date': message.date.isoformat(),
                    'has_media': bool(message.media),
                }

                # Handle media if present
                if message.media:
                    try:
                        media_dir = Path('/tmp/telethon_media')
                        media_dir.mkdir(exist_ok=True)
                        media_path = media_dir / f"{chat_id}_{message.id}.jpg"

                        await message.download_media(file=str(media_path))

                        if media_path.exists():
                            payload['media_path'] = str(media_path)
                            logger.info(f"Downloaded media: {media_path}")
                        else:
                            logger.warning(f"Media download verification failed: {media_path}")
                    except Exception as e:
                        logger.warning(f"Media download failed: {type(e).__name__}: {e}")

                # Send to backend
                if listener_state.aiohttp_session:
                    try:
                        async with listener_state.aiohttp_session.post(
                            f'{settings.backend_url}/api/telethon/message',
                            json=payload,
                            timeout=aiohttp.ClientTimeout(total=10)
                        ) as resp:
                            if resp.status == 200:
                                listener_state.messages_received += 1
                                listener_state.last_message_time = datetime.now()
                                logger.info(f"✓ Message sent to backend: {chat_name} - {text[:50]}")
                            else:
                                logger.warning(f"Backend returned {resp.status}")
                    except asyncio.TimeoutError:
                        logger.error(f"Backend timeout sending message")
                    except aiohttp.ClientError as e:
                        logger.error(f"Backend connection error: {e}")
                    except Exception as e:
                        logger.error(f"Error sending to backend: {type(e).__name__}: {e}")
                else:
                    logger.warning("No aiohttp session for backend communication")

            except Exception as e:
                logger.error(f"Error in message handler: {type(e).__name__}: {e}")

        logger.info("✓ Listeners setup complete")
    except Exception as e:
        logger.error(f"Error setting up listeners: {type(e).__name__}: {e}")

async def add_channel(channel_id: int, channel_name: str = None) -> bool:
    """Add channel to monitoring list"""
    persistence = get_persistence()
    if not listener_state.client or not listener_state.authenticated or not listener_state.phone:
        logger.error("Client not authenticated for add_channel")
        return False

    try:
        entity = await listener_state.client.get_entity(channel_id)
        channel_name = channel_name or entity.title or f"Channel_{channel_id}"

        listener_state.channels.append(channel_id)
        persistence.add_channel(listener_state.phone, channel_id, channel_name)

        logger.info(f"✓ Added channel: {channel_name} ({channel_id})")
        persistence.log_event('INFO', 'daemon', f'Channel added: {channel_name}')
        return True
    except Exception as e:
        logger.error(f"Failed to add channel: {type(e).__name__}: {e}")
        persistence.log_event('ERROR', 'daemon', f'Failed to add channel: {str(e)}')
        return False

# ============================================================================
# STATUS & MONITORING
# ============================================================================

async def get_status() -> Dict:
    """Get listener status"""
    return {
        'authenticated': listener_state.authenticated,
        'phone': listener_state.phone or 'Not logged in',
        'channels': listener_state.channels,
        'messages_received': listener_state.messages_received,
        'last_message': listener_state.last_message_time.isoformat(),
        'timestamp': datetime.now().isoformat()
    }

# ============================================================================
# MAIN DAEMON
# ============================================================================

async def main():
    """Main daemon loop"""
    persistence = get_persistence()

    logger.info("=" * 80)
    logger.info("Starting PINLIVES Pro v3.1 Telethon Listener Daemon")
    logger.info("=" * 80)
    logger.info(f"Phone: {settings.telethon_phone}")
    logger.info(f"API ID: {settings.telethon_api_id}")
    logger.info(f"Backend: {settings.backend_url}")
    logger.info("=" * 80)

    await init_aiohttp()
    persistence.log_event('INFO', 'daemon', 'Daemon started')

    # Try to restore from database
    result = await start_login(settings.telethon_phone)
    logger.info(f"Startup result: {result}")

    if listener_state.authenticated:
        logger.info(f"✓ Authenticated on startup")
    else:
        logger.warning(f"⚠️ Not authenticated, waiting for login via API")

    # Keep daemon running
    try:
        if listener_state.client:
            logger.info("Running with Telethon client...")
            await listener_state.client.run_until_disconnected()
        else:
            logger.info("No client connected, waiting for API login...")
            while True:
                await asyncio.sleep(5)
                if listener_state.authenticated and listener_state.client:
                    logger.info("Client authenticated via API, starting listener...")
                    await listener_state.client.run_until_disconnected()
                    break

    except KeyboardInterrupt:
        logger.info("Shutdown requested")
        persistence.log_event('INFO', 'daemon', 'Shutdown requested')
    except asyncio.CancelledError:
        logger.info("Task cancelled")
    except Exception as e:
        logger.error(f"Daemon error: {type(e).__name__}: {e}")
        persistence.log_event('ERROR', 'daemon', f'Daemon error: {str(e)}')
    finally:
        if listener_state.client:
            try:
                await listener_state.client.disconnect()
            except:
                pass
        await close_aiohttp()
        logger.info("✓ Telethon listener daemon stopped")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {type(e).__name__}: {e}")
        print(f"FATAL ERROR: {e}", file=sys.stderr)
        sys.exit(1)
