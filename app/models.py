import os
from datetime import datetime
from pydantic import BaseModel, Field, field_validator


class DocumentIn(BaseModel):
    content: str = Field(..., max_length=500_000)
    filename: str = Field(..., max_length=255)

    @field_validator("filename")
    @classmethod
    def filename_no_path_traversal(cls, v: str) -> str:
        if os.path.basename(v) != v:
            raise ValueError("Filename must not contain path separators")
        return v


class DocumentOut(BaseModel):
    id: int
    filename: str
    category: str
    content: str
    created_at: datetime

    class Config:
        from_attributes = True
