def test_shorten_returns_a_short_code(client):
    response = client.post("/api/shorten", json={"long_url": "https://example.com/a/b/c"})
    assert response.status_code == 200
    body = response.json()
    assert body["short_code"]
    assert body["long_url"] == "https://example.com/a/b/c"
    assert body["short_code"] in body["short_url"]


def test_shorten_same_url_twice_returns_same_code(client):
    first = client.post("/api/shorten", json={"long_url": "https://example.com/dup"})
    second = client.post("/api/shorten", json={"long_url": "https://example.com/dup"})
    assert first.json()["short_code"] == second.json()["short_code"]


def test_shorten_rejects_invalid_url(client):
    response = client.post("/api/shorten", json={"long_url": "not-a-url"})
    assert response.status_code == 422


def test_shorten_enforces_rate_limit(client):
    from app.config import settings

    for _ in range(settings.rate_limit_max_requests):
        response = client.post("/api/shorten", json={"long_url": "https://example.com/x"})
        assert response.status_code == 200

    response = client.post("/api/shorten", json={"long_url": "https://example.com/y"})
    assert response.status_code == 429
