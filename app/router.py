import asyncio
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import DocumentIn, DocumentOut
from app.classifier import classify_document
from app.dependencies import verify_api_key
from app import db_models

# verify_api_key applied to every route in this router.
router = APIRouter(dependencies=[Depends(verify_api_key)])


@router.post("/documents/", response_model=DocumentOut)
async def tag_document(doc: DocumentIn, db: Session = Depends(get_db)):
    # classify_document is sync (uses blocking OpenAI HTTP call).
    # asyncio.to_thread offloads it to a thread pool so it does not block the
    # event loop under concurrent requests.
    category = await asyncio.to_thread(classify_document, doc.content)
    db_doc = db_models.Document(
        filename=doc.filename,
        content=doc.content,
        category=category,
    )
    db.add(db_doc)
    db.commit()
    db.refresh(db_doc)
    return db_doc


# GET /documents/ must be registered before GET /documents/{doc_id}.
# Starlette matches routes in registration order: placing the parameterized
# route first would cause it to capture the trailing-slash path.
@router.get("/documents/", response_model=list[DocumentOut])
def list_documents(
    skip: int = 0,
    limit: int = Query(default=20, le=100),
    db: Session = Depends(get_db),
):
    return db.query(db_models.Document).offset(skip).limit(limit).all()


@router.get("/documents/{doc_id}", response_model=DocumentOut)
def get_document(doc_id: int, db: Session = Depends(get_db)):
    doc = db.query(db_models.Document).filter(db_models.Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc
