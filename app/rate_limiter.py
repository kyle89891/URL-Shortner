import time

from fastapi import HTTPException, Request

from app import cache
from app.config import settings


def rate_limit(request: Request) -> None:
    client_id = request.client.host if request.client else "unknown"
    window = int(time.time()) // settings.rate_limit_window_seconds
    key = f"rate_limit:{client_id}:{window}"

    count = cache.redis_client.incr(key)
    if count == 1:
        cache.redis_client.expire(key, settings.rate_limit_window_seconds)

    if count > settings.rate_limit_max_requests:
        raise HTTPException(status_code=429, detail="rate limit exceeded, try again later")
