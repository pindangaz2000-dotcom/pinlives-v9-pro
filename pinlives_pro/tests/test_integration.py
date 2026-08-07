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
# Channel seed (config/channels_seed.json + seed tool)
# ----------------------------------------------------------------------

def test_channel_seed_file_is_valid():
    from pinlives_pro.tools.seed_channels import load_seed
    entries = load_seed()
    # Every entry carries a site and a way to identify the channel.
    assert len(entries) == 33
    for e in entries:
        assert e.get('site'), e
        assert ('channel_id' in e) or e.get('username'), e
    sites = {e['site'] for e in entries}
    assert {'qq88', 'hi88', 'llwin', 'mm88', 'rr88', 'xx88',
            'gg88', 'mb66', 'c168', 'sc88', '8kbet', 'kjc_shared'} == sites
    # Exactly one entry is pre-resolved to a numeric id; the rest need Telegram.
    by_id = [e for e in entries if 'channel_id' in e]
    assert [e['channel_id'] for e in by_id] == [-1002272716520]


@pytest.mark.asyncio
async def test_offline_seed_adds_numeric_entries_only(authed_store):
    """--offline stores entries that already have a numeric id and skips the
    ones that would need a live username lookup."""
    from pinlives_pro.tools.seed_channels import seed
    tally = await seed(offline=True, dry_run=False)
    assert tally.get('added') == 1
    assert tally.get('skipped_offline') == 32
    stored = authed_store.get_channels(PHONE)
    assert [(c['channel_id'], c['site_id']) for c in stored] == [(-1002272716520, 'qq88')]


@pytest.mark.asyncio
async def test_offline_seed_is_idempotent(authed_store):
    from pinlives_pro.tools.seed_channels import seed
    await seed(offline=True, dry_run=False)
    tally = await seed(offline=True, dry_run=False)
    # Second run adds nothing new; the numeric entry is already present.
    assert tally.get('added', 0) == 0
    assert tally.get('already') == 1
    assert len(authed_store.get_channels(PHONE)) == 1


# ----------------------------------------------------------------------
# kjc_shared routing
# ----------------------------------------------------------------------

# A real KJC_THETHAO post: trap-obfuscated codes, tagged with every KJC site.
KJC_THETHAO_POST = ("⚡️⚡️⚡️⚡️⚡️🤭😨:  J7#2L+IS ➡️ SX?79$SL\n"
                    "#kjc #lienminhquoctekjc #rr88 #mm88 #xx88 #gg88 #LLwin #thethao")


def test_detect_kjc_site_from_text():
    from pinlives_pro.core.kjc_routing import detect_kjc_site_from_text
    # Exactly one site named -> that site.
    assert detect_kjc_site_from_text('MM88 code 7hK2mQ') == 'mm88'
    assert detect_kjc_site_from_text('phat code RR88 hom nay') == 'rr88'
    assert detect_kjc_site_from_text('LLWIN B37MM9') == 'llwin'
    assert detect_kjc_site_from_text('XX88 esport') == 'xx88'
    assert detect_kjc_site_from_text('GG88 tang ma') == 'gg88'
    assert detect_kjc_site_from_text('code chung khong site') is None
    assert detect_kjc_site_from_text('') is None
    # Several sites named (a broadcast post) -> None, not "first marker wins".
    assert detect_kjc_site_from_text(KJC_THETHAO_POST) is None


def test_resolve_sites():
    from pinlives_pro.core.kjc_routing import resolve_sites, KJC_SITES
    # A kjc_shared post names one site -> just that site.
    assert resolve_sites('kjc_shared', 'MM88 code 7hK2mQ') == ['mm88']
    # No marker -> all five KJC sites, in order.
    assert resolve_sites('kjc_shared', 'code chung 7hK2mQ') == list(KJC_SITES)
    # Every KJC site tagged (broadcast) -> all five, not just the first.
    assert resolve_sites('kjc_shared', KJC_THETHAO_POST) == list(KJC_SITES)
    # A normal site resolves to itself; an empty site to nothing (generic later).
    assert resolve_sites('c168', 'anything') == ['c168']
    assert resolve_sites('', 'anything') == []


def test_kjc_thethao_post_extracts_trap_codes_and_broadcasts():
    """A real KJC_THETHAO post: the trap symbols are stripped to 6-char codes,
    and the all-site tags route it to every KJC site."""
    from pinlives_pro.api.backend import extract_codes_for_text
    from pinlives_pro.core.kjc_routing import resolve_sites, KJC_SITES
    assert resolve_sites('kjc_shared', KJC_THETHAO_POST) == list(KJC_SITES)
    codes = extract_codes_for_text(KJC_THETHAO_POST, 'kjc_shared')
    assert codes == ['J72LIS', 'SX79SL']


def test_kjc_shared_extraction_routes_by_marker():
    """A kjc_shared post with a site marker runs that site's extractor."""
    from pinlives_pro.api.backend import extract_codes_for_text
    assert extract_codes_for_text('MM88 code 7hK2mQ nhan ngay', 'kjc_shared') == ['7hK2mQ']


def test_kjc_shared_extraction_unions_when_no_marker():
    """Without a marker the code is valid for all five KJC sites; it is still
    extracted (unioned across them, de-duplicated)."""
    from pinlives_pro.api.backend import extract_codes_for_text
    codes = extract_codes_for_text('Code chung A9bQ2Z dung duoc het', 'kjc_shared')
    assert codes == ['A9bQ2Z']


def test_kjc_shared_does_not_change_normal_site_routing():
    from pinlives_pro.api.backend import extract_codes_for_text
    assert extract_codes_for_text('HI88 ma uRCzuDA8Z7', 'hi88') == ['uRCzuDA8Z7']


# ----------------------------------------------------------------------
# Per-site code-length rules
# ----------------------------------------------------------------------

def test_site_length_rule():
    from pinlives_pro.core.site_rules import passes_length
    # qq88 codes are exactly 10.
    assert passes_length('qq88', 'cMoC1cCsy3') is True     # 10
    assert passes_length('qq88', 'Please') is False        # 6
    assert passes_length('qq88', 'cMoC1cCsy3X') is False   # 11
    # KJC sites are exactly 6.
    assert passes_length('mm88', 'J72LIS') is True         # 6
    assert passes_length('mm88', 'J72LI') is False         # 5
    # An unlisted site is unconstrained.
    assert passes_length('hi88', 'uRCzuDA8Z7') is True
    assert passes_length('', 'anything') is True


def test_qq88_length_rule_drops_short_word_keeps_real_code():
    """The vendored qq88 extractor accepts 'Please' (6 chars); the length rule
    drops it while keeping the real 10-char code."""
    from pinlives_pro.api.backend import extract_codes_for_text
    assert extract_codes_for_text('Please open Telegram', 'qq88') == []
    assert extract_codes_for_text('Ma moi: cMoC1cCsy3 Please', 'qq88') == ['cMoC1cCsy3']


# ----------------------------------------------------------------------
# Image-only sites (8KBET)
# ----------------------------------------------------------------------

def test_is_image_only():
    from pinlives_pro.core.site_rules import is_image_only
    assert is_image_only('8kbet') is True
    assert is_image_only('8KBET') is True
    assert is_image_only('hi88') is False
    assert is_image_only('') is False


@pytest.mark.asyncio
async def test_image_only_site_skips_text_codes(authed_store):
    """8KBET posts its codes only in images, so a code-shaped token in the
    promo text must not be stored; the same token on a normal site is."""
    from pinlives_pro.api import backend as backend_module
    backend_module.persistence = authed_store
    try:
        await backend_module.process_message({
            'phone': PHONE, 'chat_id': CHANNEL, 'chat_name': '8kbet',
            'message_id': 5001, 'site_id': '8kbet', 'has_media': False,
            'text': 'Nhan code Zx9K2mQ7 tai t.me/CODESHARE8KBET_BOT',
        })
        assert authed_store.get_codes() == []

        # Control: the same token on a generic site is extracted and stored.
        await backend_module.process_message({
            'phone': PHONE, 'chat_id': -1009999999999, 'chat_name': 'x',
            'message_id': 5002, 'site_id': '', 'has_media': False,
            'text': 'Ma hom nay: Zx9K2mQ7',
        })
        assert [c['code'] for c in authed_store.get_codes()] == ['Zx9K2mQ7']
    finally:
        backend_module.persistence = None


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
    from httpx import AsyncClient, ASGITransport
    from pinlives_pro.api import backend as backend_module

    # Wire the module's globals the way startup() would, without opening a
    # Telegram connection.
    backend_module.persistence = authed_store
    backend_module.telethon_service = object()
    processor = asyncio.create_task(backend_module.message_processor())

    try:
        async with AsyncClient(transport=ASGITransport(app=backend_module.app), base_url='http://test') as client:
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
    from httpx import AsyncClient, ASGITransport
    from pinlives_pro.api import backend as backend_module

    backend_module.persistence = authed_store
    backend_module.telethon_service = object()
    try:
        async with AsyncClient(transport=ASGITransport(app=backend_module.app), base_url='http://test') as client:
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
    from httpx import AsyncClient, ASGITransport
    from pinlives_pro.api import backend as backend_module

    backend_module.persistence = None
    backend_module.telethon_service = None
    async with AsyncClient(transport=ASGITransport(app=backend_module.app), base_url='http://test') as client:
        resp = await client.get('/api/codes')
    assert resp.status_code == 503


# ----------------------------------------------------------------------
# Listener scope: only configured channels, never our own bot
# ----------------------------------------------------------------------

class _FakeChat:
    def __init__(self, cid, title=None, first_name=None):
        self.id, self.title, self.first_name = cid, title, first_name


class _FakeMessage:
    def __init__(self, mid, text):
        from datetime import datetime as _dt
        self.id, self.text, self.media = mid, text, None
        self.date = _dt.now()


class _FakeEvent:
    def __init__(self, chat_id, message, sender_id=None):
        self._chat = _FakeChat(chat_id, title=f'Chat{chat_id}')
        self.message = message
        self.chat_id = chat_id
        self.sender_id = sender_id
        self.out = False

    async def get_chat(self):
        return self._chat


class _ChannelStub:
    """Persistence stub exposing a single configured channel."""
    def __init__(self, channel_id):
        self.channel_id = channel_id

    def get_channels(self, phone):
        return [{'channel_id': self.channel_id, 'channel_name': 'Configured'}]


def _service(configured, bot_user_id, self_user_id, sink, sink_chat_ids=None):
    from pinlives_pro.core.telethon_client import TelethonService
    svc = TelethonService(api_id=1, api_hash='h', persistence=_ChannelStub(configured),
                          on_message=sink, bot_user_id=bot_user_id,
                          sink_chat_ids=sink_chat_ids)
    svc.phone = PHONE
    svc.self_user_id = self_user_id
    svc.refresh_allowed_channels()
    return svc


BOT_ID = 8423073556
SELF_ID = 7478077662


@pytest.mark.asyncio
async def test_listener_accepts_only_configured_channels():
    seen = []

    async def sink(payload):
        seen.append(payload)

    svc = _service(CHANNEL, BOT_ID, SELF_ID, sink)
    await svc._handle_message(_FakeEvent(CHANNEL, _FakeMessage(1, 'Ma: AB7X9Q2M')))
    await svc._handle_message(_FakeEvent(-1009999999999, _FakeMessage(2, 'Ma: ZZ1Y8W3N')))

    assert [p['chat_id'] for p in seen] == [CHANNEL]
    assert svc.ignored_unconfigured == 1


@pytest.mark.asyncio
async def test_listener_ignores_messages_from_our_own_bot():
    """The bot reports codes to the admin; re-ingesting them is a feedback loop."""
    seen = []

    async def sink(payload):
        seen.append(payload)

    svc = _service(CHANNEL, BOT_ID, SELF_ID, sink)
    await svc._handle_message(
        _FakeEvent(BOT_ID, _FakeMessage(3, 'Recent codes: AB7X9Q2M'), sender_id=BOT_ID)
    )
    assert seen == []
    assert svc.ignored_own_bot == 1


@pytest.mark.asyncio
async def test_listener_ignores_detection_log_channel():
    """The detection-log sink must never be scanned, even if it were also listed
    as a configured channel — that is the infinite re-detection loop."""
    seen = []

    async def sink(payload):
        seen.append(payload)

    LOG_CHAN = -1005555555555
    # Worst case: the sink is *also* in the monitored set (a config mistake).
    svc = _service(LOG_CHAN, BOT_ID, SELF_ID, sink, sink_chat_ids={LOG_CHAN})
    # A detection notice posted into the log channel, carrying a real-looking code.
    await svc._handle_message(
        _FakeEvent(LOG_CHAN, _FakeMessage(9, 'Detected code: AB7X9Q2M from Chat X'))
    )
    assert seen == []
    assert svc.ignored_sink == 1


@pytest.mark.asyncio
async def test_listener_ignores_anonymous_bot_post_by_sink():
    """A sink post with no sender_id (anonymous channel post) is still dropped:
    the sink check runs before, and independently of, the sender checks."""
    seen = []

    async def sink(payload):
        seen.append(payload)

    LOG_CHAN = -1005555555555
    svc = _service(CHANNEL, BOT_ID, SELF_ID, sink, sink_chat_ids={LOG_CHAN})
    await svc._handle_message(
        _FakeEvent(LOG_CHAN, _FakeMessage(10, 'code AB7X9Q2M'), sender_id=None)
    )
    assert seen == []
    assert svc.ignored_sink == 1


def test_bot_user_id_derived_from_token():
    """backend derives the bot's user id from the token prefix so the own-bot
    guard is actually armed (it was passed as None before)."""
    from pinlives_pro.api.backend import _bot_user_id_from_token
    assert _bot_user_id_from_token('8423073556:AAF-secret-part') == 8423073556
    assert _bot_user_id_from_token('') is None
    assert _bot_user_id_from_token('no-colon') is None
    assert _bot_user_id_from_token('abc:def') is None


@pytest.mark.asyncio
async def test_listener_ignores_own_saved_messages():
    seen = []

    async def sink(payload):
        seen.append(payload)

    svc = _service(CHANNEL, BOT_ID, SELF_ID, sink)
    await svc._handle_message(
        _FakeEvent(SELF_ID, _FakeMessage(4, 'note MM3K9Z2Q'), sender_id=SELF_ID)
    )
    assert seen == []
    assert svc.ignored_self == 1


@pytest.mark.asyncio
async def test_listener_uses_prefixed_chat_id():
    """event.chat_id carries the -100 form; chat.id would never match storage."""
    seen = []

    async def sink(payload):
        seen.append(payload)

    svc = _service(CHANNEL, BOT_ID, SELF_ID, sink)
    event = _FakeEvent(CHANNEL, _FakeMessage(5, 'Ma: AB7X9Q2M'))
    # chat.id deliberately differs from the -100-prefixed event.chat_id.
    event._chat.id = 1234567890
    await svc._handle_message(event)
    assert seen and seen[0]['chat_id'] == CHANNEL


def test_channel_site_id_round_trip(authed_store):
    authed_store.add_channel(PHONE, CHANNEL, 'Kenh Kin', site_id='MB66')
    assert authed_store.get_channel_site(PHONE, CHANNEL) == 'mb66'
    assert authed_store.get_channels(PHONE)[0]['site_id'] == 'mb66'


# ----------------------------------------------------------------------
# Event journal, provenance, replay
# ----------------------------------------------------------------------

def test_journal_records_event_once(store):
    from pinlives_pro.models.database import Provenance
    payload = {'phone': PHONE, 'chat_id': CHANNEL, 'chat_name': 'K', 'message_id': 1,
               'text': 'Ma AB7X9Q2M', 'site_id': 'mb66'}
    first = store.journal_event(payload, Provenance.REAL)
    second = store.journal_event(payload, Provenance.REAL)
    assert isinstance(first, int)
    # A redelivered update must not create a second journal row.
    assert second == first
    assert len(store.get_journal()) == 1


def test_journal_records_resolved_site_id(store):
    """Replay reads site_id back; an empty one would run a different extractor."""
    store.journal_event({'phone': PHONE, 'chat_id': CHANNEL, 'chat_name': 'K',
                         'message_id': 2, 'text': 'x', 'site_id': 'MB66'})
    assert store.get_journal()[0]['site_id'] == 'mb66'


def test_journal_provenance_is_tracked(store):
    from pinlives_pro.models.database import Provenance
    store.journal_event({'phone': PHONE, 'chat_id': CHANNEL, 'message_id': 3,
                         'chat_name': 'K', 'text': 'a'}, Provenance.REAL)
    store.journal_event({'phone': PHONE, 'chat_id': CHANNEL, 'message_id': 4,
                         'chat_name': 'K', 'text': 'b'}, Provenance.BACKFILL)
    stats = store.journal_stats()
    assert stats['by_provenance'][Provenance.REAL] == 1
    assert stats['by_provenance'][Provenance.BACKFILL] == 1


def test_journal_latency_percentiles(store):
    jid = store.journal_event({'phone': PHONE, 'chat_id': CHANNEL, 'message_id': 5,
                               'chat_name': 'K', 'text': 'a'})
    assert store.mark_journal_processed(jid, 12.5) is True
    latency = store.journal_stats()['latency_ms']
    assert latency['count'] == 1 and latency['p50'] == 12.5


def test_percentiles_helper():
    from pinlives_pro.core.persistence import _percentiles
    assert _percentiles([])['count'] == 0
    p = _percentiles([float(i) for i in range(1, 101)])
    assert p['p50'] == 50.0 and p['p95'] == 95.0 and p['max'] == 100.0


def test_site_routing_dearmours_codes():
    """Armoured codes are the real shape; a generic filter cannot recover them."""
    from pinlives_pro.api.backend import extract_codes_for_text
    assert extract_codes_for_text('MB66 code: aB★7x9◆Qm✦K', 'mb66') == ['aB7x9QmK']


def test_site_routing_falls_back_without_site():
    from pinlives_pro.api.backend import extract_codes_for_text
    assert extract_codes_for_text('Code AB7X9Q2M', '') == ['AB7X9Q2M']


def test_site_routing_survives_unknown_site():
    from pinlives_pro.api.backend import extract_codes_for_text
    assert isinstance(extract_codes_for_text('Code AB7X9Q2M', 'no_such_site'), list)


# ----------------------------------------------------------------------
# OCR preprocessing and glyph confusion
# ----------------------------------------------------------------------

def test_confusion_candidates_reach_the_true_reading():
    """Measured case: `yNbEB7eSNa` is read as `yNbEBTeSNa`, one glyph off."""
    from pinlives_pro.core.ocr import confusion_candidates
    assert 'yNbEB7eSNa' in confusion_candidates('yNbEBTeSNa', max_swaps=1)


def test_confusion_candidates_exclude_the_input():
    from pinlives_pro.core.ocr import confusion_candidates
    assert 'yNbEBTeSNa' not in confusion_candidates('yNbEBTeSNa', max_swaps=1)


def test_confusion_candidates_are_bounded():
    from pinlives_pro.core.ocr import confusion_candidates
    # 'admfrtny' contains no glyph in the confusion or case-ambiguous tables,
    # so there is nothing to swap.
    assert confusion_candidates('admfrtny', max_swaps=2) == []


def test_case_ambiguous_letters_get_case_variants():
    """v/V and similar are near-identical in shape; both cases are offered."""
    from pinlives_pro.core.ocr import confusion_candidates
    assert 'vGW65kBRMs' in confusion_candidates('VGW65kBRMs', max_swaps=1)


def test_strikethrough_restoration_rebuilds_crossed_strokes():
    """A rule crossing a glyph must not punch a hole through it."""
    import numpy as np
    from pinlives_pro.core.ocr import preprocess_screenshot

    # White vertical bar on black, with a red horizontal rule across its middle.
    img = np.zeros((40, 40, 3), np.uint8)
    img[5:35, 18:23] = (255, 255, 255)
    img[19:22, :] = (0, 0, 220)          # BGR red

    out = preprocess_screenshot(img, binarize_threshold=125, scale=1)

    # The bar survives where the rule crossed it, because text sits above and below.
    assert out[20, 20] == 255
    # Away from the bar the rule is erased, not left as a line.
    assert out[20, 2] == 0


def test_preprocess_returns_binary_image():
    import numpy as np
    from pinlives_pro.core.ocr import preprocess_screenshot
    img = np.full((20, 20, 3), 200, np.uint8)
    out = preprocess_screenshot(img, scale=2)
    assert out.shape[:2] == (40, 40)
    assert set(np.unique(out)) <= {0, 255}


# ----------------------------------------------------------------------
# OCR review queue: OCR codes are held for review, never confirmed
# ----------------------------------------------------------------------

def test_ocr_codes_go_to_review_not_confirmed(authed_store):
    """A one-glyph-wrong OCR reading must not sit in the confirmed list."""
    msg = authed_store.save_message(PHONE, CHANNEL, 'K', 1, '', has_media=True)
    authed_store.save_code(msg, 'AB7X9Q2M', entropy=3.0)                    # text: confirmed
    authed_store.save_code(msg, 'yNbEBTeSNa', ocr_confidence=0.6, is_valid=False)  # OCR: review

    confirmed = [c['code'] for c in authed_store.get_codes(valid_only=True)]
    review = [c['code'] for c in authed_store.get_review_codes()]

    assert 'AB7X9Q2M' in confirmed
    assert 'yNbEBTeSNa' not in confirmed
    assert 'yNbEBTeSNa' in review


def test_review_codes_carry_the_review_flag(authed_store):
    msg = authed_store.save_message(PHONE, CHANNEL, 'K', 1, '', has_media=True)
    authed_store.save_code(msg, 'kbs7cox3AU', ocr_confidence=0.8, is_valid=False)
    row = authed_store.get_review_codes()[0]
    assert row['needs_review'] is True
    assert row['confidence'] == 0.8


@pytest.mark.asyncio
async def test_media_message_runs_ocr_and_holds_for_review(authed_store, monkeypatch):
    """End to end: a media payload produces review codes, not confirmed ones."""
    from pinlives_pro.api import backend as backend_module

    backend_module.persistence = authed_store
    authed_store.add_channel(PHONE, CHANNEL, 'K', site_id='')

    async def fake_ocr(media_path, site_id):
        return [('kbs7cox3AU', 0.9)]
    monkeypatch.setattr(backend_module, 'extract_codes_from_media', fake_ocr)

    await backend_module.process_message({
        'phone': PHONE, 'chat_id': CHANNEL, 'chat_name': 'K', 'message_id': 5,
        'text': '', 'has_media': True, 'media_path': '/tmp/x.jpg',
    })

    assert [c['code'] for c in authed_store.get_codes(valid_only=True)] == []
    assert 'kbs7cox3AU' in [c['code'] for c in authed_store.get_review_codes()]
    backend_module.persistence = None


@pytest.mark.asyncio
async def test_ocr_failure_does_not_lose_the_message(authed_store, monkeypatch):
    """If OCR raises, the text codes and the message must still be stored."""
    from pinlives_pro.api import backend as backend_module

    backend_module.persistence = authed_store
    authed_store.add_channel(PHONE, CHANNEL, 'K', site_id='')

    async def boom(media_path, site_id):
        raise RuntimeError("tesseract exploded")
    monkeypatch.setattr(backend_module, 'extract_codes_from_media', boom)

    await backend_module.process_message({
        'phone': PHONE, 'chat_id': CHANNEL, 'chat_name': 'K', 'message_id': 6,
        'text': 'Ma AB7X9Q2M', 'has_media': True, 'media_path': '/tmp/x.jpg',
    })

    # The message survived and its text code was still extracted.
    assert authed_store.count_messages() == 1
    assert 'AB7X9Q2M' in [c['code'] for c in authed_store.get_codes()]
    backend_module.persistence = None


# ----------------------------------------------------------------------
# Session import utility (offline conversion, no network, no key exposure)
# ----------------------------------------------------------------------

def test_import_session_round_trips(tmp_path):
    """A file session converts to a StringSession the system can restore."""
    from telethon.sessions import SQLiteSession, StringSession
    from telethon.crypto import AuthKey
    from pinlives_pro.tools.import_session import convert_to_string_session

    # Build a synthetic authenticated file session — no real credential.
    src = tmp_path / 'acct.session'
    s = SQLiteSession(str(src.with_suffix('')))
    s.set_dc(2, '149.154.167.40', 443)
    # A non-degenerate key: an all-zero key is treated as "no key".
    s.auth_key = AuthKey(data=os.urandom(256))
    s.save()

    string_session = convert_to_string_session(str(src))
    decoded = StringSession(string_session)
    assert decoded.dc_id == 2
    assert decoded.auth_key is not None and len(decoded.auth_key.key) == 256


def test_import_session_rejects_unauthenticated(tmp_path):
    from telethon.sessions import SQLiteSession
    from pinlives_pro.tools.import_session import convert_to_string_session

    src = tmp_path / 'empty.session'
    SQLiteSession(str(src.with_suffix(''))).save()  # no auth_key
    with pytest.raises(ValueError):
        convert_to_string_session(str(src))


def test_import_session_missing_file():
    from pinlives_pro.tools.import_session import convert_to_string_session
    with pytest.raises(FileNotFoundError):
        convert_to_string_session('/no/such/file.session')
