# Learning Notes: Building a URL Shortener From Scratch

This file is a running log of **what we built and why**, written for a beginner (roughly
2-2.5 years of experience) who wants to understand the core ideas behind a production-style
URL shortener: hashing/encoding, database design, caching, read-heavy system design, rate
limiting, and scalability basics.

Read this top to bottom, in order — each stage builds on the previous one. Code comments in
the project stay minimal on purpose; the reasoning lives here instead.

---

## Stage 0 — Environment Setup

### What a URL shortener actually needs to store

At its core, a URL shortener does one job: given a long URL, hand back a short code; given a
short code, hand back the original long URL. That means we need:

1. **A durable store** — something that will still have the mapping tomorrow, next year,
   after a server restart. That's **MySQL** in this project. Durable stores are usually
   slower per-request than an in-memory store, because they write to disk and may live on a
   different machine.
2. **A fast, ephemeral store** — something that answers "what does `abc123` map to?" in
   microseconds, at the cost of not being guaranteed to survive a crash. That's **Redis**
   in this project (an in-memory key-value store).

Why do we need both? Because a URL shortener is a **read-heavy system**: for every 1 short
URL created, it might get clicked/redirected thousands or millions of times. If every single
redirect had to hit MySQL, the database would become the bottleneck under load. So we put a
cache (Redis) in front of the database for the read path. This single idea — "durable
source of truth + fast cache in front of it for reads" — is one of the most common patterns
in backend systems, and it's the backbone of this whole project.

### Why FastAPI

FastAPI is a Python web framework that's async-native, has built-in request/response
validation via Pydantic, and is close enough to what's used in real production Python
services (Uber, Netflix, Microsoft all have services on it) that what you learn here
transfers directly.

### Dependencies (`requirements.txt`)

- `fastapi`, `uvicorn` — the web framework and the server that runs it
- `sqlalchemy` + `pymysql` — SQLAlchemy is Python's most common ORM (object-relational
  mapper); `pymysql` is the actual driver that speaks MySQL's wire protocol
- `redis` — the Python client for talking to Redis
- `pydantic-settings` — lets us load config (DB URL, Redis URL, etc.) from environment
  variables/`.env` file instead of hardcoding them (a production must-have — never hardcode
  credentials)
- `pytest`, `httpx`, `fakeredis` — testing tools. `fakeredis` lets tests simulate Redis
  behavior without needing a real Redis server running

### Config via `.env`

See `.env.example`. In production you never commit real secrets to git — you commit an
`.env.example` template showing what variables are needed, and each environment
(developer laptop, staging, production) supplies its own real `.env` (or, in real production,
a secrets manager) that is never checked into version control.

### Running MySQL and Redis locally

- MySQL is already installed locally. We create one schema (database) called
  `urlshortener` for this project.
- Redis is **not** installed locally, so instead of a full production install we run it as a
  single throwaway Docker container: `docker run -d -p 6379:6379 redis`. This is purely a
  convenience for local development — it is not "infrastructure work", just the easiest way
  to get a real Redis process running on a Windows machine without a native install.

---

## Stage 1 — Database Design

### The `urls` table (`app/models.py`)

```
id              BIGINT AUTO_INCREMENT PRIMARY KEY
short_code      VARCHAR(10) UNIQUE, indexed, nullable
long_url        VARCHAR(2048) NOT NULL
long_url_hash   VARCHAR(64) UNIQUE, indexed, NOT NULL   -- SHA-256 hex digest of long_url
click_count     BIGINT NOT NULL DEFAULT 0
created_at      DATETIME, defaults to now()
expires_at      DATETIME, nullable
```

### Why `id` is a `BIGINT AUTO_INCREMENT`, not a UUID

We picked an auto-increment integer on purpose, because our ID strategy (see Stage 2) is to
**base62-encode the numeric id** into the short code. A monotonically increasing integer is
exactly what we want to feed into that encoder: it's compact, and MySQL generates it for free
on every `INSERT` with no extra round trip. (A UUID would also work as a primary key, but it
doesn't compress into a short, readable code the way a small integer does — that's the whole
reason we're not using UUIDs here.)

### Why `short_code` is nullable and a separate `UNIQUE` index (not the primary key)

The primary key (`id`) is what the database uses internally and what we feed to the encoder.
`short_code` is the *external-facing* value users see in the URL — it's derived from `id`,
not a replacement for it. It starts out `NULL` because of a sequencing problem explained in
Stage 3 (the "two-write problem"): we don't know what `id` MySQL will assign until after the
row is inserted, so we can't compute `short_code` before that insert happens.

We put a `UNIQUE INDEX` on `short_code` because **this is the single hottest lookup in the
entire system** — every redirect (`GET /{code}`) needs to find a row by `short_code` as fast
as possible. Without an index, MySQL would have to scan every row in the table
(an `O(n)` full table scan) to find a match. With a `UNIQUE` B-tree index, that lookup is
`O(log n)` — the difference between a lookup taking milliseconds vs. seconds once the table
has millions of rows.

### Why we don't index `long_url` directly, and what `long_url_hash` is for

We want to detect "someone already shortened this exact URL before" so we can return the
existing short code instead of creating a duplicate row for the same destination. That check
requires looking up by `long_url`. But `long_url` is declared `VARCHAR(2048)` — URLs can be
long — and MySQL's `InnoDB` engine has a hard limit on how much of a column it can put into a
single index (roughly 3072 bytes total per index, less for multi-byte charsets), plus a long
`VARCHAR` index is simply slower to compare and takes more disk space than a short fixed-size
one.

The standard fix: store a **fixed-length hash of the long URL** (`long_url_hash`, a 64-character
SHA-256 hex digest) in its own column, and put the `UNIQUE INDEX` on *that* instead. Comparing
64-character fixed-length hashes is fast and predictable; comparing arbitrarily long strings is
not. This is a very common real-world pattern: **whenever you need to deduplicate or index
something that's long or highly variable-length, hash it into a fixed-size value and index the
hash.**

Note the distinction between this hash and the base62 *encoding* in Stage 2: this is a genuine
cryptographic-style hash used purely for deduplication/equality checks (not exposed to end
users), whereas base62 is a reversible encoding used to actually *produce* the public short
code from a numeric id. Same word ("hash") gets used loosely in casual conversation for both,
but they solve different problems.

### Verifying it worked

We ran `Base.metadata.create_all()` (SQLAlchemy's table-creation call) against the real MySQL
database and confirmed with `DESCRIBE urls;` / `SHOW INDEX FROM urls;` that both unique indexes
exist. In a real production project you would not call `create_all()` directly — you'd use a
migration tool (Alembic is the standard one for SQLAlchemy) so schema changes are versioned and
repeatable across environments. We're skipping that here to keep focus on the core concepts,
but it's worth knowing the name for later.

---

## Stage 2 — Base62 Encoding (the "hashing" core)

### This is encoding, not hashing — and that distinction matters

Colloquially people say a URL shortener "hashes" the URL, but what we're building in
`app/encoding.py` is **not a hash function at all** — it's a **reversible encoding**, closer
to how binary-to-hex or Base64 works than to MD5/SHA. The difference matters:

- A **hash function** (MD5, SHA-256) takes arbitrary input and produces a fixed-size output
  that is *not reversible* — you cannot go from the hash back to the original input. Different
  inputs can also produce the same hash (a "collision"), which is why systems that hash values
  for identifiers need collision-handling logic (retry, check-then-insert, etc).
- Our `encode(number)` takes the row's numeric `id` and represents that *exact same number* in
  base62 instead of base10. `decode(encode(x)) == x`, always, no collisions possible, because
  it's a pure change of number base — like writing "10" in binary as "1010"; no information is
  lost or randomized.

We chose this approach (over generating a random string and checking for collisions) because
it's simpler to reason about and it leans on a guarantee we already have for free: MySQL's
`AUTO_INCREMENT` never gives out the same `id` twice.

### How base62 encoding works

Base62 uses the alphabet `0-9`, `a-z`, `A-Z` (62 characters) instead of the 10 digits (`0-9`)
you're used to in base10. The algorithm is exactly the same "repeated division" method you'd
use to convert decimal to binary by hand, just with 62 symbols instead of 2:

```
encode(125):
  125 divmod 62 -> quotient 2, remainder 1  -> alphabet[1] = "1"
    2 divmod 62 -> quotient 0, remainder 2  -> alphabet[2] = "2"
  (quotient is 0, stop)
  digits collected in reverse order: "21"
```

Why base62 specifically, and not base16 (hex) or base10? More symbols per "digit" means a
shorter string for the same number — that's the entire point of a *short* URL. `2^53` (a very
large id) encodes to just 9 characters in base62, versus 16 in hex or ~18 in decimal. And
unlike Base64, base62 avoids `+`, `/`, `=` — characters that aren't safe or clean inside a URL
path without escaping.

### `decode` is the inverse: reading the number back out

`decode("21")` walks the string left to right, and for every character does
`number = number * 62 + value_of_char`. This is the same technique you'd use to parse a
decimal string like `"125"` into an integer by hand (`1*10 + 2`, `*10 + 5`), just base62
instead of base10. We use `ALPHABET.index(char)` to turn a character back into its numeric
value (0-61); an unrecognized character naturally raises `ValueError` here, which is exactly
the behavior we want if someone requests a short code containing characters outside our
alphabet.

### The security lesson: sequential IDs are guessable

Because `short_code` is a direct, reversible encoding of a sequential auto-increment id,
**anyone can enumerate your entire URL database** just by requesting `/1`, `/2`, `/3`, ...
and decoding/incrementing from there. This is a real, well-known weakness of this exact
approach, and it's worth naming explicitly rather than discovering the hard way in production:

- If short URLs might point to anything even mildly sensitive (private documents, unlisted
  content, internal links), sequential+reversible IDs are a genuine information disclosure
  risk — someone can walk the entire history of URLs ever shortened.
  - Real-world mitigations: don't feed the raw id straight into the encoder — first pass it
    through a **reversible bit-mixing step** (e.g. XOR with a secret constant, or a
    format-preserving permutation) before base62-encoding it. This keeps the "no collisions,
    no DB round trip" benefit of sequential ids while making the *external* codes look random
    and non-enumerable, without needing a collision-retry loop.
  - The alternative we didn't pick — random string + collision check — sidesteps this problem
    entirely because there's no relationship between one code and the next, at the cost of an
    extra "does this already exist?" DB check on every creation.

We're keeping the simple version (direct encode, no bit-mixing) in this project because the
goal here is to learn the concept clearly, but a production system handling sensitive
destinations should add that mixing step or switch strategies.

### Tests (`tests/test_encoding.py`)

We wrote round-trip tests (`decode(encode(x)) == x`) across a range of values — including
edge cases like `0` and very large numbers (`2**53`, close to the largest integer JavaScript
can represent exactly, a common real-world boundary to test against) — plus a test confirming
the encoded string really is shorter than the decimal representation for large numbers, which
is the whole reason we're doing this. All 13 tests pass.

---

## Stage 3 — Create-Short-URL Endpoint (`POST /api/shorten`)

### The two-write problem

Here's a chicken-and-egg issue that shows up the moment you try to implement "encode the id
into a short code": **we don't know what `id` a row will get until after we `INSERT` it**,
but `short_code` is supposed to live on that same row. You can't compute
`encode(row.id)` before `row.id` exists.

The fix in `app/crud.py::create_url` is to write the row **twice**:

1. `INSERT` the row with `long_url` and `long_url_hash` filled in, `short_code` left `NULL`.
   MySQL's `AUTO_INCREMENT` assigns the `id` at this point.
2. Now that we have `id` (via `db.refresh(url_row)`, which reloads the row's generated
   values), compute `short_code = encode(id)` and `UPDATE` just that column.

This is exactly why `short_code` was declared nullable back in Stage 1 — there's a brief
window where the row genuinely doesn't have one yet.

**Why this matters at scale:** two writes instead of one for every single URL creation
doubles the write load for this endpoint. For a URL shortener this is usually an acceptable
trade because *creates are rare compared to reads* (that's the "read-heavy" theme again) — but
it's worth knowing the alternative real systems use when creates are frequent: a separate
**id-generation step that happens before the insert** (e.g. reserving a block of ids ahead of
time, or a dedicated id-generation service like Twitter's Snowflake), so the id is known
up-front and the row can be written once, complete. We're intentionally keeping the simpler
two-write version here since the concept (base62 of a sequential id) is the same either way.

### De-duplication via `long_url_hash`

Before inserting anything, `create_url` checks `get_by_long_url()`, which looks up by
`long_url_hash` (see Stage 1 for why we hash instead of indexing `long_url` directly). If a
row already exists for this exact URL, we return the existing short code instead of creating
a duplicate. We verified this works: POSTing the same URL twice returned the same
`short_code` both times, and the `urls` table has exactly one row.

### Why the route is thin (`app/routes/shorten.py`)

The route function itself does almost nothing: parse the request, call `crud.create_url`,
shape the response. All the actual logic (de-dup check, two-write sequence, encoding) lives in
`app/crud.py`. This separation — **routes handle HTTP concerns, crud/service functions handle
business logic** — means the core logic can be unit-tested and reused without spinning up a
web server, and the route stays easy to scan.

### Pydantic validation for free

`ShortenRequest` declares `long_url: HttpUrl` (Pydantic's URL type). FastAPI validates the
incoming JSON against this automatically — if someone posts `{"long_url": "not a url"}`, they
get a `422 Unprocessable Entity` with a clear error message before our code ever runs. This is
a good habit generally: validate at the boundary (the API layer) so internal code can trust
its inputs are already well-formed.

### Verified end-to-end

Started the server with `uvicorn app.main:app`, then:
- `POST /api/shorten` with a long URL returned `short_code: "1"` (the first row's id, base62
  encoded — since `1` is below 62 it encodes to itself).
- Posting the *same* URL again returned the same `short_code`, confirming de-duplication.
- Checked directly in MySQL (`SELECT * FROM urls`) and confirmed exactly one row exists.

---

## Stage 4 — Redirect Endpoint + Cache-Aside (`GET /{short_code}`)

### This is the endpoint the entire project is designed around

Everything up to this point — the schema, the indexes, the encoding — exists to make this one
endpoint as fast as possible, because it's called far more often than the create endpoint (a
single short URL might get clicked thousands of times). This is what "read-heavy system"
means in practice: design every earlier decision around making the read path cheap.

### Cache-aside, step by step (`app/routes/redirect.py`)

```
GET /{short_code}
  1. Ask Redis: do we have this code cached?  (app/cache.py::get_url)
  2. If yes (cache HIT): use that value, skip MySQL entirely.
  3. If no (cache MISS): query MySQL for the row.
       - Not found in MySQL either -> 404.
       - Found -> store it in Redis with a TTL (app/cache.py::set_url), THEN use it.
  4. Increment click_count, then respond with a 302 redirect to the long URL.
```

The name "cache-aside" (also called "lazy loading") describes exactly this: the cache doesn't
know how to load data on its own — the application sits *beside* it, checks it first, and is
responsible for filling it in on a miss. This is the most common caching pattern in real
backend systems because it's simple and it only caches things that are actually requested
(as opposed to pre-loading everything into the cache up front).

### Why a TTL (`CACHE_TTL_SECONDS=3600`) instead of caching forever

Two reasons: (1) memory in Redis isn't infinite — an expiring cache naturally evicts entries
nobody has asked for recently, keeping memory bounded to "recently active" URLs; (2) if a URL
is ever edited or deleted at the database level (not implemented here, but a realistic future
feature), a cache with no expiry would keep serving the stale value forever. A TTL bounds how
long that staleness can last, which is why almost no real cache is used without one.

### What we verified

- Before the first request, `redis-cli GET short_url:1` returned nothing (empty cache).
- First `GET /1` returned `302` and correctly redirected — this was necessarily a cache
  **miss**, since nothing was cached yet, so it went to MySQL and then populated Redis.
  We confirmed the value now exists in Redis with `redis-cli GET`, and its TTL
  (`redis-cli TTL`) was ~3599 seconds — right at the configured 3600s ceiling.
- A second `GET /1` returned the same redirect — this time it should be served from the cache
  **hit** path without touching MySQL, since the key exists in Redis.
- `GET /doesnotexist` correctly returned `404`.
- `click_count` in MySQL was `2` after two redirects, confirming the click counter is being
  updated on the redirect path (see the callout below for why this specific detail is a
  planted problem we'll fix in Stage 6, not the final design).

### A debugging detour worth remembering: stale process holding the port

When first restarting the server after adding the redirect route, requests kept returning
`404` even though the code looked correct. The cause: an old `uvicorn` process from Stage 3
was still bound to port 8000 (a `pkill` had been issued but the OS hadn't freed the socket
yet), so new requests were silently being served by the *old* code that had never heard of
`/{short_code}`. The fix was to find the actual process holding the port
(`netstat -ano | grep :8000`) and stop it directly, then start a fresh server. Lesson: **if a
running server doesn't seem to reflect your latest code changes, verify you're actually
talking to the process you think you are** before assuming the code itself is wrong — port/
process confusion is one of the most common sources of "changes aren't taking effect" during
local development.

### A deliberate rough edge: `click_count` is still being updated synchronously on every request

Right now `redirect_to_long_url` calls `crud.increment_click_count`, which runs an `UPDATE`
against MySQL, **on every single redirect** — including cache hits. That defeats part of the
point of caching: we skip the expensive *read* on a cache hit, but we still pay for a database
*write* every time. This is intentional at this stage, so the "before" state is visible; Stage
6 revisits this specifically and fixes it by buffering the count in Redis instead.

---

## Stage 5 — Rate Limiting (Fixed Window Counter)

### Why rate limit at all

Without a limit, one client (malicious or just buggy — a retry loop with no backoff) can send
unlimited requests to `POST /api/shorten`, each of which does at least one MySQL write. Rate
limiting protects the backend from being overwhelmed by any single caller, and is a standard
piece of any public-facing API.

### The fixed window algorithm (`app/rate_limiter.py`)

```
window = current_unix_time // window_size_seconds   # e.g. // 60 -> a number that changes every 60s
key = "rate_limit:<client_ip>:<window>"

count = INCR key        # atomically increments and returns the new value
if count == 1:
    EXPIRE key <window_size_seconds>   # only set expiry once, on first request in this window
if count > max_requests:
    reject with 429
```

The "window" is computed by integer-dividing the current Unix timestamp by the window size —
this buckets time into fixed, non-overlapping 60-second slices (e.g. `00:00-00:59`,
`01:00-01:59`, ...). Every request in the same slice increments the same Redis key; the key
naturally expires once its slice ends. This is why it's called a **fixed window**: the window
boundaries are fixed points in time, not "60 seconds relative to this request."

### Why `INCR` and not "read count, check, then write"

`redis_client.incr(key)` is a single atomic operation — Redis guarantees no two concurrent
requests can both read the same starting count and each think they're "request number 5." If
we instead did `count = GET key`, then checked it, then `SET key count+1` as three separate
steps, two requests arriving at the same instant could both read the same value and both
proceed, silently letting more requests through than the limit allows. This is a small but
important lesson in **concurrency correctness**: whenever multiple requests might touch shared
state at the same time, using an atomic primitive (`INCR`) removes an entire class of race
condition rather than trying to work around it with more application logic.

### Why this lives in Redis, not an in-process Python dict

A naive rate limiter might just keep a `dict` in memory counting requests per client. That
works fine for a single running process, but breaks the moment you run more than one instance
of the app (which any real production deployment will do, for both capacity and redundancy —
see Stage 6). Each process would have its own separate counter, so a client could get
`max_requests` allowed through *per instance* rather than in total. Storing the counter in
Redis means every app instance shares the same view of "how many requests has this client made
recently," regardless of which instance handled which request.

### Fixed window's known weakness (worth knowing, not fixed here)

A fixed window has a boundary burst problem: if the limit is 10 requests/minute, a client
could send 10 requests at `00:00:59` (end of one window) and another 10 at `00:01:00` (start
of the next) — 20 requests in two seconds, technically obeying the rule but defeating its
intent. A **sliding window** (tracking exact timestamps, e.g. via a Redis sorted set) or
**token bucket** (tokens refill continuously rather than resetting all at once) algorithm
avoids this at the cost of more complexity. We chose fixed window deliberately as the simplest
version to learn the core idea first; swapping in a different algorithm later would only
require changing `app/rate_limiter.py` — the rest of the app doesn't need to know which
strategy is used.

### Applying it as a FastAPI dependency

`rate_limit` is wired in via
`dependencies=[Depends(rate_limit)]` on the route decorator rather than being called inside
the route function body. FastAPI runs dependencies before the route's own code, and a
dependency that raises `HTTPException` (as `rate_limit` does on `count > max_requests`) short-
circuits the request immediately — the route function's body never executes. This keeps the
route function itself focused purely on "shorten a URL" while the cross-cutting concern
(rate limiting) is declared separately and can be reused on other routes.

### What we verified

Configured with `RATE_LIMIT_MAX_REQUESTS=10`, `RATE_LIMIT_WINDOW_SECONDS=60`. Sent 13
consecutive `POST /api/shorten` requests from the same client:
- Requests 1-10 returned `200 OK`.
- Requests 11-13 returned `429 Too Many Requests`.
- Checked Redis directly: a single key `rate_limit:127.0.0.1:<window>` existed with value
  `13` (every attempt increments the counter, even rejected ones) and a TTL under 60 seconds,
  confirming the window will reset on its own.

---

## Stage 6 — Scalability Basics

This stage ties together every earlier decision and fixes the one rough edge we deliberately
left in from Stage 4.

### Fixing the click-count write-on-every-read problem

**Before:** `redirect_to_long_url` called `crud.increment_click_count`, an `UPDATE` against
MySQL, on every redirect — even cache hits. That meant the "hot path" of the whole system
(the endpoint called far more often than any other) always did at least one database write, no
matter how effective the cache was at avoiding a database *read*. Under heavy traffic
(thousands of redirects/sec), that's thousands of individual `UPDATE` statements per second
hitting the one database — exactly the bottleneck a cache is supposed to prevent.

**After:** the redirect route now calls `cache.buffer_click(short_code)` instead
(`app/cache.py`), which does `HINCRBY pending_click_counts <short_code> 1` — incrementing a
counter *inside Redis*, in a single hash keyed by short code. No MySQL write happens on the
request path at all anymore. A separate function, `crud.flush_buffered_click_counts` (invoked
via `python -m app.flush_clicks`), periodically:

1. Atomically reads and clears the whole `pending_click_counts` hash from Redis
   (`app/cache.py::pop_buffered_clicks`, using a Redis pipeline so the read+delete happen as
   one atomic unit — otherwise a click arriving between the read and the delete could be lost).
2. For each short code, applies the *accumulated* count to MySQL in one `UPDATE` — e.g. "add 5"
   once, instead of five separate "add 1" statements.

This is a **write-batching** pattern: instead of many small writes, accumulate in a fast store
and flush accumulated totals to the durable store on a schedule. In real production systems
this flush would run on a timer (a cron job, a Celery beat task, a background thread) every
few seconds to a minute — we implemented it as a standalone script you run manually
(`app/flush_clicks.py`) to keep the concept isolated and easy to observe.

**We verified this concretely:** sent 5 redirects for the same short code; confirmed via
`redis-cli HGETALL pending_click_counts` that Redis showed `5` pending while MySQL's
`click_count` for that row stayed at its prior value (`2`, unchanged); ran
`python -m app.flush_clicks`; confirmed MySQL's `click_count` jumped to `7` (2 + 5) in one
update, and the Redis hash was empty afterward.

**The tradeoff to be honest about:** click counts are no longer real-time — there's a window
(however long between flushes) where a click has happened but isn't reflected in MySQL yet, and
if Redis lost data before a flush (e.g. crashed with no persistence configured) those buffered
clicks would be lost. This is a real, common tradeoff in production systems: **exact-real-time
accuracy vs. system throughput.** For an analytics counter like this, approximate-but-fast is
almost always the right choice — nobody needs click counts to be transactionally exact to the
millisecond. You would not make this same tradeoff for something like a financial balance.

### Update: automatic flushing (fixing a real "bug report")

While testing manually, `click_count` in MySQL appeared stuck at an old value after clicking a
short URL repeatedly — it looked like a bug. It wasn't: the clicks *were* being recorded, just
sitting in Redis's `pending_click_counts` hash, because nothing was calling
`python -m app.flush_clicks` automatically. This is exactly the tradeoff described above made
visible — if you never flush, the durable store never catches up, and from the outside that's
indistinguishable from "counting is broken."

The fix, in `app/main.py`: a background `asyncio` task (`_flush_clicks_periodically`) started
via FastAPI's `lifespan` context manager, which calls `crud.flush_buffered_click_counts` every
`CLICK_FLUSH_INTERVAL_SECONDS` (default 10s) for as long as the app process is running, and is
cancelled cleanly on shutdown. `app/flush_clicks.py` (the manual script) still exists and still
works — it's useful for triggering an on-demand flush (e.g. right before checking a number in a
demo) — but you no longer have to remember to run it for counts to eventually show up.

We verified this by sending 3 redirects for a short code, confirming Redis showed `3` pending
immediately afterward, waiting past the 10-second interval, and confirming MySQL's
`click_count` updated on its own with no manual intervention, and the Redis buffer emptied.

In a production deployment, this exact pattern (a periodic in-process background task) is fine
for a single instance, but if you were running multiple app instances (Stage 6's horizontal
scaling discussion), you'd want only *one* of them doing the flush at a time to avoid duplicate
or conflicting flush attempts — typically solved with a distributed lock (Redis itself can act
as one, via `SET key value NX EX <ttl>`) or by moving the flush into its own separate scheduled
job outside the web app entirely (a cron job, a Celery beat task) rather than inside every
instance. We're keeping it simple (in-process) here since we're only ever running one instance
locally.

### Read replicas (discussion — not implemented)

`app/db.py` currently has one `engine`/`SessionLocal` used for both reads and writes. In a real
production deployment handling serious read traffic, MySQL supports **read replicas** —
additional MySQL servers that continuously copy data from the primary and can serve read
queries, while all writes still go to the primary only. The application would then maintain
two connections/engines (e.g. `write_engine` pointed at the primary, `read_engine` pointed at a
load-balanced pool of replicas) and route accordingly: `crud.create_url` uses the write engine,
`crud.get_by_short_code` uses the read engine. This works well here specifically *because* our
access pattern is so read-heavy and reads (redirect lookups) don't need to see a write
(a new short URL) with zero delay — a few milliseconds of replication lag is a fine trade for
spreading read load across multiple machines. We didn't wire this up (it needs multiple actual
MySQL servers), but the split in `app/db.py` is exactly where that change would go.

### Horizontal scaling (discussion)

Notice that `app/main.py` holds no per-request state in memory — every piece of state that
needs to persist between requests (the URL mappings, the rate limit counters, the cache, the
buffered click counts) lives in MySQL or Redis, not in a Python variable inside the FastAPI
process. This property — an app process holding no state of its own — is what's usually meant
by "stateless," and it's *why* horizontal scaling works at all: you can run 2, 10, or 100
identical copies of this FastAPI app behind a load balancer, and it doesn't matter which
instance handles which request, because they all read/write the same shared MySQL and Redis.
If we had instead kept rate-limit counts or cached URLs in an in-process Python dict, running
multiple instances would silently break correctness (as discussed in Stage 5) — each instance
would have its own inconsistent view of the world.

### How the pieces fit together as one story

- MySQL: durable source of truth, indexed for fast lookup by `short_code`.
- Base62 encoding: turns a cheap, guaranteed-unique auto-increment id into a short public code.
- Redis cache-aside: absorbs the overwhelming majority of read traffic so MySQL only sees a
  read on a genuine cache miss.
- Redis rate limiting: protects the write path from being overwhelmed, using an atomic
  operation so it stays correct across multiple app instances.
- Redis click-count buffering: converts many small writes into fewer, larger batched writes.
- Statelessness: every piece above lives in a shared store, not in-process, which is what
  makes running many copies of the app (horizontal scaling) safe and effective.

---

## Stage 7 — Tests

### Why tests use `fakeredis` instead of the real Redis container

`tests/conftest.py` replaces `app.cache.redis_client` with a `fakeredis.FakeStrictRedis`
instance for every test (an `autouse` fixture, so every test gets it automatically without
needing to ask for it explicitly). This makes the test suite fast and deterministic — it
doesn't depend on a Docker container being up, and each test starts from a guaranteed-empty
Redis state (the fixture calls `flushall()` after every test).

One subtlety this surfaced: `app/rate_limiter.py` originally did
`from app.cache import redis_client`, which copies the *object reference at import time* into
`rate_limiter`'s own namespace. Monkeypatching `app.cache.redis_client` later has no effect on
that already-bound copy — the test would still hit the real Redis client. The fix was to
change it to `from app import cache` and reference `cache.redis_client` at call time instead of
import time, so it always reads whatever object currently lives on the `cache` module — real or
faked. This is a common gotcha with Python's `from module import name` syntax: it binds a name
to whatever the target was *at that moment*, not a live link back to the module.

### Why tests use a real (separate) MySQL schema instead of SQLite

We used a second schema, `urlshortener_test`, with tables created before each test and dropped
after (`tests/conftest.py::db_session`), rather than swapping in SQLite for speed. This avoids a
common trap: SQLite and MySQL don't behave identically (e.g. type handling, some constraint
behaviors), so tests passing against SQLite don't guarantee the code works against the MySQL
you'll actually run in production. Testing against the real database engine you deploy against
is worth the small speed cost.

### What the suite covers

- `test_encoding.py` — base62 round-trip correctness (Stage 2)
- `test_shorten.py` — creating a short URL, de-duplication, input validation, rate limit
  enforcement (Stages 3 and 5)
- `test_redirect.py` — 404 on unknown codes, cache miss → hit behavior, confirming clicks are
  buffered in Redis rather than written to MySQL immediately (Stages 4 and 6)
- `test_rate_limiter.py` — requests under the limit pass, requests over the limit raise `429`,
  different clients (IPs) are tracked independently (Stage 5)
- `test_flush_clicks.py` — buffered Redis click counts are correctly applied to MySQL and the
  buffer is cleared afterward (Stage 6)

All 24 tests pass (`pytest -v`).

---

## Stage 8 — A Plain HTML/CSS/JS Frontend

### Why this exists

Everything up to this point was tested via `curl` and MySQL/Redis CLI inspection. A minimal
frontend (`static/index.html`, `static/style.css`, `static/app.js` — no framework, no build
step, just plain files) lets you exercise the same `POST /api/shorten` API from an actual
browser: paste a URL, get a clickable short link back, click it, watch the redirect and the
click-count buffering happen for real.

### The routing collision this project already had, and how the frontend avoids it

`app/routes/redirect.py` defines `GET /{short_code}` — a **catch-all** route that matches any
single path segment at the root (`/anything`) and tries to treat it as a short code. If we
mounted the frontend carelessly, a request for `/` or `/static/app.js` could get swallowed by
that catch-all and return a 404 "short URL not found" instead of the page or the script.

FastAPI resolves routes **in registration order** — the first matching route wins. The fix in
`app/main.py` was to register the frontend's routes (`GET /` and the `/static` mount) *before*
`app.include_router(redirect.router)`. So the actual order is:

1. `GET /` → serves `static/index.html`
2. `/static/*` → serves CSS/JS files (via `StaticFiles`)
3. `shorten.router` → `POST /api/shorten`
4. `redirect.router` → `GET /{short_code}` (catch-all, checked last)

This is a general lesson, not just a quirk of this project: **whenever a route pattern can
match "anything" (a catch-all, a wildcard, a `{param}` at a shallow path), register your
specific routes before it**, or the catch-all will intercept requests meant for something else.

### What the frontend actually does (`static/app.js`)

Plain vanilla JS, no framework: listens for the form's `submit` event, calls
`fetch("/api/shorten", { method: "POST", ... })` with the URL as JSON, and either shows the
returned `short_url` (with a copy-to-clipboard button) or displays the API's error message —
including FastAPI/Pydantic's `422` validation errors and the rate limiter's `429` response,
both of which the backend already returns as JSON that the frontend just needs to read and
display. No new backend behavior was needed to support this — the frontend is a client for an
API that was already complete.

---

## Wrap-up: the whole system in one paragraph

A client asks to shorten a URL; we deduplicate by a hash of the long URL, insert a row to get a
sequential id from MySQL, and encode that id into a short, compact base62 code — all protected
by a Redis-backed rate limiter so the write path can't be overwhelmed. A client visiting a short
URL hits the read path far more often than anyone creates one, so we check Redis first
(cache-aside) and only fall back to MySQL on a miss, repopulating the cache for next time.
Click counts are buffered in Redis and flushed to MySQL in batches instead of writing on every
single request, because exact-real-time analytics isn't worth turning a pure read into a write.
Nothing about the app process itself holds state — everything lives in MySQL or Redis — which
is precisely what would let you run many copies of this app behind a load balancer and scale
horizontally. Every one of these decisions traces back to the same root fact: **this system is
read-heavy**, and every choice was made to keep that hot path as cheap as possible while keeping
the rarer write path correct.

---

## Top 10 Interview Questions on This Project

These are the kinds of questions an interviewer would realistically ask if you said "I built a
URL shortener" — one per major concept in this project, roughly in increasing difficulty. Try
answering them yourself from memory first, then check against the stage notes above.

### 1. Walk me through what happens end-to-end when a user submits a long URL to be shortened.

*Tests: whether you understand your own system, not just individual pieces.*
Expected shape of the answer: rate limit check → hash the long URL → check for an existing row
by that hash (de-dup) → if none, insert a row to get an auto-increment id → base62-encode the
id into `short_code` → update the row → return the short URL. Reference: Stage 3.

### 2. Is base62-encoding an id the same thing as hashing? Why or why not?

*Tests: precision about a term used loosely in casual conversation.*
No — it's a reversible change of number base (like decimal to binary), not a one-way hash.
`decode(encode(x)) == x` always holds and there are no collisions, unlike MD5/SHA-style hashing.
Reference: Stage 2.

### 3. What's the security concern with using a direct base62 encoding of a sequential id as your public short code?

*Tests: whether you think about attackers, not just happy paths.*
It's enumerable — anyone can decode a code back to a number, increment it, and re-encode to
walk your entire dataset (`/1`, `/2`, `/3`, ...). Mitigations: bit-mixing/permuting the id before
encoding, or switching to random-code-plus-collision-check. Reference: Stage 2.

### 4. Why did you hash the long URL into a separate column instead of putting a unique index directly on it?

*Tests: real database indexing knowledge, not textbook recall.*
`long_url` is a long, variable-length `VARCHAR` — expensive to index directly and bumps into
InnoDB's per-index byte limits. Hashing it into a fixed-length column (`long_url_hash`) gives a
cheap, fast-to-compare unique index instead. General principle: hash long/variable values you
need to deduplicate or index. Reference: Stage 1.

### 5. Explain the cache-aside pattern you used for redirects. What are its failure modes?

*Tests: whether you understand the caching pattern, not just that you "used Redis."*
Check cache first; on miss, read from DB and populate cache with a TTL; on hit, skip the DB
entirely. Failure modes worth naming: cache stampede (many simultaneous misses for the same
hot key all hit the DB at once), stale data if the underlying data changes and the TTL hasn't
expired yet, and the "thundering herd" problem when a popular key's TTL expires. Reference:
Stage 4.

### 6. Why did you pick a fixed-window rate limiter instead of a sliding window or token bucket? What's the tradeoff?

*Tests: whether you can compare algorithms, not just implement one.*
Fixed window is the simplest to reason about and implement (`INCR` + `EXPIRE`), but has a
boundary-burst problem — up to 2x the limit can slip through right at a window boundary.
Sliding window (timestamps in a sorted set) and token bucket (continuous refill) both fix this
at the cost of more implementation complexity. Reference: Stage 5.

### 7. Why does the rate limiter state live in Redis instead of an in-memory Python variable?

*Tests: whether you understand *why* shared state needs a shared store, tying to horizontal
scaling.*
An in-process counter only limits requests seen by *that one process*. The moment you run more
than one instance of the app (which any real deployment does for capacity/redundancy), each
instance would enforce the limit independently, letting a client get `limit × instance_count`
requests through in total. Redis gives every instance a shared, consistent view. Reference:
Stages 5 and 6.

### 8. Why did you change from writing click counts directly to MySQL on every redirect, to buffering them in Redis?

*Tests: whether you can reason about read-heavy vs write-heavy tradeoffs and defend a real
architectural decision you made in this exact project.*
Writing to MySQL on every redirect turns your cheapest, highest-volume read endpoint into a
guaranteed database write on every single request, regardless of cache hits — defeating much of
the point of caching. Buffering in Redis (`HINCRBY`) and flushing accumulated counts to MySQL
periodically converts many small writes into fewer, larger batched writes, at the cost of the
count being eventually-consistent rather than exact in real time. Reference: Stage 6, and the
"click count wasn't incrementing" investigation that led to adding the automatic background
flush.

### 9. This system uses `INCR` for rate limiting and `HINCRBY` for click buffering instead of read-then-write. Why does that matter?

*Tests: concurrency correctness, a favorite backend interview topic.*
`INCR`/`HINCRBY` are atomic single operations in Redis — two concurrent requests can't both
read the same starting value and each think they're incrementing from it, which is exactly what
would happen with a naive `GET` → add one → `SET` sequence (a classic race condition/
lost-update bug). Using an atomic primitive removes the entire class of bug instead of working
around it with extra locking logic. Reference: Stage 5.

### 10. How would you scale this system to handle 100x the current traffic? What would you change first, and why?

*Tests: whether you can extrapolate beyond what you actually built — a classic system-design
closing question.*
A strong answer touches, roughly in priority order: (a) confirm the app is stateless so you can
just run more instances behind a load balancer (already true here); (b) add MySQL read
replicas and route reads there since the workload is read-heavy and can tolerate small
replication lag (Stage 6); (c) make sure Redis itself isn't a single point of failure/bottleneck
(Redis Cluster or at least a replica); (d) revisit the flush-interval/batch-size tradeoff for
click counts as volume grows; (e) if create-traffic ever became high too, revisit the two-write
insert pattern (Stage 3) with a pre-reserved id range or dedicated id-generation service.
Reference: Stage 6.



### Why `click_count` lives on this row at all (foreshadowing Stage 6)

It would be simpler to never track this, but real URL shorteners need click analytics. We'll
see in Stage 6 that incrementing this column on *every single redirect* is dangerous in a
read-heavy system — it turns a pure-read hot path into a write on every request. For now, just
note the column exists; we'll revisit how to update it safely later.


