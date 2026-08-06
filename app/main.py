import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import crud
from app.config import settings
from app.db import SessionLocal
from app.routes import redirect, shorten


async def _flush_clicks_periodically():
    while True:
        await asyncio.sleep(settings.click_flush_interval_seconds)
        db = SessionLocal()
        try:
            crud.flush_buffered_click_counts(db)
        finally:
            db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_flush_clicks_periodically())
    yield
    task.cancel()


app = FastAPI(title="URL Shortener", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def serve_frontend():
    return FileResponse("static/index.html")


app.include_router(shorten.router)
app.include_router(redirect.router)
