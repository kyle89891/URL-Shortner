from app import cache, crud
from app.models import Url


def test_flush_moves_buffered_counts_into_mysql(client, db_session):
    created = client.post("/api/shorten", json={"long_url": "https://example.com/flush"})
    short_code = created.json()["short_code"]

    for _ in range(4):
        client.get(f"/{short_code}", follow_redirects=False)

    flushed = crud.flush_buffered_click_counts(db_session)
    assert flushed == 1

    row = db_session.query(Url).filter(Url.short_code == short_code).first()
    assert row.click_count == 4

    assert cache.redis_client.hgetall("pending_click_counts") == {}
