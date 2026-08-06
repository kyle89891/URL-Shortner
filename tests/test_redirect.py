from app import cache


def test_redirect_unknown_code_returns_404(client):
    response = client.get("/doesnotexist", follow_redirects=False)
    assert response.status_code == 404


def test_redirect_cache_miss_then_hit(client):
    created = client.post("/api/shorten", json={"long_url": "https://example.com/redir"})
    short_code = created.json()["short_code"]

    assert cache.get_url(short_code) is None

    first = client.get(f"/{short_code}", follow_redirects=False)
    assert first.status_code == 302
    assert first.headers["location"] == "https://example.com/redir"

    assert cache.get_url(short_code) == "https://example.com/redir"

    second = client.get(f"/{short_code}", follow_redirects=False)
    assert second.status_code == 302
    assert second.headers["location"] == "https://example.com/redir"


def test_redirect_buffers_click_count_in_redis_not_mysql(client, db_session):
    from app.models import Url

    created = client.post("/api/shorten", json={"long_url": "https://example.com/clicks"})
    short_code = created.json()["short_code"]

    for _ in range(3):
        client.get(f"/{short_code}", follow_redirects=False)

    row = db_session.query(Url).filter(Url.short_code == short_code).first()
    assert row.click_count == 0

    buffered = cache.redis_client.hget("pending_click_counts", short_code)
    assert int(buffered) == 3
