"""Turn an uploaded file into content blocks the Claude API can read.

- Text PDFs  -> extracted text (cheaper, fast)
- Scanned PDFs (little or no text layer) -> sent as a PDF document; Claude reads the page images
- Images (jpg, png, webp, gif, tiff, bmp) -> sent as image blocks (normalised and resized)
- Word (.docx) -> paragraphs + tables as text
- .txt -> text
"""
from __future__ import annotations

import base64
import hashlib
import io
from dataclasses import dataclass, field

MAX_PDF_BYTES = 30 * 1024 * 1024        # API limit is 32 MB per request; keep headroom
MAX_PDF_PAGES = 100                     # API page limit for PDF documents
MIN_CHARS_PER_PAGE = 200                # below this a PDF page is treated as scanned
MAX_TEXT_CHARS = 400_000                # very long contracts get truncated with a warning
MAX_IMAGE_SIDE = 1568                   # longest side; larger images get downscaled
IMAGE_TYPES = {"jpg", "jpeg", "png", "webp", "gif", "tif", "tiff", "bmp"}


class UnsupportedFileError(ValueError):
    pass


@dataclass
class LoadedDocument:
    filename: str
    file_hash: str
    mode: str                                # "text" | "pdf" | "image"
    blocks: list[dict]                       # Claude content blocks
    pages: int | None = None
    text_chars: int = 0
    warnings: list[str] = field(default_factory=list)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _b64(data: bytes) -> str:
    return base64.standard_b64encode(data).decode("ascii")


def _text_block(text: str, filename: str, warnings: list[str]) -> dict:
    if len(text) > MAX_TEXT_CHARS:
        warnings.append(f"Document text was truncated to {MAX_TEXT_CHARS:,} characters.")
        text = text[:MAX_TEXT_CHARS]
    return {"type": "text", "text": f"<document filename=\"{filename}\">\n{text}\n</document>"}


# ---------- PDF ----------

def load_pdf(data: bytes, filename: str) -> LoadedDocument:
    import pdfplumber

    warnings: list[str] = []
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = len(pdf.pages)
            texts = [(p.extract_text() or "") for p in pdf.pages]
    except Exception as e:  # encrypted or corrupt
        raise UnsupportedFileError(f"Could not open PDF: {e}") from e

    text = "\n\n".join(f"--- Page {i + 1} ---\n{t}" for i, t in enumerate(texts))
    chars = sum(len(t.strip()) for t in texts)
    scanned_pages = sum(1 for t in texts if len(t.strip()) < MIN_CHARS_PER_PAGE)

    # Mostly a text PDF: send text only.
    if pages and scanned_pages / pages <= 0.2:
        return LoadedDocument(filename, sha256(data), "text", [_text_block(text, filename, warnings)],
                              pages=pages, text_chars=chars, warnings=warnings)

    # Scanned or mixed: let Claude read the page images directly.
    if len(data) > MAX_PDF_BYTES or pages > MAX_PDF_PAGES:
        if chars:
            warnings.append("Scanned PDF is too large to send as images; only its partial text layer was used.")
            return LoadedDocument(filename, sha256(data), "text", [_text_block(text, filename, warnings)],
                                  pages=pages, text_chars=chars, warnings=warnings)
        raise UnsupportedFileError(
            f"Scanned PDF is too large ({len(data) / 1e6:.1f} MB, {pages} pages). "
            f"Limit is {MAX_PDF_BYTES // 1_000_000} MB and {MAX_PDF_PAGES} pages; split it and upload the parts.")

    block = {"type": "document",
             "source": {"type": "base64", "media_type": "application/pdf", "data": _b64(data)},
             "title": filename}
    return LoadedDocument(filename, sha256(data), "pdf", [block], pages=pages, text_chars=chars, warnings=warnings)


# ---------- Images ----------

def load_image(data: bytes, filename: str) -> LoadedDocument:
    from PIL import Image, ImageOps

    try:
        img = Image.open(io.BytesIO(data))
        frames = []
        for i in range(getattr(img, "n_frames", 1)):      # multi-page TIFF support
            img.seek(i)
            frames.append(ImageOps.exif_transpose(img.copy()).convert("RGB"))
    except Exception as e:
        raise UnsupportedFileError(f"Could not open image: {e}") from e

    warnings: list[str] = []
    if len(frames) > 20:
        warnings.append(f"Only the first 20 of {len(frames)} pages were processed.")
        frames = frames[:20]

    blocks = []
    for f in frames:
        f.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
        buf = io.BytesIO()
        f.save(buf, format="JPEG", quality=90)
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": _b64(buf.getvalue())}})
        if min(f.size) < 600:
            warnings.append("Image resolution is low; small text may be misread.")
    return LoadedDocument(filename, sha256(data), "image", blocks, pages=len(blocks), warnings=warnings)


# ---------- Word ----------

def load_docx(data: bytes, filename: str) -> LoadedDocument:
    import docx  # python-docx

    try:
        d = docx.Document(io.BytesIO(data))
    except Exception as e:
        raise UnsupportedFileError(f"Could not open Word file: {e}") from e

    # Walk the body in order so tables stay next to their surrounding text.
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    parts: list[str] = []
    for child in d.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            t = Paragraph(child, d).text.strip()
            if t:
                parts.append(t)
        elif tag == "tbl":
            for row in Table(child, d).rows:
                cells = [c.text.strip() for c in row.cells]
                if any(cells):
                    parts.append(" | ".join(cells))
    for section in d.sections:                       # headers often hold reference numbers
        for p in section.header.paragraphs:
            if p.text.strip():
                parts.insert(0, "[Header] " + p.text.strip())

    text = "\n".join(parts)
    warnings: list[str] = []
    if len(text.strip()) < 100:
        warnings.append("Word file has very little text; it may contain only images (save it as PDF and upload that instead).")
    return LoadedDocument(filename, sha256(data), "text", [_text_block(text, filename, warnings)],
                          text_chars=len(text), warnings=warnings)


def load_txt(data: bytes, filename: str) -> LoadedDocument:
    text = data.decode("utf-8", errors="replace")
    w: list[str] = []
    return LoadedDocument(filename, sha256(data), "text", [_text_block(text, filename, w)], text_chars=len(text), warnings=w)


def load_file(data: bytes, filename: str) -> LoadedDocument:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if data[:4] == b"%PDF" or ext == "pdf":
        return load_pdf(data, filename)
    if ext == "docx":
        return load_docx(data, filename)
    if ext == "doc":
        raise UnsupportedFileError("Old .doc format isn't supported. Save it as .docx or PDF and upload again.")
    if ext in IMAGE_TYPES:
        return load_image(data, filename)
    if ext == "txt":
        return load_txt(data, filename)
    raise UnsupportedFileError(f"Unsupported file type '.{ext}'. Use PDF, DOCX, TXT or an image.")
