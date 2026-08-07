"""
PINLIVES Pro v3.1 - Integration tests.

Each test runs against a real SQLite database in a temporary directory, and the
API tests drive the real FastAPI app through its ASGI transport. Nothing here is
mocked away, so a passing run means the code paths actually executed.
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

# Point the app at a throwaway database before anything imports the models.
_TEST_DB = Path(os.environ.setdefault('DB_PATH', '/tmp/pinlives_test.db'))
os.environ.setdefault('TELETHON_API_ID', '1111111')
os.environ.setdefault('TELETHON_API_HASH', 'test_hash')
os.environ.setdefault('TELETHON_PHONE', '+84388588488')
os.environ.setdefault('TELEGRAM_BOT_TOKEN', '123456:TEST')
os.environ.setdefault('TELEGRAM_ADMIN_ID', '7478077662')
os.environ.setdefault('LOG_FILE', '/tmp/pinlives_test.log')

from pinlives_pro.core.codefilter import (  # noqa: E402
    extract_codes, is_valid_code, repeat_ratio, shannon_entropy,
)
from pinlives_pro.core.config import ConfigError, Settings  # noqa: E402
from pinlives_pro.core.persistence import PersistenceManager  # noqa: E402
from pinlives_pro.models.database import Base, engine, init_db  # noqa: E402

PHONE = '+84388588488'
# A real-shaped supergroup id: negative and beyond 32-bit range.
CHANNEL = -1001234567890


@pytest.fixture(autouse=True)
def clean_db():
    """Give every test an empty schema."""
    Base.metadata.drop_all(bind=engine)
    init_db()
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def store():
    return PersistenceManager()


@pytest.fixture
def authed_store(store):
    store.save_session(PHONE, 'STRING_SESSION')
    store.mark_authenticated(PHONE)
    return store


# ----------------------------------------------------------------------
# Schema
# ----------------------------------------------------------------------

def test_schema_creates_every_table():
    from sqlalchemy import inspect
    tables = set(inspect(engine).get_table_names())
    assert {
        'telegram_sessions', 'monitored_channels', 'telegram_messages',
        'extracted_codes', 'system_metrics', 'system_logs',
    } <= tables


def test_index_names_are_unique_across_tables():
    from sqlalchemy import inspect
    inspector = inspect(engine)
    names = []
    for table in inspector.get_table_names():
        names.extend(i['name'] for i in inspector.get_indexes(table))
    assert len(names) == len(set(names)), f"duplicate index names: {names}"


# ----------------------------------------------------------------------
# Sessions
# ----------------------------------------------------------------------

def test_session_round_trip(store):
    assert store.save_session(PHONE, 'ABC') is True
    assert store.load_session(PHONE) == 'ABC'


def test_session_update_replaces_value(store):
    store.save_session(PHONE, 'ABC')
    store.save_session(PHONE, 'XYZ')
    assert store.load_session(PHONE) == 'XYZ'


def test_load_missing_session_returns_none(store):
    assert store.load_session('+99900000000') is None


def test_authentication_flag(store):
    store.save_session(PHONE, 'ABC')
    assert store.is_authenticated(PHONE) is False
    assert store.mark_authenticated(PHONE) is True
    assert store.is_authenticated(PHONE) is True


def test_mark_authenticated_without_session(store):
    assert store.mark_authenticated('+99900000000') is False


# ----------------------------------------------------------------------
# Channels
# ----------------------------------------------------------------------

def test_add_channel_accepts_large_negative_id(authed_store):
    assert authed_store.add_channel(PHONE, CHANNEL, 'Kenh Kin') is True
    channels = authed_store.get_channels(PHONE)
    assert len(channels) == 1
    # The id must survive the round trip; a 32-bit column would truncate it.
    assert channels[0]['channel_id'] == CHANNEL


def test_duplicate_channel_rejected(authed_store):
    authed_store.add_channel(PHONE, CHANNEL, 'Kenh Kin')
    assert authed_store.add_channel(PHONE, CHANNEL, 'Kenh Kin') is False


def test_add_channel_unknown_account(store):
    assert store.add_channel('+99900000000', CHANNEL, 'X') is False


# ----------------------------------------------------------------------
# Messages
# ----------------------------------------------------------------------

def test_message_auto_registers_unseen_channel(authed_store):
    """The listener sees chats before anyone registers them."""
    row_id = authed_store.save_message(PHONE, CHANNEL, 'Kenh Kin', 1, 'hello')
    assert isinstance(row_id, int)
    assert len(authed_store.get_channels(PHONE)) == 1


def test_message_rejected_when_auto_register_disabled(authed_store):
    assert authed_store.save_message(
        PHONE, CHANNEL, 'Kenh Kin', 1, 'hello', auto_register_channel=False
    ) is None


def test_duplicate_message_returns_same_row(authed_store):
    first = authed_store.save_message(PHONE, CHANNEL, 'Kenh Kin', 7, 'hello')
    second = authed_store.save_message(PHONE, CHANNEL, 'Kenh Kin', 7, 'hello')
    assert first == second
    assert authed_store.count_messages() == 1


def test_message_unknown_account(store):
    assert store.save_message('+99900000000', CHANNEL, 'X', 1, 'hi') is None


# ----------------------------------------------------------------------
# Codes
# ----------------------------------------------------------------------

def test_code_saved_and_listed(authed_store):
    msg = authed_store.save_message(PHONE, CHANNEL, 'Kenh Kin', 1, 'Code AB7X9Q2M')
    assert authed_store.save_code(msg, 'AB7X9Q2M', entropy=3.0) is True
    codes = authed_store.get_codes()
    assert [c['code'] for c in codes] == ['AB7X9Q2M']


def test_duplicate_code_rejected_and_store_still_usable(authed_store):
    msg = authed_store.save_message(PHONE, CHANNEL, 'Kenh Kin', 1, 'Code AB7X9Q2M')
    assert authed_store.save_code(msg, 'AB7X9Q2M') is True
    assert authed_store.save_code(msg, 'AB7X9Q2M') is False
    # A rolled-back transaction must not poison later writes.
    assert authed_store.log_event('INFO', 'test', 'still alive') is True


# ----------------------------------------------------------------------
# Metrics, logs, health
# ----------------------------------------------------------------------

def test_metrics_and_logs(store):
    assert store.log_metric('probe', 1.5, {'tag': 'v'}) is True
    assert store.log_event('INFO', 'test', 'hello', {'k': 'v'}) is True
    assert len(store.get_logs(limit=5)) == 1


def test_health_check(store):
    assert store.health_check() is True


# ----------------------------------------------------------------------
# Code filter
# ----------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Code hom nay: AB7X9Q2M", ["AB7X9Q2M"]),
    ("Hai ma: X7f2Kq9L va M4nP8vT2", ["X7f2Kq9L", "M4nP8vT2"]),
    ("Lien he 0388588488", []),                      # phone number
    ("Ngay 2026-08-07", []),                          # date
    ("Xem https://t.me/kenhkin/12345", []),           # url path
    ("AAAAAAAA", []),                                 # no entropy
    ("Gia 1500000 VND", []),                          # price
    ("THANKS ADMIN", []),                             # stopwords
    ("", []),
    ("Ma AB7X9Q2M lap lai AB7X9Q2M", ["AB7X9Q2M"]),  # deduplicated
])
def test_extract_codes(text, expected):
    assert extract_codes(text) == expected


def test_entropy_ranks_real_codes_above_filler():
    assert shannon_entropy('AAAAAAAA') < 1.0
    assert shannon_entropy('AB7X9Q2M') > 2.5


def test_all_digit_run_rejected_despite_high_entropy():
    """Entropy alone is not enough: '12345678' scores high but is never a code."""
    assert shannon_entropy('12345678') > 2.5
    assert is_valid_code('12345678') is False


def test_repeat_ratio():
    assert repeat_ratio('AAAA') == 1.0
    assert repeat_ratio('ABCD') == 0.25


# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------

def test_settings_load_from_env():
    s = Settings()
    assert s.telethon_api_id == 1111111
    assert s.backend_port == 8000


def test_missing_required_vars_raise_not_exit(monkeypatch):
    """Importing or constructing config must never kill the process."""
    monkeypatch.delenv('TELETHON_API_ID', raising=False)
    with pytest.raises(ConfigError) as exc:
        Settings()
    assert 'TELETHON_API_ID' in str(exc.value)


def test_all_missing_vars_reported_together(monkeypatch):
    for key in ('TELETHON_API_ID', 'TELETHON_API_HASH', 'TELEGRAM_BOT_TOKEN'):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ConfigError) as exc:
        Settings()
    message = str(exc.value)
    assert all(k in message for k in ('TELETHON_API_ID', 'TELETHON_API_HASH', 'TELEGRAM_BOT_TOKEN'))


def test_bad_integer_rejected(monkeypatch):
    monkeypatch.setenv('BACKEND_PORT', 'not-a-number')
    with pytest.raises(ConfigError):
        Settings()


def test_config_dict_excludes_secrets():
    exposed = Settings().to_dict()
    assert 'bot_token' not in exposed
    assert 'telethon_api_hash' not in exposed


# ----------------------------------------------------------------------
# End-to-end through the real API
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_message_endpoint_persists_and_extracts(authed_store):
    """Post a message to the running app and confirm it reaches the database."""
    from httpx import AsyncClient
    from pinlives_pro.api import backend as backend_module

    # Wire the module's globals the way startup() would, without opening a
    # Telegram connection.
    backend_module.persistence = authed_store
    backend_module.telethon_service = object()
    processor = asyncio.create_task(backend_module.message_processor())

    try:
        async with AsyncClient(app=backend_module.app, base_url='http://test') as client:
            resp = await client.post('/api/telethon/message', json={
                'phone': PHONE,
                'chat_id': CHANNEL,
                'chat_name': 'Kenh Kin',
                'message_id': 4242,
                'text': 'Ma hom nay: Zx9K2mQ7',
                'has_media': False,
            })
            assert resp.status_code == 200
            assert resp.json()['queued'] is True

            for _ in range(40):
                await asyncio.sleep(0.1)
                if authed_store.count_messages() >= 1:
                    break

        assert authed_store.count_messages() == 1, "message never reached the database"
        assert [c['code'] for c in authed_store.get_codes()] == ['Zx9K2mQ7']
    finally:
        processor.cancel()
        backend_module.persistence = None
        backend_module.telethon_service = None


@pytest.mark.asyncio
async def test_message_without_phone_is_rejected(authed_store):
    """A payload missing 'phone' used to be accepted and then silently dropped."""
    from httpx import AsyncClient
    from pinlives_pro.api import backend as backend_module

    backend_module.persistence = authed_store
    backend_module.telethon_service = object()
    try:
        async with AsyncClient(app=backend_module.app, base_url='http://test') as client:
            resp = await client.post('/api/telethon/message', json={
                'chat_id': CHANNEL,
                'chat_name': 'Kenh Kin',
                'message_id': 1,
                'text': 'hi',
            })
        assert resp.status_code == 422
    finally:
        backend_module.persistence = None
        backend_module.telethon_service = None


@pytest.mark.asyncio
async def test_unstorable_message_is_not_counted_as_processed(store):
    """A message that cannot be stored must raise, retry, and be dead-lettered.

    The original defect let save_message return None unnoticed: the API reported
    success, the counter incremented, and the message was gone. This test drives
    the failing path directly, so reverting that check fails the suite.
    """
    from pinlives_pro.api import backend as backend_module

    backend_module.persistence = store  # no session saved, so the write must fail
    try:
        with pytest.raises(RuntimeError):
            await backend_module.process_message({
                'phone': '+99900000000',
                'chat_id': CHANNEL,
                'chat_name': 'Ghost',
                'message_id': 1,
                'text': 'Code AB7X9Q2M',
                'has_media': False,
            })
        assert store.count_messages() == 0
    finally:
        backend_module.persistence = None


@pytest.mark.asyncio
async def test_failed_message_is_retried_then_dead_lettered(store, monkeypatch):
    """Exhausted retries must leave an audit record, not vanish."""
    from pinlives_pro.api import backend as backend_module

    backend_module.persistence = store
    # Collapse the backoff so the test does not wait 14 real seconds.
    monkeypatch.setattr(backend_module, 'RETRY_BASE_DELAY', 0.01)

    processor = asyncio.create_task(backend_module.message_processor())
    try:
        await backend_module.message_queue.enqueue({
            'phone': '+99900000000',
            'chat_id': CHANNEL,
            'chat_name': 'Ghost',
            'message_id': 77,
            'text': 'Code AB7X9Q2M',
            'has_media': False,
        })

        dead_letters = []
        for _ in range(60):
            await asyncio.sleep(0.1)
            dead_letters = [
                entry for entry in store.get_logs(limit=50)
                if 'dropped after' in (entry['message'] or '')
            ]
            if dead_letters:
                break

        assert dead_letters, "message vanished without a dead-letter record"
        assert store.count_messages() == 0
    finally:
        processor.cancel()
        backend_module.persistence = None


@pytest.mark.asyncio
async def test_endpoints_return_503_before_startup():
    """Requests that arrive before wiring completes must not raise AttributeError."""
    from httpx import AsyncClient
    from pinlives_pro.api import backend as backend_module

    backend_module.persistence = None
    backend_module.telethon_service = None
    async with AsyncClient(app=backend_module.app, base_url='http://test') as client:
        resp = await client.get('/api/codes')
    assert resp.status_code == 503
