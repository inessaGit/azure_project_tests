from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import DocumentIn, DocumentOut
from app.classifier import classify_document
from app import db_models

router = APIRouter()


@router.post("/documents/", response_model=DocumentOut)
def tag_document(doc: DocumentIn, db: Session = Depends(get_db)):
    category = classify_document(doc.content)
    db_doc = db_models.Document(
        filename=doc.filename,
        content=doc.content,
        category=category,
    )
    db.add(db_doc)
    db.commit()
    db.refresh(db_doc)
    return db_doc


@router.get("/documents/{doc_id}", response_model=DocumentOut)
def get_document(doc_id: int, db: Session = Depends(get_db)):
    doc = db.query(db_models.Document).filter(db_models.Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.get("/documents/", response_model=list[DocumentOut])
def list_documents(db: Session = Depends(get_db)):
    return db.query(db_models.Document).all()
