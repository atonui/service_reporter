from typing import Literal

from pydantic import Field

from backend.app.schemas.service_event import StrictModel


class ExtractedPage(StrictModel):
    page_number: int = Field(ge=1)
    text: str
    character_count: int = Field(ge=0)
    extraction_method: Literal["embedded_text", "ocr"] = "embedded_text"
    needs_ocr: bool


class ParsedDocument(StrictModel):
    file_name: str
    media_type: Literal["application/pdf"] = "application/pdf"
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    page_count: int = Field(ge=1)
    encrypted: bool
    pages_requiring_ocr: list[int] = Field(default_factory=list)
    pages: list[ExtractedPage]
