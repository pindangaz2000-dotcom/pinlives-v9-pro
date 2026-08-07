"""
PINLIVES Pro v3.1 - Telegram Bot Control Panel
Production-grade with commands, callbacks, and backend integration
"""

import logging
import asyncio
import time
import sys
import re
from datetime import datetime
from typing import Dict, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters, ConversationHandler
)
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
# CONVERSATION STATES
# ============================================================================

WAIT_LOGIN_PHONE = 1
WAIT_LOGIN_OTP = 2
WAIT_CHANNEL_ID = 3

# ============================================================================
# COMMAND HANDLERS
# ============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start command - show main menu"""
    persistence = get_persistence()

    if update.effective_user.id != settings.admin_id:
        await update.message.reply_text("❌ Unauthorized")
        persistence.log_event('WARNING', 'bot', f'Unauthorized access: {update.effective_user.id}')
        return

    keyboard = [
        [InlineKeyboardButton("🔐 Login", callback_data='login_start')],
        [InlineKeyboardButton("📊 Status", callback_data='status')],
        [InlineKeyboardButton("➕ Add Channel", callback_data='add_channel')],
        [InlineKeyboardButton("📋 Logs", callback_data='logs')],
        [InlineKeyboardButton("📈 Stats", callback_data='stats')],
        [InlineKeyboardButton("⚙️ Config", callback_data='config')],
        [InlineKeyboardButton("ℹ️ Help", callback_data='help')],
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "🤖 *PINLIVES Pro v3.1 Control Panel*\n\n"
        "Select an option:",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

    persistence.log_event('INFO', 'bot', 'Start command issued')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Help command"""
    help_text = """
🤖 *PINLIVES Pro v3.1 Help*

*Commands:*
/start - Show main menu
/status - System status
/logs - View logs
/stats - Statistics
/help - This message

*Buttons:*
🔐 Login - Start Telegram authentication
📊 Status - Check system status
➕ Add Channel - Add channel to monitor
📋 Logs - View system logs
📈 Stats - View statistics
⚙️ Config - View configuration
ℹ️ Help - Show help message

*Features:*
✓ Real Telethon library for private channels
✓ Persistent session storage
✓ Code extraction with OCR
✓ Message queue with retry
✓ System monitoring & health checks
✓ Full audit logging

*Support:* Contact admin
"""
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

# ============================================================================
# CALLBACK QUERY HANDLERS
# ============================================================================

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle button callbacks"""
    query = update.callback_query
    user_id = update.effective_user.id
    persistence = get_persistence()

    if user_id != settings.admin_id:
        await query.answer("❌ Unauthorized", show_alert=True)
        return

    await query.answer()

    if query.data == 'login_start':
        await query.edit_message_text(
            "🔐 *Login to Telegram*\n\n"
            "Please send your phone number (e.g., +84388588488):"
        )
        persistence.log_event('INFO', 'bot', 'Login started')
        return WAIT_LOGIN_PHONE

    elif query.data == 'status':
        status_text = await get_status_text()
        await query.edit_message_text(status_text, parse_mode=ParseMode.MARKDOWN)

    elif query.data == 'add_channel':
        await query.edit_message_text(
            "➕ *Add Channel*\n\n"
            "Please send the channel ID:"
        )
        return WAIT_CHANNEL_ID

    elif query.data == 'logs':
        logs_text = await get_logs_text()
        await query.edit_message_text(logs_text, parse_mode=ParseMode.MARKDOWN)

    elif query.data == 'stats':
        stats_text = await get_stats_text()
        await query.edit_message_text(stats_text, parse_mode=ParseMode.MARKDOWN)

    elif query.data == 'config':
        config_text = get_config_text()
        await query.edit_message_text(config_text, parse_mode=ParseMode.MARKDOWN)

    elif query.data == 'help':
        help_text = """
🤖 *PINLIVES Pro v3.1 Control Panel*

*Main Features:*
1. 🔐 Login to Telegram account
2. 📊 Monitor system status
3. ➕ Add channels to monitor
4. 📋 View system logs
5. 📈 View statistics
6. ⚙️ View configuration
7. ℹ️ Get help

*How to Use:*
- Use buttons to navigate
- Send required information when prompted
- All actions are logged for audit trail

*Security:*
- Only admin can access
- Credentials stored securely
- All operations logged
"""
        await query.edit_message_text(help_text, parse_mode=ParseMode.MARKDOWN)

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

async def get_status_text() -> str:
    """Get system status"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{settings.backend_url}/api/status") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return f"""
📊 *System Status*

Status: `{data.get('status')}`
Version: `{data.get('version')}`
Uptime: `{data.get('uptime_seconds', 0):.0f}s`
Messages: `{data.get('messages_processed', 0)}`
Errors: `{data.get('errors', 0)}`
Queue: `{data.get('queue_depth', 0)}`
Last Message: `{data.get('last_message', 'N/A')}`
"""
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        return f"❌ Error getting status: {str(e)}"

async def get_logs_text() -> str:
    """Get system logs"""
    try:
        with open(settings.log_file, 'r') as f:
            lines = f.readlines()
            recent = ''.join(lines[-10:])
            return f"""
📋 *Recent Logs*

```
{recent}
```
"""
    except Exception as e:
        logger.error(f"Error reading logs: {e}")
        return f"❌ Error reading logs: {str(e)}"

async def get_stats_text() -> str:
    """Get system statistics"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{settings.backend_url}/api/metrics") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    mem = data.get('memory', {})
                    cpu = data.get('cpu', 0)
                    disk = data.get('disk', {})
                    queue = data.get('queue', {})
                    cache = data.get('cache', {})
                    messages = data.get('messages', {})

                    return f"""
📈 *System Statistics*

*Performance:*
CPU: `{cpu:.1f}%`
Memory: `{mem.get('rss_mb', 0):.1f}MB`
Disk: `{disk.get('used_gb', 0):.1f}GB / {disk.get('total_gb', 0):.1f}GB`

*Queue:*
Depth: `{queue.get('depth', 0)}`
Max Size: `{queue.get('max_size', 0)}`

*Cache:*
Size: `{cache.get('size', 0)}`
Max Size: `{cache.get('max_size', 0)}`

*Messages:*
Processed: `{messages.get('processed', 0)}`
Errors: `{messages.get('errors', 0)}`
Error Rate: `{messages.get('error_rate', 0):.2f}%`
"""
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        return f"❌ Error getting stats: {str(e)}"

def get_config_text() -> str:
    """Get configuration info"""
    config = settings.to_dict()
    config_lines = [f"`{k}`: `{v}`" for k, v in config.items()]
    return "⚙️ *Configuration*\n\n" + "\n".join(config_lines)

# ============================================================================
# MESSAGE HANDLERS FOR MULTI-STEP CONVERSATIONS
# ============================================================================

async def handle_login_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle phone input for login"""
    persistence = get_persistence()
    phone = update.message.text.strip()

    # Validate phone format
    if not re.match(r'^\+?[0-9]{7,20}$', phone.replace(' ', '').replace('-', '')):
        await update.message.reply_text("❌ Invalid phone format. Please use format like +84388588488")
        return WAIT_LOGIN_PHONE

    try:
        # Call backend to start login
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{settings.backend_url}/api/telethon/login/start",
                json={"phone": phone}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    await update.message.reply_text(
                        f"✓ {data.get('message')}\n\n"
                        "Please send the OTP code sent to your Telegram account:"
                    )
                    context.user_data['phone'] = phone
                    persistence.log_event('INFO', 'bot', f'Login started for {phone}')
                    return WAIT_LOGIN_OTP
                else:
                    error = await resp.text()
                    await update.message.reply_text(f"❌ Error: {error}")
                    return WAIT_LOGIN_PHONE
    except Exception as e:
        logger.error(f"Error starting login: {e}")
        persistence.log_event('ERROR', 'bot', f'Login error: {str(e)}')
        await update.message.reply_text(f"❌ Error: {str(e)}")
        return WAIT_LOGIN_PHONE

async def handle_login_otp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle OTP input for login"""
    persistence = get_persistence()
    otp = update.message.text.strip()
    otp = re.sub(r'[\s\-,_.]', '', otp)  # Parse various formats

    if not otp.isdigit() or len(otp) < 4:
        await update.message.reply_text("❌ Invalid OTP. Please send digits only.")
        return WAIT_LOGIN_OTP

    try:
        phone = context.user_data.get('phone')
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{settings.backend_url}/api/telethon/login/verify",
                json={"phone": phone, "otp": otp}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    await update.message.reply_text(f"✓ {data.get('message')}")
                    persistence.log_event('INFO', 'bot', f'Login verified for {phone}')
                    return ConversationHandler.END
                else:
                    error = await resp.text()
                    await update.message.reply_text(f"❌ Error: {error}")
                    return WAIT_LOGIN_OTP
    except Exception as e:
        logger.error(f"Error verifying OTP: {e}")
        persistence.log_event('ERROR', 'bot', f'OTP error: {str(e)}')
        await update.message.reply_text(f"❌ Error: {str(e)}")
        return WAIT_LOGIN_OTP

async def handle_add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle channel ID input"""
    persistence = get_persistence()
    text = update.message.text.strip()

    try:
        channel_id = int(text)
    except ValueError:
        await update.message.reply_text("❌ Invalid channel ID. Please send a number.")
        return WAIT_CHANNEL_ID

    try:
        phone = settings.telethon_phone
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{settings.backend_url}/api/telethon/channel/add",
                json={"phone": phone, "channel_id": channel_id, "channel_name": f"Channel_{channel_id}"}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    await update.message.reply_text(f"✓ {data.get('message')}")
                    persistence.log_event('INFO', 'bot', f'Channel added: {channel_id}')
                    return ConversationHandler.END
                else:
                    error = await resp.text()
                    await update.message.reply_text(f"❌ Error: {error}")
                    return WAIT_CHANNEL_ID
    except Exception as e:
        logger.error(f"Error adding channel: {e}")
        persistence.log_event('ERROR', 'bot', f'Add channel error: {str(e)}')
        await update.message.reply_text(f"❌ Error: {str(e)}")
        return WAIT_CHANNEL_ID

# ============================================================================
# MAIN APPLICATION
# ============================================================================

def main():
    """Start bot application"""
    logger.info("=" * 80)
    logger.info("Starting PINLIVES Pro v3.1 Telegram Bot")
    logger.info("=" * 80)

    app = Application.builder().token(settings.bot_token).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))

    # Conversation handler for login
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_callback, pattern='^login_start$')],
        states={
            WAIT_LOGIN_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_login_phone)],
            WAIT_LOGIN_OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_login_otp)],
            WAIT_CHANNEL_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_channel)],
        },
        fallbacks=[]
    )

    app.add_handler(conv_handler)

    # General callbacks
    app.add_handler(CallbackQueryHandler(button_callback))

    logger.info("✓ Bot started")
    logger.info(f"Admin ID: {settings.admin_id}")
    logger.info(f"Backend: {settings.backend_url}")

    get_persistence().log_event('INFO', 'bot', 'Bot started')

    # Start polling
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {type(e).__name__}: {e}")
        print(f"FATAL ERROR: {e}", file=sys.stderr)
        sys.exit(1)
