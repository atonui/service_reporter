from io import BytesIO

import pytest
from reportlab.pdfgen import canvas

import backend.app.services.pdf_parser as pdf_parser
from backend.app.services.pdf_parser import (
    PdfValidationError,
    parse_pdf_bytes,
    parse_pdf_bytes_with_ocr,
)


def make_pdf(*page_texts: str) -> bytes:
    buffer = BytesIO()
    document = canvas.Canvas(buffer)
    for text in page_texts:
        document.drawString(72, 720, text)
        document.showPage()
    document.save()
    return buffer.getvalue()


def test_extracts_text_and_preserves_page_boundaries() -> None:
    parsed = parse_pdf_bytes(
        "work-order.pdf",
        make_pdf("Work Order WO-004479870", "MLC Interlocks and parts installed"),
    )
    assert parsed.page_count == 2
    assert "WO-004479870" in parsed.pages[0].text
    assert "MLC Interlocks" in parsed.pages[1].text
    assert parsed.pages_requiring_ocr == []
    assert len(parsed.sha256) == 64


def test_short_or_empty_page_is_flagged_for_ocr() -> None:
    parsed = parse_pdf_bytes("scan.pdf", make_pdf(""))
    assert parsed.pages_requiring_ocr == [1]
    assert parsed.pages[0].needs_ocr is True


def test_ocr_recovers_text_from_an_image_only_page(monkeypatch) -> None:
    monkeypatch.setattr(
        pdf_parser,
        "_ocr_image",
        lambda _image: "Work Order WO-004537370 Machine dropping into PEL",
    )

    parsed = parse_pdf_bytes_with_ocr("scan.pdf", make_pdf(""))

    assert parsed.pages_requiring_ocr == []
    assert parsed.pages[0].extraction_method == "ocr"
    assert parsed.pages[0].needs_ocr is False
    assert "WO-004537370" in parsed.pages[0].text


def test_rejects_non_pdf_content() -> None:
    with pytest.raises(PdfValidationError, match="valid PDF signature"):
        parse_pdf_bytes("fake.pdf", b"not actually a PDF")
