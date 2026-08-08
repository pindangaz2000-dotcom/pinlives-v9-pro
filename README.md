# PINLIVES Pro v3.1

Captures messages from private Telegram channels, extracts codes from them, and
stores everything durably. Operated through a Telegram bot.

See [VERIFICATION.md](VERIFICATION.md) for what has been executed and verified,
and what has not.

---

## How it works

Two processes:

**Backend** — FastAPI plus the Telethon client, in one process. Telethon signs
in as a real user account, which is what allows reading private channels a bot
cannot see. Both are asyncio, so they share an event loop and the HTTP endpoints
drive the login flow directly.

**Bot** — the operator console. Holds no state; every action is a backend API
call.

```
    Telegram (private channels)
              │  MTProto, as a user account
              ▼
    ┌─────────────────────────┐
    │  Backend process        │
    │  ┌───────────────────┐  │
    │  │ Telethon listener │  │
    │  └─────────┬─────────┘  │      ┌──────────┐
    │            ▼            │◄─────┤   Bot    │
    │      message queue      │ HTTP │ (control)│
    │            ▼            │      └──────────┘
    │   code extraction       │
    └────────────┬────────────┘
                 ▼
          SQLite / PostgreSQL
```

A message arrives, goes on an in-memory queue, is written to the database, and
its codes are extracted and stored. Failures retry with exponential backoff and
end up in a dead-letter log rather than disappearing.

---

## Requirements

- Python 3.11+
- A Telegram application: `api_id` and `api_hash` from https://my.telegram.org/apps
- A bot token from [@BotFather](https://t.me/BotFather)
- Your numeric user ID from [@userinfobot](https://t.me/userinfobot)
- **Outbound access to Telegram's MTProto servers.** This is not ordinary HTTPS;
  a restrictive network or an HTTP-only proxy will block sign-in.

---

## Setup

```bash
git clone https://github.com/pindangaz2000-dotcom/pinlives-v9-pro.git
cd pinlives-v9-pro

cp .env.example .env
$EDITOR .env          # fill in the five required values

python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Check the configuration before starting anything:

```bash
.venv/bin/python -m pinlives_pro.core.config
```

It prints the resolved settings, or names every missing variable at once.

---

## Running

### Local

```bash
# Backend
.venv/bin/python -m uvicorn pinlives_pro.api.backend:app --host 0.0.0.0 --port 8000

# Bot, in another terminal
.venv/bin/python -m pinlives_pro.bot.orchestrator
```

### Docker

```bash
docker compose up -d --build
docker compose logs -f
```

Two containers: `backend` and `bot`. Compose fails fast if a required variable
is missing from `.env`.

> Not yet verified: the images have not been built or run in this environment.

---

## First run

1. Send `/start` to your bot.
2. **🔐 Login** — send the phone number, then the code Telegram sends you.
   Spaces and dashes in the code are fine. 2FA accounts get a password prompt.
3. **📡 My channels** — lists what the account can see, with IDs.
4. **➕ Add channel** — paste an ID to start monitoring it.

The session is stored, so a restart reconnects on its own. Login is a one-time
step unless Telegram invalidates the session.

---

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Liveness. Reports `degraded` when the database is unreachable |
| `GET /api/status` | Uptime, stored counts read back from the database, queue depth |
| `GET /api/metrics` | CPU, memory, disk, queue, cache, error rate |
| `POST /api/telethon/login/start` | Request a login code |
| `POST /api/telethon/login/verify` | Submit the code |
| `POST /api/telethon/login/password` | Submit a 2FA password |
| `GET /api/telethon/state` | Position in the login flow |
| `GET /api/telethon/dialogs` | Channels the account can see |
| `POST /api/telethon/channel/add` | Register a channel, after confirming it is readable |
| `GET /api/channels` | Registered channels |
| `GET /api/codes` | Extracted codes |
| `GET /api/logs` | Audit log |
| `POST /api/telethon/message` | Ingest a message (external producers and tests) |

Interactive docs at `/docs`.

```bash
curl http://localhost:8000/api/health
curl http://localhost:8000/api/status
```

---

## Code extraction

`core/codefilter.py` scores candidates by Shannon entropy and then applies
structural filters. Entropy alone is insufficient — `12345678` scores 3.0 bits
yet is never a code.

Rejected: pure digit runs (phone numbers, prices), dates, URL and email
contents, repeated-character runs, common English words, and anything without
both a letter and a digit.

```
"Code hom nay: AB7X9Q2M"        -> ["AB7X9Q2M"]
"Hai ma: X7f2Kq9L va M4nP8vT2"  -> ["X7f2Kq9L", "M4nP8vT2"]
"Lien he 0388588488"            -> []
"Ngay 2026-08-07"               -> []
"https://t.me/kenhkin/12345"    -> []
```

Tune the thresholds via `MIN_ENTROPY`, `MIN_LENGTH`, and `MAX_REPEAT_RATIO` in
that module.

There is **no OCR**. Media files are downloaded and their paths recorded, but
nothing reads them yet.

---

## Configuration

Five required variables:

| Variable | Source |
|---|---|
| `TELETHON_API_ID` | https://my.telegram.org/apps |
| `TELETHON_API_HASH` | https://my.telegram.org/apps |
| `TELETHON_PHONE` | The account's phone, e.g. `+84388588488` |
| `TELEGRAM_BOT_TOKEN` | @BotFather |
| `TELEGRAM_ADMIN_ID` | @userinfobot |

Everything else has a working default — see `.env.example`. Useful ones:
`DB_TYPE` (`sqlite` or `postgres`), `LOG_LEVEL`, `OTP_TIMEOUT_SECONDS`,
`MESSAGE_QUEUE_SIZE`.

---

## Tests

```bash
.venv/bin/python -m pytest -q
```

41 tests against a real database and the real app through ASGI transport.

The suite has been mutation-checked: each fix was reverted in place to confirm
the relevant tests actually fail. Details in [VERIFICATION.md](VERIFICATION.md).

---

## Troubleshooting

**Login times out after 20 seconds.** The network cannot reach Telegram's
MTProto servers. An HTTP proxy does not carry MTProto. Verify with
`.venv/bin/python -c "import socket; socket.create_connection(('149.154.167.51', 443), 10)"`.

**Bot does not respond.** Check `TELEGRAM_ADMIN_ID` — the bot ignores everyone
else. `/api/logs` records the rejected user ID.

**Messages arrive but no codes appear.** The filter is deliberately strict. Test
a sample directly:

```bash
.venv/bin/python -c "from pinlives_pro.core.codefilter import extract_codes; print(extract_codes('your message'))"
```

**`degraded` from `/api/health`.** The database is unreachable. Check `DB_PATH`
permissions, or the PostgreSQL connection when `DB_TYPE=postgres`.

**Messages disappear.** They do not — check the dead-letter entries:

```bash
curl 'http://localhost:8000/api/logs?level=ERROR'
```

---

## Security

- Credentials come only from the environment. `.env` and `*.session` are gitignored.
- The bot serves `TELEGRAM_ADMIN_ID` alone; everyone else is rejected and logged.
- Session strings grant full account access. Treat the database as a secret.
- 2FA passwords are deleted from the chat after submission.
- Compose binds the API to `127.0.0.1`. Put it behind TLS before exposing it.
- Bot output is HTML-escaped, so channel content cannot inject markup.
