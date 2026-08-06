# URL Shortener — Learning Project

A from-scratch, core-only URL shortener built with **Python, FastAPI, MySQL, and Redis** to
learn the fundamentals behind a production-style read-heavy system: base62 encoding, database
indexing, cache-aside caching, rate limiting, and scalability basics.

**Start here:** [`LEARNING_NOTES.md`](LEARNING_NOTES.md) explains *why* every decision in this
codebase was made, stage by stage. This README only covers how to run it.

## Stack

- **FastAPI** — web framework
- **MySQL** — durable storage for URL mappings
- **Redis** — cache-aside caching, rate limiting, click-count buffering
- **SQLAlchemy** — ORM / database access
- **pytest** — tests

## Project layout

```
app/
  main.py            FastAPI app + route registration
  config.py           settings loaded from .env
  db.py                SQLAlchemy engine/session
  models.py            the `urls` table
  schemas.py           request/response models
  encoding.py          base62 encode/decode
  crud.py              database access functions
  cache.py             Redis cache-aside + click-count buffer
  rate_limiter.py      fixed-window rate limiter
  flush_clicks.py       standalone script to flush buffered click counts into MySQL
  routes/
    shorten.py          POST /api/shorten
    redirect.py         GET /{short_code}
tests/                  pytest suite (uses fakeredis + a separate test MySQL schema)
```

## Setup

1. Create a virtual environment and install dependencies:
   ```
   python -m venv venv
   source venv/Scripts/activate   # on Windows Git Bash
   pip install -r requirements.txt
   ```

2. Create the MySQL databases (main + test):
   ```
   mysql -u root -p -e "CREATE DATABASE IF NOT EXISTS urlshortener;"
   mysql -u root -p -e "CREATE DATABASE IF NOT EXISTS urlshortener_test;"
   ```

3. Start Redis (a throwaway Docker container is the easiest local option):
   ```
   docker run -d --name urlshort-redis -p 6379:6379 redis
   ```

4. Copy `.env.example` to `.env` and fill in your real MySQL credentials.

5. Create the `urls` table:
   ```
   python -c "from app.db import Base, engine; import app.models; Base.metadata.create_all(bind=engine)"
   ```

## Running the app

```
uvicorn app.main:app --reload --port 8000
```

- Create a short URL:
  ```
  curl -X POST http://localhost:8000/api/shorten \
    -H "Content-Type: application/json" \
    -d '{"long_url": "https://example.com/some/long/path"}'
  ```
- Follow the redirect: open the returned `short_url` in a browser, or:
  ```
  curl -i http://localhost:8000/<short_code>
  ```

## Flushing buffered click counts

Click counts are buffered in Redis on every redirect (see `LEARNING_NOTES.md` Stage 6 for why)
and periodically flushed into MySQL:

```
python -m app.flush_clicks
```

In a real deployment this would run on a schedule (cron, a background scheduler) rather than
manually.

## Running tests

```
pytest -v
```

Tests use `fakeredis` (no real Redis needed for tests) and a separate `urlshortener_test`
MySQL schema that is created/dropped around each test.
