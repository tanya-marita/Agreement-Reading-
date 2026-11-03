"""Agreement extraction pipeline: file bytes -> loaded document -> Claude -> cleaned record -> database."""
from __future__ import annotations

import os
from dataclasses import dataclass

from . import storage
from .llm import ExtractionError, extract_document
from .loaders import UnsupportedFileError, load_file
from .postprocess import postprocess

__all__ = ["process_file", "ProcessResult", "ExtractionError", "UnsupportedFileError"]

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "data/uploads")


@dataclass
class ProcessResult:
    id: int
    data: dict
    duplicate: bool
    mode: str
    input_tokens: int = 0
    output_tokens: int = 0


def process_file(data: bytes, filename: str, *, client=None, model: str | None = None,
                 uploaded_by: str | None = None, reprocess: bool = False, keep_file: bool = True) -> ProcessResult:
    import json

    doc = load_file(data, filename)

    existing = storage.find_by_hash(doc.file_hash)
    if existing and not reprocess:
        return ProcessResult(existing["id"], json.loads(existing["data_json"]), True, doc.mode)

    llm = extract_document(doc, client=client, model=model)
    record = postprocess(llm.data)
    record["warnings"] = doc.warnings + record["warnings"]
    record["derived"]["input_mode"] = doc.mode
    record["derived"]["pages"] = doc.pages

    stored = None
    if keep_file:
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in filename)[-80:]
        stored = os.path.join(UPLOAD_DIR, f"{doc.file_hash[:12]}_{safe}")
        with open(stored, "wb") as f:
            f.write(data)

    rid = storage.save(record, file_hash=doc.file_hash, filename=filename, stored_file=stored, model=llm.model,
                       input_tokens=llm.input_tokens, output_tokens=llm.output_tokens, uploaded_by=uploaded_by)
    return ProcessResult(rid, record, False, doc.mode, llm.input_tokens, llm.output_tokens)
