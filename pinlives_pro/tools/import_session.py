"""
Import an existing Telethon session into the system.

Authenticating once (OTP or QR) produces a session; re-doing that on every
deploy is avoidable. This converts a Telethon file-session (a .session SQLite
DB) into the StringSession the system stores, and saves it via the persistence
layer, so a real deployment's restore() reconnects with no OTP.

The conversion is offline — it re-encodes the stored auth_key and datacentre,
it does not connect to Telegram. The auth_key is never printed.

SECURITY: a session grants full access to the Telegram account. Treat the input
file and the resulting database as secrets — do not commit either, and delete
the input file once imported.

Usage:
    python -m pinlives_pro.tools.import_session <path-to.session> <phone>
"""

import sys
from pathlib import Path

from telethon.sessions import SQLiteSession, StringSession


def convert_to_string_session(session_path: str) -> str:
    """Offline: load the file session and re-encode it as a StringSession."""
    path = Path(session_path)
    if not path.exists():
        raise FileNotFoundError(f"No such session file: {session_path}")

    # SQLiteSession appends '.session'; hand it the stem so it opens this file.
    stem = str(path.with_suffix('')) if path.suffix == '.session' else str(path)
    sqlite = SQLiteSession(stem)
    string_session = StringSession.save(sqlite)
    # An unauthenticated session encodes to an empty string; reject it clearly
    # rather than storing something restore() cannot use.
    if not string_session or sqlite.auth_key is None:
        raise ValueError("Session has no auth_key — it is not authenticated.")
    return string_session


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2

    session_path, phone = sys.argv[1], sys.argv[2]
    try:
        string_session = convert_to_string_session(session_path)
    except Exception as e:
        print(f"Conversion failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    # Imported directly here so the module can be inspected without a full env.
    from ..core.persistence import get_persistence

    store = get_persistence()
    if not store.save_session(phone, string_session):
        print("Failed to store the session in the database.", file=sys.stderr)
        return 1
    store.mark_authenticated(phone)

    # Confirmation only — never the key or the string.
    print(f"Imported session for {phone} "
          f"({len(string_session)} chars, stored and marked authenticated).")
    print("Delete the source .session file now; it is a live credential.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
