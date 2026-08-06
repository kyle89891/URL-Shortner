from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import crud
from app.config import settings
from app.db import get_db
from app.rate_limiter import rate_limit
from app.schemas import ShortenRequest, ShortenResponse

router = APIRouter()


@router.post("/api/shorten", response_model=ShortenResponse, dependencies=[Depends(rate_limit)])
def shorten_url(payload: ShortenRequest, db: Session = Depends(get_db)):
    url_row = crud.create_url(db, str(payload.long_url))
    return ShortenResponse(
        short_code=url_row.short_code,
        short_url=f"{settings.base_host}/{url_row.short_code}",
        long_url=url_row.long_url,
        created_at=url_row.created_at,
    )
