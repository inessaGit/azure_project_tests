from pydantic import BaseModel
from typing import Optional


class DocumentIn(BaseModel):
    content: str
    filename: str


class DocumentOut(BaseModel):
    id: int
    filename: str
    category: str
    content: str

    class Config:
        from_attributes = True
