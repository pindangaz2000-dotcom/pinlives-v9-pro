"""
PINLIVES Pro v3.1 - Configuration Management
Environment-based configuration with validation
"""

import os
from typing import Optional
from dotenv import load_dotenv
import sys

load_dotenv()

class Settings:
    """Application settings from environment variables"""

    # ============================================================================
    # REQUIRED ENVIRONMENT VARIABLES
    # ============================================================================

    def __init__(self):
        # Telethon credentials
        self.telethon_api_id = self._get_required_int(
            'TELETHON_API_ID',
            'Get from https://my.telegram.org/apps'
        )
        self.telethon_api_hash = self._get_required(
            'TELETHON_API_HASH',
            'Get from https://my.telegram.org/apps'
        )
        self.telethon_phone = self._get_required(
            'TELETHON_PHONE',
            'Format: +84388588488'
        )

        # Bot credentials
        self.bot_token = self._get_required(
            'TELEGRAM_BOT_TOKEN',
            'Get from @BotFather'
        )
        self.admin_id = self._get_required_int(
            'TELEGRAM_ADMIN_ID',
            'Your Telegram user ID'
        )

        # ============================================================================
        # OPTIONAL ENVIRONMENT VARIABLES
        # ============================================================================

        # API Configuration
        self.backend_url = os.getenv('BACKEND_URL', 'http://localhost:8000')
        self.backend_host = os.getenv('BACKEND_HOST', '0.0.0.0')
        self.backend_port = int(os.getenv('BACKEND_PORT', '8000'))

        # Database Configuration
        self.db_type = os.getenv('DB_TYPE', 'sqlite')
        self.db_path = os.getenv('DB_PATH', '/tmp/pinlives_pro.db')
        self.db_user = os.getenv('DB_USER', 'pinlives')
        self.db_password = os.getenv('DB_PASSWORD', 'pinlives')
        self.db_host = os.getenv('DB_HOST', 'localhost')
        self.db_port = os.getenv('DB_PORT', '5432')
        self.db_name = os.getenv('DB_NAME', 'pinlives_pro')

        # Session Configuration
        self.session_dir = os.getenv('SESSION_DIR', '/tmp/telethon_sessions')
        self.session_timeout_minutes = int(os.getenv('SESSION_TIMEOUT_MINUTES', '10'))

        # Cache Configuration
        self.ocr_cache_size = int(os.getenv('OCR_CACHE_SIZE', '500'))
        self.ocr_cache_ttl_seconds = int(os.getenv('OCR_CACHE_TTL_SECONDS', '3600'))

        # Queue Configuration
        self.message_queue_size = int(os.getenv('MESSAGE_QUEUE_SIZE', '5000'))
        self.max_retries = int(os.getenv('MAX_RETRIES', '3'))

        # Logging Configuration
        self.log_level = os.getenv('LOG_LEVEL', 'INFO')
        self.log_file = os.getenv('LOG_FILE', '/tmp/pinlives.log')
        self.sql_echo = os.getenv('SQL_ECHO', 'false').lower() == 'true'

        # Feature Flags
        self.enable_monitoring = os.getenv('ENABLE_MONITORING', 'true').lower() == 'true'
        self.enable_persistence = os.getenv('ENABLE_PERSISTENCE', 'true').lower() == 'true'

    def _get_required(self, key: str, description: str = None) -> str:
        """Get required environment variable or exit"""
        value = os.getenv(key)
        if not value:
            msg = f"❌ ERROR: Environment variable '{key}' is required"
            if description:
                msg += f"\n   Description: {description}"
            print(msg, file=sys.stderr)
            sys.exit(1)
        return value

    def _get_required_int(self, key: str, description: str = None) -> int:
        """Get required integer environment variable or exit"""
        value = self._get_required(key, description)
        try:
            return int(value)
        except ValueError:
            msg = f"❌ ERROR: {key} must be an integer, got: {value}"
            print(msg, file=sys.stderr)
            sys.exit(1)

    def to_dict(self) -> dict:
        """Convert settings to dictionary (exclude secrets)"""
        return {
            'backend_url': self.backend_url,
            'backend_host': self.backend_host,
            'backend_port': self.backend_port,
            'db_type': self.db_type,
            'db_path': self.db_path,
            'session_dir': self.session_dir,
            'session_timeout_minutes': self.session_timeout_minutes,
            'ocr_cache_size': self.ocr_cache_size,
            'log_level': self.log_level,
            'enable_monitoring': self.enable_monitoring,
            'enable_persistence': self.enable_persistence,
        }

# Global settings instance
settings = Settings()

def get_settings() -> Settings:
    """Get settings singleton"""
    return settings

if __name__ == '__main__':
    print("✓ Configuration loaded successfully")
    print(settings.to_dict())
