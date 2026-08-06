from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.config import settings
from app.rate_limiter import rate_limit


def _make_request(ip="1.2.3.4"):
    request = MagicMock()
    request.client.host = ip
    return request


def test_allows_requests_under_the_limit():
    request = _make_request()
    for _ in range(settings.rate_limit_max_requests):
        rate_limit(request)  # should not raise


def test_rejects_requests_over_the_limit():
    request = _make_request()
    for _ in range(settings.rate_limit_max_requests):
        rate_limit(request)

    with pytest.raises(HTTPException) as exc_info:
        rate_limit(request)
    assert exc_info.value.status_code == 429


def test_different_clients_have_independent_limits():
    request_a = _make_request("1.1.1.1")
    request_b = _make_request("2.2.2.2")

    for _ in range(settings.rate_limit_max_requests):
        rate_limit(request_a)

    with pytest.raises(HTTPException):
        rate_limit(request_a)

    rate_limit(request_b)  # different client, should not raise
