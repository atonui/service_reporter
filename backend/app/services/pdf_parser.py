from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import os
import shutil
import subprocess

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from backend.app.schemas.document import ExtractedPage, ParsedDocument

MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 200
OCR_TEXT_THRESHOLD = 20
DEFAULT_MAX_OCR_PAGES = 20


class PdfValidationError(ValueError):
    """Raised when an upload cannot be safely treated as a supported PDF."""


class OcrError(ValueError):
    """Raised when an image-only PDF cannot be converted into usable text."""


def parse_pdf_bytes(file_name: str, data: bytes) -> ParsedDocument:
    if not data:
        raise PdfValidationError("The uploaded file is empty.")
    if len(data) > MAX_PDF_BYTES:
        raise PdfValidationError(f"PDF exceeds the {MAX_PDF_BYTES // (1024 * 1024)} MB limit.")
    if not data.startswith(b"%PDF-"):
        raise PdfValidationError("The uploaded file does not have a valid PDF signature.")

    try:
        reader = PdfReader(BytesIO(data), strict=True)
    except (PdfReadError, ValueError) as exc:
        raise PdfValidationError("The PDF is malformed or unsupported.") from exc

    if reader.is_encrypted:
        try:
            unlocked = reader.decrypt("")
        except Exception as exc:
            raise PdfValidationError("The PDF is password protected and cannot be read.") from exc
        if not unlocked:
            raise PdfValidationError("The PDF is password protected and cannot be read.")

    page_count = len(reader.pages)
    if page_count == 0:
        raise PdfValidationError("The PDF contains no pages.")
    if page_count > MAX_PDF_PAGES:
        raise PdfValidationError(f"PDF exceeds the {MAX_PDF_PAGES}-page limit.")

    pages: list[ExtractedPage] = []
    pages_requiring_ocr: list[int] = []
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            text = (page.extract_text() or "").strip()
        except Exception as exc:
            raise PdfValidationError(f"Text extraction failed on page {page_number}.") from exc
        needs_ocr = len(text) < OCR_TEXT_THRESHOLD
        if needs_ocr:
            pages_requiring_ocr.append(page_number)
        pages.append(
            ExtractedPage(
                page_number=page_number,
                text=text,
                character_count=len(text),
                needs_ocr=needs_ocr,
            )
        )

    return ParsedDocument(
        file_name=file_name,
        size_bytes=len(data),
        sha256=sha256(data).hexdigest(),
        page_count=page_count,
        encrypted=reader.is_encrypted,
        pages_requiring_ocr=pages_requiring_ocr,
        pages=pages,
    )


def parse_pdf_bytes_with_ocr(file_name: str, data: bytes) -> ParsedDocument:
    """Extract embedded text, then OCR only pages that do not contain usable text."""
    document = parse_pdf_bytes(file_name, data)
    if not document.pages_requiring_ocr:
        return document

    maximum = _max_ocr_pages()
    if len(document.pages_requiring_ocr) > maximum:
        raise OcrError(
            f"PDF requires OCR on {len(document.pages_requiring_ocr)} pages; "
            f"the configured limit is {maximum}."
        )

    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise OcrError(
            "OCR dependencies are not installed. Run: pip install -e \".[dev]\""
        ) from exc

    try:
        rendered = pdfium.PdfDocument(data)
    except Exception as exc:
        raise OcrError("The PDF pages could not be rendered for OCR.") from exc

    ocr_pages = set(document.pages_requiring_ocr)
    updated_pages: list[ExtractedPage] = []
    try:
        for page in document.pages:
            if page.page_number not in ocr_pages:
                updated_pages.append(page)
                continue
            pdf_page = None
            bitmap = None
            image = None
            try:
                pdf_page = rendered[page.page_number - 1]
                bitmap = pdf_page.render(scale=300 / 72)
                image = bitmap.to_pil()
                text = _ocr_image(image).strip()
            except OcrError:
                raise
            except Exception as exc:
                raise OcrError(f"OCR failed on page {page.page_number}.") from exc
            finally:
                if image is not None:
                    image.close()
                if bitmap is not None:
                    bitmap.close()
                if pdf_page is not None:
                    pdf_page.close()
            updated_pages.append(
                ExtractedPage(
                    page_number=page.page_number,
                    text=text,
                    character_count=len(text),
                    extraction_method="ocr",
                    needs_ocr=len(text) < OCR_TEXT_THRESHOLD,
                )
            )
    finally:
        rendered.close()

    remaining = [page.page_number for page in updated_pages if page.needs_ocr]
    return document.model_copy(
        update={"pages": updated_pages, "pages_requiring_ocr": remaining}, deep=True
    )


def _ocr_image(image) -> str:
    command = os.getenv("TESSERACT_CMD") or shutil.which("tesseract")
    if not command:
        raise OcrError(
            "Tesseract OCR is not installed or not on PATH. On Windows run: "
            "winget install --id UB-Mannheim.TesseractOCR -e"
        )
    language = os.getenv("SERVICE_INTELLIGENCE_OCR_LANGUAGE", "eng")
    image_bytes = BytesIO()
    image.save(image_bytes, format="PNG")
    try:
        result = subprocess.run(
            [command, "stdin", "stdout", "-l", language, "--psm", "6"],
            input=image_bytes.getvalue(),
            capture_output=True,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OcrError(f"Tesseract OCR could not be executed: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise OcrError(f"Tesseract OCR failed: {detail or 'unknown error'}")
    try:
        return result.stdout.decode("utf-8")
    except Exception as exc:
        raise OcrError("Tesseract returned text that could not be decoded.") from exc


def _max_ocr_pages() -> int:
    value = os.getenv("SERVICE_INTELLIGENCE_MAX_OCR_PAGES", str(DEFAULT_MAX_OCR_PAGES))
    try:
        parsed = int(value)
    except ValueError as exc:
        raise OcrError("SERVICE_INTELLIGENCE_MAX_OCR_PAGES must be an integer.") from exc
    if parsed < 1 or parsed > MAX_PDF_PAGES:
        raise OcrError(
            f"SERVICE_INTELLIGENCE_MAX_OCR_PAGES must be between 1 and {MAX_PDF_PAGES}."
        )
    return parsed
