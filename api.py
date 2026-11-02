"""Optional REST API, for plugging extraction into an existing company system.

Run:  uvicorn api:app --host 0.0.0.0 --port 8000
Then: curl -H "X-API-Key: <API_TOKEN>" -F "file=@agreement.pdf" http://localhost:8000/agreements
"""
from __future__ import annotations

import json
import os

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile

load_dotenv()

from extractor import ExtractionError, UnsupportedFileError, process_file, storage  # noqa: E402

MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "30"))
app = FastAPI(title="Agreement Extractor API", version="1.0")


def auth(x_api_key: str | None = Header(default=None)):
    token = os.getenv("API_TOKEN")
    if token and x_api_key != token:
        raise HTTPException(401, "Invalid or missing X-API-Key header")


@app.post("/agreements", dependencies=[Depends(auth)])
async def upload(file: UploadFile = File(...), reprocess: bool = False, uploaded_by: str | None = None):
    data = await file.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"File larger than {MAX_UPLOAD_MB} MB")
    try:
        res = process_file(data, file.filename or "upload", reprocess=reprocess, uploaded_by=uploaded_by)
    except UnsupportedFileError as e:
        raise HTTPException(415, str(e))
    except ExtractionError as e:
        raise HTTPException(502, str(e))
    return {"id": res.id, "duplicate": res.duplicate, "agreement": res.data}


@app.get("/agreements", dependencies=[Depends(auth)])
def list_agreements(status: str | None = None):
    rows = storage.list_all()
    if status:
        rows = [r for r in rows if r["status"] == status]
    return [{k: v for k, v in r.items() if k != "data_json"} for r in rows]


@app.get("/agreements/{agreement_id}", dependencies=[Depends(auth)])
def get_agreement(agreement_id: int):
    row = storage.get(agreement_id)
    if not row:
        raise HTTPException(404, "Not found")
    return {"id": row["id"], "filename": row["filename"], "agreement": json.loads(row["data_json"])}


@app.get("/health")
def health():
    return {"ok": True}
