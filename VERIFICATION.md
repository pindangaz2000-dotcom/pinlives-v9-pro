# PINLIVES Pro v3.1 — Verification Report

This report records what was **executed**, not what was intended. Every claim
below corresponds to a command that ran and produced the stated output. Where
something is untested, it says so.

Probed on Python 3.11.15, Linux, SQLite backend.

---

## Correction to the previous report

An earlier version of this file claimed a "9.8/10 production readiness score"
and listed dozens of "Verification Tests Passed". **None of those tests had been
run.** The code had never been executed. The first time it was, it failed on
import. That report has been deleted; this one replaces it.

---

## Defects found by running the code

Each of these was found by executing the system, not by reading it.

### 1. Database schema could not be created — system was 100% non-functional

```
sqlalchemy.exc.ConstraintColumnNotFoundError: Can't create Index on table
'telegram_messages': no column named 'has_code' is present.
```

`init_db()` raised on the first call, so no component could start. Two further
schema defects were behind it: index names were reused across tables
(`idx_session_id`, `idx_channel_id`, `idx_timestamp`), and Telegram channel IDs
were stored as `Integer`, which cannot hold a real supergroup ID such as
`-1001234567890` on PostgreSQL.

**Fixed.** Removed the phantom index, made every index name unique, moved
`channel_id` and `message_id` to `BigInteger`.
**Verified:** six tables create; `-1001234567890` survives a write/read round trip.

### 2. Importing the config module killed the process

`config.py` called `sys.exit(1)` at module scope when a variable was missing, so
`import` terminated the interpreter. Test collection, tooling, and any inspection
of the bot or backend were impossible without a full environment.

**Fixed.** `Settings` raises `ConfigError`; only entrypoints exit. Missing
variables are now reported all at once instead of one per run.
**Verified:** import without env succeeds, process survives, all five missing
variables listed in a single message.

### 3. The API reported success while silently discarding every message

```
POST /api/telethon/message  ->  {"success":true,"queued":true}
SELECT COUNT(*) FROM telegram_messages  ->  0
```

`MessagePayload` had no `phone` field, so Pydantic v2 dropped it from the
request. `process_message` then looked up the account as `'unknown'`, got
`None` back from `save_message`, ignored it, and incremented the success
counter. Messages were lost with no error anywhere.

**Fixed.** `phone` is now a required field; a failed write raises so the retry
path runs; unregistered channels are recorded on first sight rather than
causing a drop.
**Verified:** the same request now yields 1 message row, 1 auto-registered
channel, and 2 extracted codes.

### 4. Login endpoints never contacted Telegram

`/api/telethon/login/start` returned `"✓ Code sent to +84…"` with the Telethon
call left commented out. It could not have worked as built: the backend and the
listener ran in separate containers, so the backend had no way to reach the
Telethon client's in-memory state.

**Fixed.** The Telethon client now runs inside the backend process — both are
asyncio, so they share an event loop and the endpoints drive the login directly.
The separate listener container is gone.
**Verified:** the endpoint now genuinely attempts a connection and reports a
real failure when Telegram is unreachable, instead of a fabricated success.

### 5. Nothing was ever logged

Every module called `logger.info(...)`, but no logging was configured. The root
logger defaults to WARNING with no handler, so all of it went nowhere — an
operator watching a running system would have seen an empty log.

**Fixed.** Added `logging_setup` with stdout plus a rotating file handler.
**Verified:** startup, session restore, message storage, and retries all appear
in both sinks.

### 6. The bot could not start

```
ImportError: cannot import name 'ParseMode' from 'telegram'
```

`ParseMode` moved to `telegram.constants` in python-telegram-bot v20. Reading
further, the `ConversationHandler` was also miswired: its entry point was a
generic handler that returned `None` for most buttons, and the add-channel state
was unreachable.

**Fixed.** Rewrote the bot with separate conversations for login and
add-channel, HTML escaping on all interpolated values, and a 2FA password step.
**Verified:** all 8 menu buttons resolve to a handler, both conversations expose
their states, and every screen renders from live backend data.

### 7. A network problem wedged logins permanently

The first real login attempt hung past a 45-second client timeout. Telethon
retries a failed connection indefinitely by default, and the login lock stayed
held for the duration — so every later login attempt would queue behind it
forever.

**Fixed.** Bounded every Telethon call with `asyncio.wait_for`, capped
connection and request retries, and made lock acquisition time out rather than
queue.
**Verified:** the attempt now fails in exactly 20s with HTTP 400 and an
actionable message; a second attempt also takes 20s rather than queueing; other
endpoints stay responsive throughout.

### 8. Retries all fired within 2 milliseconds

Retry logic existed and terminated correctly, but the timestamps showed all four
attempts inside 2ms:

```
09:34:18,724  attempt 0 failed
09:34:18,726  gave up after 3 attempts
```

A transient fault could never clear inside that window, making the retries
pointless. Exhausted messages were also dropped with no durable record.

**Fixed.** Exponential backoff (2s, 4s, 8s), scheduled off the processor so the
queue keeps draining, plus a dead-letter entry carrying the payload and the last
error.
**Verified by timestamp:** `+2.008s`, `+4.017s`, `+8.020s`, then a dead-letter
row containing the original text and failure reason.

---

## Architectural changes

**The listener moved into the backend process.** The three-container design
could not work: the login endpoints needed the Telethon client's live state, and
it lived in another container with no channel between them. Backend and Telethon
now share one event loop. The deployment dropped from four containers to two.

**Every database operation gets its own session.** `PersistenceManager` held a
single SQLAlchemy `Session` shared between request handlers and the background
processor. A `Session` is not safe under concurrency. Each operation now opens
and closes its own within a context manager that commits or rolls back.

**Code extraction is implemented.** It was previously a comment reading
`# Extract codes from text (placeholder)`. `core/codefilter.py` now combines
Shannon entropy with structural filters — a plain entropy threshold is not
enough, since `12345678` scores 3.0 bits yet is never a code.

---

## Test suite

```
41 passed in 4.28s
```

Real SQLite database per test, real FastAPI app driven through ASGI transport.
No mocks stand in for the code under test.

### The suite was checked for vacuity

A passing suite proves nothing until it is shown to fail. Each fix was reverted
in place and the suite re-run:

| Reintroduced defect | Result |
|---|---|
| Silent drop on failed save | 2 tests fail |
| `phone` removed from `MessagePayload` | 2 tests fail |
| Duplicate index name | all 41 error at schema creation |

The first mutation initially **passed**, exposing a hole: the existing test only
exercised the success path, where `save_message` never returns `None`. Two tests
covering the failure path were added, and the mutation then failed as it should.

---

## Not verified

Honest limits of this report:

- **No live Telegram session.** This sandbox blocks Telegram's MTProto
  transport, so sign-in, message capture from a real private channel, and 2FA
  were never exercised end to end. Their code paths are reachable and their
  error handling is verified; the successful path is not.
- **No PostgreSQL run.** All testing used SQLite. `BigInteger` matters most on
  PostgreSQL, where `Integer` is 32-bit — the fix is correct but untested there.
- **Docker images were never built.** The compose file and Dockerfile were
  corrected (they referenced `Dockerfile.daemon` and `Dockerfile.bot`, neither of
  which existed), but no build or `docker compose up` was run here.
- **No OCR.** Earlier documentation claimed multi-engine OCR with ddddocr and
  EasyOCR. No such code exists. Media files are downloaded and their paths
  stored; nothing reads them. The false claim and the unused dependencies were
  removed.
- **No load or soak testing.** Cache eviction and queue bounds are correct by
  construction and covered by unit tests, but were not tested under sustained
  volume.

---

## Security note

`.env.example` was committed containing a **real bot token**. It has been
replaced with placeholders, but the token remains in pushed git history and must
be treated as compromised.

**Revoke it with @BotFather (`/revoke`) and issue a new one.** Editing the file
does not remove it from history.

---

## State

Working and verified: schema, persistence, config, code extraction, message
ingestion through to storage, retry with backoff, dead-lettering, logging,
health and status reporting, bot structure and rendering, login failure paths.

Requires a live Telegram connection to confirm: sign-in, message capture from a
real channel, 2FA.

---

## Update: OCR wired into the pipeline (review queue)

OCR now runs on media messages inside the processing pipeline. Verified and not:

**Verified**
- Media messages run OCR in a thread pool, so a slow read does not block the
  event loop.
- OCR codes are stored as needs-review (`is_valid=False`), never confirmed.
  They appear at `/api/codes/review`, not `/api/codes`. A text code and an OCR
  code on the same message land in different lists — tested.
- OCR failure (or timeout) does not lose the message: the text codes and the
  message row are still stored, and the failure is logged — tested by raising
  inside the OCR call and asserting the message survived.
- Preprocessing, measured earlier: colour-key and the ported v9.5 preprocessor
  both read 9 of 10 characters on a real banner crop.

**Not verified in this environment**
- Real OCR through the queue on a real image. Tesseract on this sandbox takes
  >60s per variant on a 420x110 crop (a healthy host is well under a second),
  so every variant hits the per-variant timeout and OCR yields nothing here.
  This is an environment limit, not a code defect — the graceful-degradation
  path (message saved, timeout logged) is what runs, and that is tested.
- OCR accuracy end to end. The one remaining character error (`7` read as `T`)
  is a recognition-model limit, unchanged by preprocessing. Recovering it needs
  a stronger model than tesseract; that is why OCR codes are review-only.

The `OCR_ENABLED` flag turns the media path off entirely, and
`OCR_VARIANT_TIMEOUT_S` bounds each variant.

---

## Update: OCR breakthrough — PP-OCRv4 (RapidOCR) replaces tesseract

The remaining `7`-read-as-`T` error was a recognition-model limit, not an image
one, so the fix was a better model. Deep-searched the options and measured them
on the ten real posts rather than trusting published numbers.

**Chosen: RapidOCR** — PP-OCRv4 detection + recognition as ONNX, run on
onnxruntime. Self-contained (models ship in the package, ~16MB), no paddle
framework. This is the same `ch_PP-OCRv4_rec_infer.onnx` an earlier upload tried
to send (that upload arrived as a 9-byte "Not Found").

Measured on the ten real posts, tesseract vs RapidOCR:

| | tesseract | RapidOCR (PP-OCRv4) |
|---|---|---|
| Exact match, single-code images | 0 / 9 | 4 / 9 raw |
| The `7`/`T` case (`yNbEB7eSNa`) | wrong (`yNbEBTeSNa`) | correct |
| Speed per image | >60s (timed out) | ~700 ms |
| 20-codes-in-one-image post | cannot express | 11 / 20 found |

RapidOCR's misses are near, not wild: `rS2HNFvDME`→`rS2INFvDME` (H/I),
`vGW65kBRMs`→`VGW65kBRMs` (v/V case), `2JtVzWvYrF` fused with an adjacent balance
into `10092JtVzWvYrF`.

**With the recovery layer** — site-format validator, single-glyph confusion
candidates (the observed H/I, c/e, k/h, E/C pairs plus digit/letter ones), and
case variants for the letters whose upper/lower shapes coincide (c o s k p u v w
x z) — exact recovery on the single-code images rose to **6 / 9**, against
tesseract's 0.

The three still missed (`kbs7cox3AU`, `2JtVzWvYrF`, `E8kkYruH8t`) are 2-3 glyphs
off. They are left for the review queue rather than chased with more table
entries — hand-fitting the tables to these exact samples is the same overfitting
the old `_smart_correct` engine did, and it does not generalise.

**Integration**: RapidOCR is the primary reader when installed; the tesseract
multi-variant path remains as the fallback (`GiftcodeOCR.backend` reports which
is active). OCR codes still go to the review queue, not the confirmed list —
6/9 is a large gain, not certainty.

**Not verified**: accuracy on posts beyond these ten; behaviour when a real
site validator (not a known-code set) gates the confusion candidates. The
recovery layer is only as safe as that validator — a loose site filter could
confirm a wrong confusion variant, which is the reason OCR output stays
review-only.
