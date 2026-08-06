import redis

from app.config import settings

redis_client = redis.from_url(settings.redis_url, decode_responses=True)

_KEY_PREFIX = "short_url:"


def _key(short_code: str) -> str:
    return f"{_KEY_PREFIX}{short_code}"


def get_url(short_code: str) -> str | None:
    return redis_client.get(_key(short_code))


def set_url(short_code: str, long_url: str, ttl_seconds: int) -> None:
    redis_client.set(_key(short_code), long_url, ex=ttl_seconds)


_CLICK_COUNT_KEY = "pending_click_counts"


def buffer_click(short_code: str) -> None:
    redis_client.hincrby(_CLICK_COUNT_KEY, short_code, 1)


def pop_buffered_clicks() -> dict[str, int]:
    pipe = redis_client.pipeline()
    pipe.hgetall(_CLICK_COUNT_KEY)
    pipe.delete(_CLICK_COUNT_KEY)
    counts, _ = pipe.execute()
    return {code: int(count) for code, count in counts.items()}
