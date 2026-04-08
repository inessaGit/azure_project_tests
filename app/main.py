from fastapi import FastAPI
from app.router import router
from app.database import Base, engine

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Legal Document Tagger", version="1.0.0")
app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok"}
