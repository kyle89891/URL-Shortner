from sqlalchemy import BigInteger, Column, DateTime, String
from sqlalchemy.sql import func

from app.db import Base


class Url(Base):
    __tablename__ = "urls"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    short_code = Column(String(10), unique=True, index=True, nullable=True)
    long_url = Column(String(2048), nullable=False)
    long_url_hash = Column(String(64), unique=True, index=True, nullable=False)
    click_count = Column(BigInteger, nullable=False, default=0)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    expires_at = Column(DateTime, nullable=True)
