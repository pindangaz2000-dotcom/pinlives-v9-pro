# PINLIVES Pro — Deployment Runbook

How to take the system from a fresh clone to a running deploy that captures
codes from the configured Telegram channels.

> **This must run where Telegram is reachable** (a VPS or your own machine).
> The MTProto data layer is what the client speaks; sandboxes and some networks
> let the TCP handshake through but drop MTProto, so a login there times out.

---

## 1. Prerequisites

- Linux host with outbound access to Telegram (MTProto, port 443).
- Python 3.11.
- `tesseract-ocr` binary **only if** you want the OCR fallback
  (`apt-get install -y tesseract-ocr`); the primary OCR is RapidOCR and needs no
  system package.
- A Telegram **user account** (phone number) that is a member of the channels
  to monitor, plus a **bot** from @BotFather for the control panel.

## 2. Install

```bash
git clone <repo-url> pinlives && cd pinlives
python3.11 -m venv venv && source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python -m pytest pinlives_pro/tests -q      # sanity: should report all passed
```

## 3. Configure

```bash
cp .env.example .env
```

Fill in `.env` (required keys — the app refuses to start without them, with a
message naming what is missing):

| Key | Where to get it |
|-----|-----------------|
| `TELETHON_API_ID`, `TELETHON_API_HASH` | https://my.telegram.org/apps |
| `TELETHON_PHONE` | the account's number, e.g. `+8438...` |
| `TELEGRAM_BOT_TOKEN` | @BotFather |
| `TELEGRAM_ADMIN_ID` | your numeric id from @userinfobot |

Optional but useful:

- `NOTIFY_CHAT_ID` — a chat the system posts detection notices to. It is
  **excluded from scanning** so an announced code never re-enters as a fresh one.
- `DB_TYPE=postgres` (+ `DB_*`) for Postgres; the default is a SQLite file at
  `DB_PATH` (`/tmp/pinlives_pro.db` — change it to a durable path).

> **Never commit `.env`.** It is gitignored. If a bot token has ever been
> committed, rotate it at @BotFather before going live.

## 4. Authenticate the user account (once)

The Telethon client runs inside the backend, so the login happens through the
bot, or by importing an existing session.

**Option A — log in through the bot** (recommended, no file handling):
start the backend and bot (section 6), open the bot in Telegram, send `/start`,
and follow the login flow (phone code, and 2FA password if enabled). The session
is stored in the database and restored automatically on restart.

**Option B — import an existing session file:**
```bash
python -m pinlives_pro.tools.import_session /path/to/account.session "$TELETHON_PHONE"
# then delete the .session file — it is a live credential
```

> Do not reuse a session that has been shared anywhere; log out that session in
> Telegram (Settings → Devices) and create a fresh one.

## 5. Seed the monitored channels

The allowlist maps each channel to the site whose code format its posts carry.
`config/channels_seed.json` holds the 33 channels ported from production.

```bash
python -m pinlives_pro.tools.seed_channels --dry-run   # resolve + preview
python -m pinlives_pro.tools.seed_channels             # resolve @usernames -> ids, store
```

Resolution needs the authenticated session (step 4). New channels are picked up
on the next `refresh_allowed_channels()` — adding one via the bot triggers it, a
restart also does.

## 6. Run

Two processes: the **backend** (FastAPI + the Telethon listener) and the **bot**
(operator control panel).

**Foreground (first run / debugging):**
```bash
python -m uvicorn pinlives_pro.api.backend:app --host 0.0.0.0 --port 8000
python -m pinlives_pro.bot.orchestrator      # in a second shell
```

**As services (production):** unit files are under
`pinlives_pro/infrastructure/systemd/`. Adjust `WorkingDirectory`, `User`, and
the venv path, then:
```bash
sudo cp pinlives_pro/infrastructure/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pinlives-backend pinlives-bot
```
Both units use `Restart=always`, so a crash is recovered automatically.

## 7. Verify it is live

```bash
curl -s localhost:8000/api/health        # {"status":"healthy", ...}
curl -s localhost:8000/api/status        # build id, journal stats, telethon state
curl -s localhost:8000/api/telethon/state
curl -s localhost:8000/api/channels      # the seeded allowlist
```

Captured codes:
```bash
curl -s localhost:8000/api/codes          # confirmed text codes
curl -s localhost:8000/api/codes/review   # OCR codes awaiting a human check
```

Seed real traffic to measure the filter before trusting it:
```bash
curl -s -X POST localhost:8000/api/backfill -d '{"limit":10}'   # pull channel history
curl -s -X POST localhost:8000/api/replay   -d '{"limit":100}'  # re-run the filter on it
```

## 8. Operate

- **Backup** the SQLite DB (or use Postgres): `cp $DB_PATH backup-$(date +%F).db`
  on a cron; the DB holds the session, the allowlist, and every captured code.
- **Logs**: `journalctl -u pinlives-backend -f`. File logs rotate
  (`LOG_FILE`, default `/tmp/pinlives.log` — move to a durable path).
- **Disk**: OCR downloads media to `/tmp/telethon_media`; prune periodically.
- **Rate limits**: on a Telegram `FloodWait` the client backs off; if the login
  reports being rate-limited, wait the stated seconds and retry.

## 9. Known limits (read before relying on it)

- **No downstream consumer.** The system *captures* codes into the DB and the
  review queue; deciding what happens with them next is a separate component that
  does not ship here.
- **`filters.py` defects** (vendored): qq88/generic can accept a 10-digit phone
  number as a code, and c168 rejects its own `#`-prefixed format. Fix or accept
  before production data matters.
- **Account-automation risk**: driving a user account can get it limited or
  banned by Telegram. Use a secondary account and avoid unusual activity.
