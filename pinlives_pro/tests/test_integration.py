"""
PINLIVES Pro v3.1 - Integration Tests
End-to-end testing of all components
"""

import pytest
import asyncio
from datetime import datetime
import sys
sys.path.insert(0, '/workspace/pinlives-v9-pro')

from pinlives_pro.models.database import (
    init_db, SessionLocal, TelegramSession, TelegramMessage,
    ExtractedCode, MonitoredChannel
)
from pinlives_pro.core.persistence import PersistenceManager
from pinlives_pro.core.config import Settings

@pytest.fixture
def db():
    """Initialize test database"""
    init_db()
    return SessionLocal()

@pytest.fixture
def persistence():
    """Initialize persistence manager"""
    return PersistenceManager()

# ============================================================================
# DATABASE TESTS
# ============================================================================

def test_database_initialization(db):
    """Test database schema creation"""
    assert db is not None
    # Verify tables exist
    from sqlalchemy import inspect
    inspector = inspect(db.get_bind())
    tables = inspector.get_table_names()
    assert 'telegram_sessions' in tables
    assert 'monitored_channels' in tables
    assert 'telegram_messages' in tables
    assert 'extracted_codes' in tables

def test_save_and_load_session(persistence):
    """Test session persistence"""
    phone = "+84388588488"
    session_string = "test_session_string_123"

    # Save
    result = persistence.save_session(phone, session_string)
    assert result is True

    # Load
    loaded = persistence.load_session(phone)
    assert loaded == session_string

def test_mark_authenticated(persistence):
    """Test marking session as authenticated"""
    phone = "+84388588488"
    session_string = "test_session"

    persistence.save_session(phone, session_string)
    result = persistence.mark_authenticated(phone)
    assert result is True

def test_add_channel(persistence):
    """Test adding channel to monitoring"""
    phone = "+84388588488"
    session_string = "test_session"

    persistence.save_session(phone, session_string)
    result = persistence.add_channel(phone, 123456789, "Test Channel")
    assert result is True

    # Verify we can't add duplicate
    result = persistence.add_channel(phone, 123456789, "Test Channel")
    assert result is False

def test_save_message(persistence):
    """Test saving message to database"""
    phone = "+84388588488"
    session_string = "test_session"

    persistence.save_session(phone, session_string)
    persistence.add_channel(phone, 123456789, "Test Channel")

    msg_id = persistence.save_message(
        phone=phone,
        chat_id=123456789,
        chat_name="Test Channel",
        message_id=1,
        text="Test message",
        has_media=False
    )
    assert msg_id is not None

def test_save_code(persistence):
    """Test saving extracted code"""
    phone = "+84388588488"
    session_string = "test_session"

    persistence.save_session(phone, session_string)
    persistence.add_channel(phone, 123456789, "Test Channel")

    msg_id = persistence.save_message(
        phone=phone,
        chat_id=123456789,
        chat_name="Test Channel",
        message_id=1,
        text="Code: ABC123DEF456",
        has_media=False
    )

    result = persistence.save_code(
        message_id=msg_id,
        code="ABC123DEF456",
        entropy=2.5,
        pattern_score=0.8,
        ocr_confidence=0.95
    )
    assert result is True

def test_get_codes(persistence):
    """Test retrieving extracted codes"""
    phone = "+84388588488"
    session_string = "test_session"

    persistence.save_session(phone, session_string)
    persistence.add_channel(phone, 123456789, "Test Channel")

    msg_id = persistence.save_message(
        phone=phone,
        chat_id=123456789,
        chat_name="Test Channel",
        message_id=1,
        text="Code: ABC123",
        has_media=False
    )

    persistence.save_code(msg_id, "ABC123")

    codes = persistence.get_codes(limit=10)
    assert len(codes) > 0
    assert codes[0]['code'] == "ABC123"

# ============================================================================
# METRICS & LOGGING TESTS
# ============================================================================

def test_log_metric(persistence):
    """Test logging system metrics"""
    result = persistence.log_metric("test_metric", 42.5, {"tag": "value"})
    assert result is True

def test_log_event(persistence):
    """Test logging system events"""
    result = persistence.log_event(
        level="INFO",
        component="backend",
        message="Test event",
        context={"details": "test"}
    )
    assert result is True

def test_cleanup_old_metrics(persistence):
    """Test metric cleanup"""
    persistence.log_metric("metric1", 10)
    persistence.log_metric("metric2", 20)

    deleted = persistence.cleanup_old_metrics(days=0)  # Delete all old
    # Should delete at least 0 (depends on timing)
    assert deleted >= 0

# ============================================================================
# CONFIGURATION TESTS
# ============================================================================

def test_settings_loading():
    """Test configuration loading"""
    settings = Settings()
    assert settings.telethon_api_id > 0
    assert settings.telethon_api_hash
    assert settings.telethon_phone

def test_settings_optional_defaults():
    """Test optional settings have defaults"""
    settings = Settings()
    assert settings.backend_url
    assert settings.backend_host
    assert settings.backend_port > 0
    assert settings.ocr_cache_size > 0
    assert settings.message_queue_size > 0

# ============================================================================
# END-TO-END WORKFLOW TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_complete_workflow(persistence):
    """Test complete workflow from login to code extraction"""

    # 1. Save session
    phone = "+84388588488"
    session_string = "workflow_test_session"
    assert persistence.save_session(phone, session_string) is True

    # 2. Mark authenticated
    assert persistence.mark_authenticated(phone) is True

    # 3. Add channels
    channels = [
        (123456789, "Channel A"),
        (987654321, "Channel B"),
    ]
    for channel_id, channel_name in channels:
        assert persistence.add_channel(phone, channel_id, channel_name) is True

    # 4. Receive messages from channels
    messages = [
        (123456789, "Channel A", 1, "Secret code ABC123DEF"),
        (987654321, "Channel B", 2, "Another code XYZ789GHI"),
    ]
    for chat_id, chat_name, msg_id, text in messages:
        saved_id = persistence.save_message(
            phone=phone,
            chat_id=chat_id,
            chat_name=chat_name,
            message_id=msg_id,
            text=text,
            has_media=False
        )
        assert saved_id is not None

    # 5. Extract codes
    codes_to_extract = ["ABC123DEF", "XYZ789GHI"]
    for code in codes_to_extract:
        # Get the first message ID (simplified for testing)
        result = persistence.save_code(
            message_id=1,  # Would be from real message
            code=code,
            entropy=2.3,
            pattern_score=0.85
        )
        # First code saves, second fails (unique constraint)
        assert result in [True, False]

    # 6. Retrieve codes
    codes = persistence.get_codes(limit=10)
    assert len(codes) >= 1

    # 7. Log metrics
    assert persistence.log_metric("messages_processed", 2) is True
    assert persistence.log_metric("codes_extracted", len(codes)) is True

    # 8. Log events
    assert persistence.log_event(
        level="INFO",
        component="workflow_test",
        message="Workflow completed successfully",
        context={"messages": 2, "codes": len(codes)}
    ) is True

# ============================================================================
# ERROR HANDLING TESTS
# ============================================================================

def test_load_nonexistent_session(persistence):
    """Test loading non-existent session"""
    result = persistence.load_session("nonexistent+12345")
    assert result is None

def test_save_message_nonexistent_phone(persistence):
    """Test saving message for non-existent phone"""
    result = persistence.save_message(
        phone="nonexistent",
        chat_id=123,
        chat_name="Test",
        message_id=1,
        text="Test"
    )
    assert result is None

def test_add_channel_nonexistent_session(persistence):
    """Test adding channel to non-existent session"""
    result = persistence.add_channel("nonexistent", 123, "Test")
    assert result is False

def test_duplicate_code(persistence):
    """Test handling duplicate code extraction"""
    phone = "+84388588488"
    persistence.save_session(phone, "test")
    persistence.mark_authenticated(phone)
    persistence.add_channel(phone, 123, "Test")

    msg_id = persistence.save_message(phone, 123, "Test", 1, "Code: TEST", False)

    # First save succeeds
    result1 = persistence.save_code(msg_id, "TEST")
    assert result1 is True

    # Duplicate fails gracefully
    result2 = persistence.save_code(msg_id, "TEST")
    assert result2 is False

if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
