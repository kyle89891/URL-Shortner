import hashlib

from sqlalchemy.orm import Session

from app.encoding import encode
from app.models import Url


def hash_long_url(long_url: str) -> str:
    return hashlib.sha256(long_url.encode("utf-8")).hexdigest()


def get_by_long_url(db: Session, long_url: str) -> Url | None:
    return db.query(Url).filter(Url.long_url_hash == hash_long_url(long_url)).first()


def get_by_short_code(db: Session, short_code: str) -> Url | None:
    return db.query(Url).filter(Url.short_code == short_code).first()


def create_url(db: Session, long_url: str) -> Url:
    existing = get_by_long_url(db, long_url)
    if existing is not None:
        return existing

    url_row = Url(long_url=long_url, long_url_hash=hash_long_url(long_url))
    db.add(url_row)
    db.commit()
    db.refresh(url_row)

    url_row.short_code = encode(url_row.id)
    db.commit()
    db.refresh(url_row)

    return url_row


def increment_click_count(db: Session, short_code: str) -> None:
    db.query(Url).filter(Url.short_code == short_code).update(
        {Url.click_count: Url.click_count + 1}
    )
    db.commit()


def flush_buffered_click_counts(db: Session) -> int:
    from app import cache

    counts = cache.pop_buffered_clicks()
    for short_code, amount in counts.items():
        db.query(Url).filter(Url.short_code == short_code).update(
            {Url.click_count: Url.click_count + amount}
        )
    db.commit()
    return len(counts)
