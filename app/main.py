from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.router import router
from app.database import Base, engine, get_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    # DB schema creation runs at server startup, not at import time.
    # This prevents test runs from creating the production DB file as a side effect.
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Legal Document Tagger", version="1.0.0", lifespan=lifespan)
app.include_router(router)


@app.get("/health")
def health(db: Session = Depends(get_db)):
    """
    Liveness check. Verifies the database is reachable.
    Returns 200 only when the DB responds — not just when the process is running.
    """
    db.execute(text("SELECT 1"))
    return {"status": "ok"}
