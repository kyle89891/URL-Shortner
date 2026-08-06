from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app import cache, crud
from app.config import settings
from app.db import get_db

router = APIRouter()


@router.get("/{short_code}")
def redirect_to_long_url(short_code: str, db: Session = Depends(get_db)):
    long_url = cache.get_url(short_code)

    if long_url is None:
        url_row = crud.get_by_short_code(db, short_code)
        if url_row is None:
            raise HTTPException(status_code=404, detail="short URL not found")

        long_url = url_row.long_url
        cache.set_url(short_code, long_url, settings.cache_ttl_seconds)

    cache.buffer_click(short_code)

    return RedirectResponse(url=long_url, status_code=302)
