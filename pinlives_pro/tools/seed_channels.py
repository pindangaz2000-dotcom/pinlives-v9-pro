"""
Seed the monitored-channel allowlist from config/channels_seed.json.

The seed lists channels by @username (and a few by numeric id). The system's
allowlist matches on the numeric -100 channel id — what event.chat_id reports —
so a username has to be resolved to that id before it can be stored. Resolution
needs Telegram to be reachable and a stored, authorised session; this sandbox
filters MTProto, so run this on a networked machine.

  python -m pinlives_pro.tools.seed_channels                # resolve + store all
  python -m pinlives_pro.tools.seed_channels --offline      # only entries that
                                                            # already carry a
                                                            # numeric channel_id
  python -m pinlives_pro.tools.seed_channels --dry-run      # resolve, print, no write

After seeding, the running backend picks the new channels up on its next
refresh_allowed_channels() (add via the bot triggers one; a restart also does).
"""

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List

from ..core.config import load_settings_or_exit
from ..core.logging_setup import setup_logging

SEED_PATH = Path(__file__).resolve().parents[1] / "config" / "channels_seed.json"


def load_seed(path: Path = SEED_PATH) -> List[Dict]:
    """Read the seed file and return its channel entries, validated."""
    data = json.loads(path.read_text(encoding="utf-8"))
    channels = data.get("channels", [])
    cleaned: List[Dict] = []
    for i, ch in enumerate(channels):
        site = (ch.get("site") or "").strip().lower()
        if not site:
            raise ValueError(f"entry {i} has no site: {ch!r}")
        if "channel_id" not in ch and not ch.get("username"):
            raise ValueError(f"entry {i} has neither channel_id nor username: {ch!r}")
        cleaned.append(ch)
    return cleaned


def _entry_name(ch: Dict) -> str:
    return ch.get("name") or ch.get("username") or str(ch.get("channel_id"))


async def seed(offline: bool, dry_run: bool) -> Dict[str, int]:
    settings = load_settings_or_exit()
    from ..core.persistence import get_persistence

    store = get_persistence()
    phone = settings.telethon_phone
    if not store.load_session(phone):
        print("No stored session. Import one first:\n"
              f"  python -m pinlives_pro.tools.import_session <file.session> {phone}",
              file=sys.stderr)
        return {}

    entries = load_seed()
    existing = {c["channel_id"] for c in store.get_channels(phone)}
    tally = Counter()

    client = None
    if not offline:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        from telethon.utils import get_peer_id

        client = TelegramClient(
            StringSession(store.load_session(phone)),
            settings.telethon_api_id, settings.telethon_api_hash,
            connection_retries=2, timeout=20,
        )
        await asyncio.wait_for(client.connect(), timeout=30)
        if not await client.is_user_authorized():
            print("Stored session is not authorized.", file=sys.stderr)
            await client.disconnect()
            return {}

    print(f"\n{'channel':28} {'site':10} {'channel_id':>16}  result")
    print("-" * 72)
    try:
        for ch in entries:
            name = _entry_name(ch)
            site = (ch["site"] or "").strip().lower()
            channel_id = ch.get("channel_id")

            # Resolve a username to its numeric -100 id when online.
            if channel_id is None:
                if offline:
                    print(f"{name[:28]:28} {site:10} {'—':>16}  skip (needs resolve)")
                    tally['skipped_offline'] += 1
                    continue
                try:
                    entity = await client.get_entity(ch["username"])
                    channel_id = get_peer_id(entity)
                    name = getattr(entity, 'title', None) or name
                except Exception as e:
                    print(f"{name[:28]:28} {site:10} {'—':>16}  ERR {type(e).__name__}")
                    tally['resolve_failed'] += 1
                    continue

            if channel_id in existing:
                print(f"{name[:28]:28} {site:10} {channel_id:>16}  already")
                tally['already'] += 1
                continue

            if dry_run:
                print(f"{name[:28]:28} {site:10} {channel_id:>16}  would add")
                tally['would_add'] += 1
                continue

            if store.add_channel(phone, channel_id, name, site):
                existing.add(channel_id)
                print(f"{name[:28]:28} {site:10} {channel_id:>16}  added")
                tally['added'] += 1
            else:
                print(f"{name[:28]:28} {site:10} {channel_id:>16}  FAILED")
                tally['failed'] += 1
    finally:
        if client is not None:
            await client.disconnect()

    print("-" * 72)
    print("  ".join(f"{k}={v}" for k, v in sorted(tally.items())) or "nothing to do")
    return dict(tally)


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed the monitored-channel allowlist.")
    ap.add_argument('--offline', action='store_true',
                    help="only add entries that already carry a numeric channel_id")
    ap.add_argument('--dry-run', action='store_true',
                    help="resolve and print, but do not write")
    args = ap.parse_args()

    settings = load_settings_or_exit()
    setup_logging(level=settings.log_level, log_file=settings.log_file,
                  component='pinlives.seed')
    try:
        asyncio.run(seed(args.offline, args.dry_run))
    except asyncio.TimeoutError:
        print("Timed out connecting to Telegram. This environment filters MTProto; "
              "run where Telegram is reachable, or use --offline.", file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
