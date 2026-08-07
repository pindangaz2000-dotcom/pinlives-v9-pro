"""
PINLIVES Pro v3.1 - Configuration Management

Settings are validated lazily via get_settings(). Importing this module never
terminates the process: missing configuration raises ConfigError, and only the
process entrypoints turn that into a non-zero exit.
"""

import os
import sys
from typing import Optional, List

from dotenv import load_dotenv

load_dotenv()


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or malformed."""


class Settings:
    """Application settings sourced from environment variables."""

    def __init__(self):
        missing: List[str] = []
        invalid: List[str] = []

        def required(key: str, description: str) -> Optional[str]:
            value = os.getenv(key)
            if not value:
                missing.append(f"  {key} — {description}")
                return None
            return value

        def required_int(key: str, description: str) -> Optional[int]:
            value = required(key, description)
            if value is None:
                return None
            try:
                return int(value)
            except ValueError:
                invalid.append(f"  {key} must be an integer, got: {value!r}")
                return None

        # --- Required: Telethon (user account, reads private channels) ---
        self.telethon_api_id = required_int(
            'TELETHON_API_ID', 'App api_id from https://my.telegram.org/apps')
        self.telethon_api_hash = required(
            'TELETHON_API_HASH', 'App api_hash from https://my.telegram.org/apps')
        self.telethon_phone = required(
            'TELETHON_PHONE', 'Account phone number, e.g. +84388588488')

        # --- Required: Bot (control panel) ---
        self.bot_token = required(
            'TELEGRAM_BOT_TOKEN', 'Bot token from @BotFather')
        self.admin_id = required_int(
            'TELEGRAM_ADMIN_ID', 'Your Telegram user ID (from @userinfobot)')

        if missing or invalid:
            parts = ["Configuration error."]
            if missing:
                parts.append("Missing required environment variables:")
                parts.extend(missing)
            if invalid:
                parts.append("Invalid values:")
                parts.extend(invalid)
            parts.append("Copy .env.example to .env and fill these in.")
            raise ConfigError("\n".join(parts))

        # --- Optional: API ---
        self.backend_url = os.getenv('BACKEND_URL', 'http://localhost:8000')
        self.backend_host = os.getenv('BACKEND_HOST', '0.0.0.0')
        self.backend_port = self._optional_int('BACKEND_PORT', 8000)

        # --- Optional: Database ---
        self.db_type = os.getenv('DB_TYPE', 'sqlite')
        self.db_path = os.getenv('DB_PATH', '/tmp/pinlives_pro.db')
        self.db_user = os.getenv('DB_USER', 'pinlives')
        self.db_password = os.getenv('DB_PASSWORD', 'pinlives')
        self.db_host = os.getenv('DB_HOST', 'localhost')
        self.db_port = os.getenv('DB_PORT', '5432')
        self.db_name = os.getenv('DB_NAME', 'pinlives_pro')

        # --- Optional: Session handling ---
        self.session_dir = os.getenv('SESSION_DIR', '/tmp/telethon_sessions')
        self.otp_timeout_seconds = self._optional_int('OTP_TIMEOUT_SECONDS', 600)

        # --- Optional: Cache / queue ---
        self.ocr_cache_size = self._optional_int('OCR_CACHE_SIZE', 500)
        self.ocr_cache_ttl_seconds = self._optional_int('OCR_CACHE_TTL_SECONDS', 3600)
        self.message_queue_size = self._optional_int('MESSAGE_QUEUE_SIZE', 5000)
        self.max_retries = self._optional_int('MAX_RETRIES', 3)

        # --- Optional: Logging ---
        self.log_level = os.getenv('LOG_LEVEL', 'INFO')
        self.log_file = os.getenv('LOG_FILE', '/tmp/pinlives.log')
        self.sql_echo = os.getenv('SQL_ECHO', 'false').lower() == 'true'

        # --- Optional: Feature flags ---
        self.enable_monitoring = os.getenv('ENABLE_MONITORING', 'true').lower() == 'true'
        self.enable_persistence = os.getenv('ENABLE_PERSISTENCE', 'true').lower() == 'true'

    @staticmethod
    def _optional_int(key: str, default: int) -> int:
        raw = os.getenv(key)
        if raw is None or raw == '':
            return default
        try:
            return int(raw)
        except ValueError:
            raise ConfigError(f"{key} must be an integer, got: {raw!r}")

    def to_dict(self) -> dict:
        """Non-secret settings, safe to display in the bot or logs."""
        return {
            'backend_url': self.backend_url,
            'backend_host': self.backend_host,
            'backend_port': self.backend_port,
            'db_type': self.db_type,
            'db_path': self.db_path if self.db_type == 'sqlite' else f'{self.db_host}:{self.db_port}/{self.db_name}',
            'session_dir': self.session_dir,
            'otp_timeout_seconds': self.otp_timeout_seconds,
            'ocr_cache_size': self.ocr_cache_size,
            'message_queue_size': self.message_queue_size,
            'max_retries': self.max_retries,
            'log_level': self.log_level,
            'enable_monitoring': self.enable_monitoring,
            'enable_persistence': self.enable_persistence,
        }


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Return the process-wide Settings, constructing it on first use."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def load_settings_or_exit() -> Settings:
    """Entrypoint helper: report a ConfigError clearly and exit non-zero."""
    try:
        return get_settings()
    except ConfigError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    s = load_settings_or_exit()
    print("✓ Configuration valid")
    for key, value in s.to_dict().items():
        print(f"  {key} = {value}")
