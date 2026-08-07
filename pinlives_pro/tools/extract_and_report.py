"""
Connect to Telegram, pull the latest posts from each configured channel, run the
code filter, and print a per-channel / per-site report.

Run this where Telegram is reachable (a VPS or your machine). This sandbox lets
the TCP handshake to Telegram's DCs through but filters the MTProto data layer,
so a live fetch times out here — the report logic below is exercised by
tools/report_selftest.py without a connection.

Channels come from the system's database (registered via the bot, each with a
site_id). With --dialogs the account's own channel list is used instead, and the
site is guessed from the title.

Usage:
    python -m pinlives_pro.tools.extract_and_report [--per-channel 10] [--dialogs]
"""

import argparse
import asyncio
import sys
from collections import defaultdict
from typing import Dict, List, Optional

from ..core.config import load_settings_or_exit
from ..core.filters import extract_codes_for_site
from ..core.logging_setup import setup_logging


SITE_TOKENS = [
    'c168', 'sc88', 'f8bet', 'hi88', 'jun88', '33win', '79king', '8kbet', 'qq88',
    'gg88', 'mm88', 'rr88', 'xx88', 'f168', 'fly88', 'j88', 'new88', 'ok8386',
    'open88', 'cm88', 'mb66', 'oklive', '789bet', 'shbet',
]


def guess_site(title: str) -> str:
    t = (title or '').lower().replace(' ', '')
    for s in SITE_TOKENS:
        if s in t:
            return s
    return ''


async def gather(per_channel: int, use_dialogs: bool) -> Dict:
    settings = load_settings_or_exit()
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from ..core.persistence import get_persistence

    store = get_persistence()
    session_string = store.load_session(settings.telethon_phone)
    if not session_string:
        print("No stored session. Import one first:\n"
              "  python -m pinlives_pro.tools.import_session <file.session> "
              f"{settings.telethon_phone}", file=sys.stderr)
        return {}

    client = TelegramClient(StringSession(session_string),
                            settings.telethon_api_id, settings.telethon_api_hash,
                            connection_retries=2, timeout=20)

    targets: List[Dict] = []
    report: Dict[str, Dict] = {}
    await asyncio.wait_for(client.connect(), timeout=30)
    if not await client.is_user_authorized():
        print("Stored session is not authorized.", file=sys.stderr)
        await client.disconnect()
        return {}

    try:
        if use_dialogs:
            async for d in client.iter_dialogs():
                if d.is_channel or d.is_group:
                    targets.append({'chat_id': d.id, 'name': d.name,
                                    'site_id': guess_site(d.name)})
        else:
            for ch in store.get_channels(settings.telethon_phone):
                targets.append({'chat_id': ch['channel_id'],
                                'name': ch['channel_name'], 'site_id': ch['site_id']})

        for tgt in targets:
            posts, codes = 0, []
            try:
                async for msg in client.iter_messages(tgt['chat_id'], limit=per_channel):
                    posts += 1
                    text = msg.text or ''
                    codes.extend(extract_codes_for_site(text, tgt['site_id'], source='text'))
            except Exception as e:
                report[tgt['name']] = {'error': f"{type(e).__name__}: {e}"}
                continue
            report[tgt['name']] = {
                'site_id': tgt['site_id'] or '(none)',
                'posts_read': posts,
                'codes': sorted(set(codes)),
            }
    finally:
        await client.disconnect()
    return report


def print_report(report: Dict):
    if not report:
        print("No channels reported.")
        return
    total_codes = 0
    print(f"\n{'channel':40} {'site':8} {'posts':>5} {'codes':>5}")
    print("-" * 70)
    for name, r in report.items():
        if 'error' in r:
            print(f"{name[:40]:40} {'ERROR':8} {r['error'][:30]}")
            continue
        n = len(r['codes'])
        total_codes += n
        print(f"{name[:40]:40} {r['site_id']:8} {r['posts_read']:5} {n:5}")
        for c in r['codes']:
            print(f"      -> {c}")
    print("-" * 70)
    print(f"channels: {len(report)}   codes extracted: {total_codes}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--per-channel', type=int, default=10)
    ap.add_argument('--dialogs', action='store_true',
                    help="use the account's own channel list instead of the configured one")
    args = ap.parse_args()

    settings = load_settings_or_exit()
    setup_logging(level=settings.log_level, log_file=settings.log_file, component='pinlives.extract')
    try:
        report = asyncio.run(gather(args.per_channel, args.dialogs))
    except asyncio.TimeoutError:
        print("Timed out connecting to Telegram. This environment filters MTProto; "
              "run where Telegram is reachable.", file=sys.stderr)
        return 1
    print_report(report)
    return 0


if __name__ == '__main__':
    sys.exit(main())
