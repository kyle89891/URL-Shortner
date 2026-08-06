from app import crud
from app.db import SessionLocal


def main() -> None:
    db = SessionLocal()
    try:
        flushed = crud.flush_buffered_click_counts(db)
        print(f"flushed click counts for {flushed} short code(s)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
