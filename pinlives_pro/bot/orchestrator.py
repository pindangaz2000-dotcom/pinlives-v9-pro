"""
PINLIVES Pro v3.1 - Telegram bot control panel.

The bot is the operator's console: it drives the login flow, registers channels,
and reports status. All state lives in the backend; the bot only calls its API.
"""

import asyncio
import html
import logging
import re
import sys
from typing import Any, Dict, Optional

import aiohttp
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from ..core.config import ConfigError, get_settings, load_settings_or_exit
from ..core.logging_setup import setup_logging

logger = logging.getLogger(__name__)

# Conversation states
ASK_PHONE, ASK_CODE, ASK_PASSWORD = range(3)
ASK_CHANNEL = 100

HTTP_TIMEOUT = aiohttp.ClientTimeout(total=20)


# ----------------------------------------------------------------------
# Backend client
# ----------------------------------------------------------------------

async def api_call(method: str, path: str, payload: Optional[Dict] = None) -> Dict[str, Any]:
    """Call the backend. Always returns a dict with 'ok' so callers never crash."""
    settings = get_settings()
    url = f"{settings.backend_url}{path}"
    try:
        async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
            async with session.request(method, url, json=payload) as resp:
                try:
                    body = await resp.json()
                except (aiohttp.ContentTypeError, ValueError):
                    body = {'error': (await resp.text())[:300]}
                if resp.status >= 400:
                    return {'ok': False, 'error': body.get('error') or body.get('detail') or f'HTTP {resp.status}'}
                return {'ok': True, 'data': body}
    except asyncio.TimeoutError:
        return {'ok': False, 'error': 'Backend timed out'}
    except aiohttp.ClientConnectorError:
        return {'ok': False, 'error': f'Cannot reach backend at {settings.backend_url}'}
    except aiohttp.ClientError as e:
        return {'ok': False, 'error': f'Backend error: {e}'}


def esc(value: Any) -> str:
    """Escape a value for HTML parse mode."""
    return html.escape(str(value))


def is_admin(update: Update) -> bool:
    user = update.effective_user
    return bool(user and user.id == get_settings().admin_id)


async def deny(update: Update) -> None:
    logger.warning("Unauthorized access from %s", update.effective_user.id if update.effective_user else '?')
    if update.callback_query:
        await update.callback_query.answer("Not authorized", show_alert=True)
    elif update.message:
        await update.message.reply_text("Not authorized.")


# ----------------------------------------------------------------------
# Menu
# ----------------------------------------------------------------------

def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔐 Login", callback_data='login'),
         InlineKeyboardButton("📊 Status", callback_data='status')],
        [InlineKeyboardButton("📡 My channels", callback_data='dialogs'),
         InlineKeyboardButton("➕ Add channel", callback_data='addchan')],
        [InlineKeyboardButton("🔑 Codes", callback_data='codes'),
         InlineKeyboardButton("📋 Logs", callback_data='logs')],
        [InlineKeyboardButton("⚙️ Config", callback_data='config'),
         InlineKeyboardButton("ℹ️ Help", callback_data='help')],
    ])


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return await deny(update)
    await update.message.reply_text(
        "<b>PINLIVES Pro v3.1</b>\nControl panel. Pick an action:",
        reply_markup=main_menu(),
        parse_mode=ParseMode.HTML,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return await deny(update)
    await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML)


HELP_TEXT = (
    "<b>PINLIVES Pro v3.1</b>\n\n"
    "<b>Commands</b>\n"
    "/start — control panel\n"
    "/status — system status\n"
    "/cancel — abort the current step\n"
    "/help — this message\n\n"
    "<b>Getting running</b>\n"
    "1. <b>Login</b> — sign the account in to Telegram. You send the phone, "
    "Telegram sends a code, you send the code back.\n"
    "2. <b>My channels</b> — list what the account can see.\n"
    "3. <b>Add channel</b> — start monitoring one by its ID.\n\n"
    "Once signed in, messages are captured, codes extracted, and everything "
    "stored. The session survives a restart, so login is a one-time step."
)


# ----------------------------------------------------------------------
# Status / read-only views
# ----------------------------------------------------------------------

async def render_status() -> str:
    result = await api_call('GET', '/api/status')
    if not result['ok']:
        return f"❌ {esc(result['error'])}"
    d = result['data']
    t = d.get('telethon', {})
    return (
        "<b>System status</b>\n"
        f"Uptime: {d.get('uptime_seconds', 0):.0f}s\n"
        f"Telegram: <b>{esc(t.get('state'))}</b>"
        + (f" ({esc(t.get('user'))})" if t.get('user') else "") + "\n"
        f"Messages stored: {d.get('messages_stored', 0)}\n"
        f"Codes extracted: {d.get('codes_extracted', 0)}\n"
        f"Queue depth: {d.get('queue_depth', 0)}\n"
        f"Errors: {d.get('errors', 0)}"
    )


async def render_codes() -> str:
    result = await api_call('GET', '/api/codes?limit=15')
    if not result['ok']:
        return f"❌ {esc(result['error'])}"
    codes = result['data'].get('codes', [])
    if not codes:
        return "No codes extracted yet."
    lines = ["<b>Recent codes</b>"]
    for c in codes:
        entropy = c.get('entropy')
        suffix = f"  <i>H={entropy:.1f}</i>" if isinstance(entropy, (int, float)) else ""
        lines.append(f"<code>{esc(c['code'])}</code>{suffix}")
    return "\n".join(lines)


async def render_logs() -> str:
    result = await api_call('GET', '/api/logs?limit=15')
    if not result['ok']:
        return f"❌ {esc(result['error'])}"
    logs = result['data'].get('logs', [])
    if not logs:
        return "No log entries yet."
    lines = ["<b>Recent activity</b>"]
    for entry in logs:
        ts = (entry.get('timestamp') or '')[11:19]
        lines.append(f"<code>{ts}</code> [{esc(entry.get('level'))}] {esc(entry.get('message'))}")
    return "\n".join(lines)


async def render_dialogs() -> str:
    result = await api_call('GET', '/api/telethon/dialogs?limit=30')
    if not result['ok']:
        return f"❌ {esc(result['error'])}\n\nSign in first with 🔐 Login."
    dialogs = result['data'].get('dialogs', [])
    if not dialogs:
        return "The account is not in any channel or group."
    lines = ["<b>Visible channels</b>", "<i>Use the ID with ➕ Add channel</i>", ""]
    for d in dialogs:
        lines.append(f"<code>{esc(d['channel_id'])}</code> — {esc(d['channel_name'])}")
    return "\n".join(lines)


async def render_config() -> str:
    settings = get_settings()
    lines = ["<b>Configuration</b>"]
    for key, value in settings.to_dict().items():
        lines.append(f"{esc(key)}: <code>{esc(value)}</code>")
    return "\n".join(lines)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return await deny(update)
    await update.message.reply_text(await render_status(), parse_mode=ParseMode.HTML)


async def on_menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the buttons that need no follow-up input."""
    if not is_admin(update):
        return await deny(update)

    query = update.callback_query
    await query.answer()
    action = query.data

    renderers = {
        'status': render_status,
        'codes': render_codes,
        'logs': render_logs,
        'dialogs': render_dialogs,
        'config': render_config,
    }

    if action == 'help':
        text = HELP_TEXT
    elif action in renderers:
        text = await renderers[action]()
    else:
        text = "Unknown action."

    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=main_menu())


# ----------------------------------------------------------------------
# Login conversation
# ----------------------------------------------------------------------

async def login_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update):
        await deny(update)
        return ConversationHandler.END

    query = update.callback_query
    await query.answer()

    state = await api_call('GET', '/api/telethon/state')
    if state['ok'] and state['data'].get('authenticated'):
        await query.edit_message_text(
            f"Already signed in as {esc(state['data'].get('user'))}.",
            parse_mode=ParseMode.HTML, reply_markup=main_menu(),
        )
        return ConversationHandler.END

    default_phone = get_settings().telethon_phone
    await query.edit_message_text(
        f"Send the phone number to sign in.\nDefault: <code>{esc(default_phone)}</code>\n"
        "Send <code>ok</code> to use it, or /cancel to stop.",
        parse_mode=ParseMode.HTML,
    )
    return ASK_PHONE


async def login_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or '').strip()
    phone = get_settings().telethon_phone if raw.lower() in {'ok', 'y', 'yes'} else raw
    phone = re.sub(r'[\s\-().]', '', phone)

    if not re.fullmatch(r'\+?\d{7,20}', phone):
        await update.message.reply_text("That is not a phone number. Try again, or /cancel.")
        return ASK_PHONE

    context.user_data['phone'] = phone
    await update.message.reply_text("Requesting a code from Telegram…")

    result = await api_call('POST', '/api/telethon/login/start', {'phone': phone})
    if not result['ok']:
        await update.message.reply_text(
            f"❌ {esc(result['error'])}\n\nSend another number, or /cancel.",
            parse_mode=ParseMode.HTML,
        )
        return ASK_PHONE

    data = result['data']
    if data.get('authenticated'):
        await update.message.reply_text(
            f"✓ {esc(data.get('message'))}", parse_mode=ParseMode.HTML, reply_markup=main_menu()
        )
        return ConversationHandler.END

    await update.message.reply_text(
        f"✓ {esc(data.get('message'))}\n\nSend the code Telegram just sent you. "
        "Spaces and dashes are fine.",
        parse_mode=ParseMode.HTML,
    )
    return ASK_CODE


async def login_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # Telegram shows the code as "12 345"; operators paste it in many shapes.
    otp = re.sub(r'[\s\-,_.]', '', (update.message.text or '').strip())
    if not otp.isdigit() or not (4 <= len(otp) <= 8):
        await update.message.reply_text("The code should be 4-8 digits. Try again, or /cancel.")
        return ASK_CODE

    result = await api_call('POST', '/api/telethon/login/verify',
                            {'phone': context.user_data.get('phone', ''), 'otp': otp})
    if not result['ok']:
        error = result['error']
        if '2FA' in error or 'password' in error.lower():
            await update.message.reply_text(
                "This account has a 2FA password. Send it now, or /cancel."
            )
            return ASK_PASSWORD
        await update.message.reply_text(
            f"❌ {esc(error)}\n\nTry the code again, or /cancel.", parse_mode=ParseMode.HTML
        )
        return ASK_CODE

    await update.message.reply_text(
        f"✓ {esc(result['data'].get('message'))}\n\nListening for messages now.",
        parse_mode=ParseMode.HTML, reply_markup=main_menu(),
    )
    return ConversationHandler.END


async def login_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    password = (update.message.text or '').strip()

    # The password should not linger in the chat history.
    try:
        await update.message.delete()
    except Exception:
        logger.debug("Could not delete the password message")

    result = await api_call('POST', '/api/telethon/login/password', {'password': password})
    if not result['ok']:
        await update.effective_chat.send_message(
            f"❌ {esc(result['error'])}\n\nSend the password again, or /cancel.",
            parse_mode=ParseMode.HTML,
        )
        return ASK_PASSWORD

    await update.effective_chat.send_message(
        f"✓ {esc(result['data'].get('message'))}", parse_mode=ParseMode.HTML, reply_markup=main_menu()
    )
    return ConversationHandler.END


# ----------------------------------------------------------------------
# Add-channel conversation
# ----------------------------------------------------------------------

async def channel_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_admin(update):
        await deny(update)
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "Send the channel ID to monitor, for example <code>-1001234567890</code>.\n"
        "Use 📡 My channels to look one up. /cancel to stop.",
        parse_mode=ParseMode.HTML,
    )
    return ASK_CHANNEL


async def channel_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or '').strip()
    try:
        channel = int(raw)
    except ValueError:
        await update.message.reply_text("That is not a channel ID. Try again, or /cancel.")
        return ASK_CHANNEL

    result = await api_call('POST', '/api/telethon/channel/add', {
        'phone': get_settings().telethon_phone,
        'channel_id': channel,
        'channel_name': f'Channel_{channel}',
    })
    if not result['ok']:
        await update.message.reply_text(
            f"❌ {esc(result['error'])}\n\nTry another ID, or /cancel.", parse_mode=ParseMode.HTML
        )
        return ASK_CHANNEL

    await update.message.reply_text(
        f"✓ Monitoring <b>{esc(result['data'].get('channel_name'))}</b>",
        parse_mode=ParseMode.HTML, reply_markup=main_menu(),
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Cancelled.", reply_markup=main_menu())
    return ConversationHandler.END


# ----------------------------------------------------------------------
# Wiring
# ----------------------------------------------------------------------

def build_application(token: str) -> Application:
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))

    login_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(login_entry, pattern=r'^login$')],
        states={
            ASK_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_phone)],
            ASK_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_code)],
            ASK_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_password)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    channel_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(channel_entry, pattern=r'^addchan$')],
        states={ASK_CHANNEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, channel_id)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(login_conv)
    app.add_handler(channel_conv)

    # Registered last so the conversations claim their own callbacks first.
    app.add_handler(CallbackQueryHandler(
        on_menu_button, pattern=r'^(status|codes|logs|dialogs|config|help)$'))

    return app


def main():
    settings = load_settings_or_exit()
    setup_logging(level=settings.log_level, log_file=settings.log_file, component='pinlives.bot')

    logger.info("Starting PINLIVES Pro v3.1 bot")
    logger.info("Admin: %s | Backend: %s", settings.admin_id, settings.backend_url)

    app = build_application(settings.bot_token)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Interrupted")
    except ConfigError as e:
        print(f"\n{e}\n", file=sys.stderr)
        sys.exit(1)
