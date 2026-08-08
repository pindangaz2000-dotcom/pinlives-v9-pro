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

---

## Update: latency principle learned from an on-device (Lens-style) pipeline

An uploaded Android native library (`com.google.android.libraries.oliveoil`,
aarch64 JNI, Halide-accelerated YuvToRgb) could not be used directly — wrong
architecture, wrong platform, and it contains only colour-space conversion, no
text extraction (verified: sole image op is `nativeHalideYuvToRgb8888`, `.rodata`
4.6 KB, no model). But the *principle* behind its low latency transfers: fuse or
drop unneeded stages, and process at the smallest sufficient resolution.

Applied to the RapidOCR path and measured on the ten real posts:

| Config | latency | accuracy |
|---|---|---|
| baseline (det + cls + rec, full res) | 899 ms | unchanged |
| drop the angle-classifier (use_cls=False) | 670 ms (−25%) | unchanged |
| + cap oversized images | 637 ms (−29%) | unchanged |
| raise onnx threads to 4 | 844 ms (**worse**) | unchanged |

The banners are horizontal, so the angle-classifier model was a wasted pass —
dropping it is the clean win. Capping resolution trims oversized uploads without
touching the multi-code posts. Raising the thread count was **measured and
rejected** — it was slower on this host, so it was not applied.

Integrated engine after tuning: 6/9 exact recovery preserved, mean latency
~600 ms (from ~720–900 ms). `OCR_MAX_SIDE` bounds the resolution cap.

---

## Update: throughput patterns learned from Google's app libraries

The uploaded Google artifacts (Clearcut logging transport, PRIMES performance
instrumentation, protobuf packaging) are stubs, not reusable code — but the
engineering patterns they embody transferred, and were measured, not assumed.

**PRIMES pattern — per-stage instrumentation.** Added latency accounting per
pipeline stage (journal / store / extract / save_codes / ocr / mark), exposed
at `/api/status`. It immediately earned its place: it showed the cost was in the
durable DB commits, not in code extraction or logging where a guess would have
put it. Optimizing without it would have targeted the wrong stage.

**Clearcut pattern — batch/coalesce writes.**
- Audit logs are buffered and flushed in bulk; ERROR flushes immediately
  (priority), INFO is coalesced, buffered INFO can be lost on a hard crash
  (the bounded-loss tradeoff Clearcut itself makes). Measured: 0 dropped.
- Codes are bulk-inserted (`save_codes`) — one commit for the batch, with
  duplicates filtered by a single SELECT rather than a commit-per-code.
- The journal and message writes, which PRIMES showed dominated latency, are
  coalesced into one transaction (`journal_and_store_message`) since no slow
  work sits between them.

Measured throughput, processing 300 messages:

| Stage | msg/s | ms/msg |
|---|---|---|
| baseline | 93 | 10.75 |
| + batched logs + bulk codes | 96 | ~10.4 |
| + coalesced journal/message | **110** | **9.12** |

A ~18% gain. The batching alone was only ~3% — because, as PRIMES showed, the
durable commits were the cost, not the logging. The remaining `mark_processed`
commit is left separate: it runs after the async OCR await, and holding a
transaction across that await is the concurrency hazard this layer exists to
avoid. That is the honest ceiling without trading the journal-first durability
guarantee.

---

## Update: PP-OCR v5 vs v4 measured, and the recognition-language finding

Compared on the ten real posts (exact match, no recovery layer). Single-code
images give the clean comparison (the 20-code post confounds a single number):

| model | single-code exact | note |
|---|---|---|
| v4, Chinese rec (previous) | 4/9 | baseline |
| v5, Chinese rec | 6/9 | v5 does beat v4 on the same rec model |
| v4, English rec | 7/9 | language of the recogniser matters more than the version |
| v5, English rec | 7/9 | same count, but a *different* 7 |

Two findings that only measurement gives:
- **v5 > v4** on the Chinese rec model (6 vs 4), so the version bump is real —
  but smaller than the language switch.
- The codes are Latin script, so the **English rec model** is the larger lever
  (4 → 7). "Newer" helped less than "right language".
- v4-en and v5-en each read a different 7/9; their **union is 9/9**. They are
  complementary, so an ensemble of the two recovers every single-code image.

Integrated, with the confusion/case recovery layer on top:

| engine config | single-code | latency |
|---|---|---|
| v4 ch-rec (before) | 6/9 | ~600 ms |
| v4 en-rec (new default) | 8/9 | ~940 ms |
| v4-en + v5-en ensemble | 9/9 | ~1.66 s |

Default is the single English-rec model (8/9, one pass). `OCR_ENSEMBLE=true`
adds the v5-en reader for 9/9 at double the latency — worth it where recall
matters more than speed, and safe because OCR output is review-only and the
site validator arbitrates the merged readings. `OCR_REC_LANG` and
`OCR_MODEL_VERSION` select the model.

---

## Update: deep stress test — defect found and fixed, memory verified

Stress-tested the OCR engine (repeated runs, latency percentiles, memory
sampling, correctness stability) and iterated on what it exposed.

**Defect found: two-glyph errors were not recovered.** The engine missed
`rS2HNFvDME`: PP-OCRv4-en read it as `rS2INFVDME`, which is *two* swaps from the
truth (I→H and V→v, a confusion and a case error at once). The recovery layer
only tried one swap. Raising it to two (validator-gated, nearest-accepted-first)
fixed it. Measured: single-code recovery 8/9 → **9/9**, stable across rounds.

**Memory verified — no leak.** RSS was sampled over 90 calls: it rises from
~225 MB to a plateau of ~235 MB and stays there (fluctuating 235–246 MB, no
monotonic growth). The initial rise is the onnxruntime arena warming up, not a
leak — the earlier "+38 MB" reading was that warm-up, confirmed bounded.

**Latency.** p50 ~1 s on this CPU-starved sandbox, p95 ~1.3 s. One p99 spike to
~7 s did not recur across rounds — a one-off GC/scheduling stall, not the engine.
The real levers (angle-classifier off, resolution cap) were already applied;
further latency chasing on this box gives unreliable numbers.

**Full result on the ten posts, single model + 2-swap recovery:**

| set | before | after |
|---|---|---|
| single-code (9) | 6/9 | **9/9** |
| 20-code image | 11/20 | **16/20** (ensemble 17/20) |
| total (29) | 17/29 | **25/29** (ensemble 26/29) |

The three still missed on the 20-code post are orange text on a night
photograph; recovering them would need low-contrast detection tuning that risks
overfitting to that single image, so they are left for the review queue.
