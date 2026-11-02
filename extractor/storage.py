"""SQLite storage. One row per uploaded file; the full extraction is kept as JSON,
and the fields people search and sort on are copied into columns."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = os.getenv("AGREEMENTS_DB", "data/agreements.db")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS agreements (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    file_hash        TEXT UNIQUE NOT NULL,
    filename         TEXT NOT NULL,
    stored_file      TEXT,
    uploaded_at      TEXT NOT NULL,
    uploaded_by      TEXT,
    title            TEXT,
    agreement_type   TEXT,
    reference_number TEXT,
    parties          TEXT,
    signing_date     TEXT,
    start_date       TEXT,
    end_date         TEXT,
    total_value      REAL,
    currency         TEXT,
    purpose          TEXT,
    status           TEXT,
    needs_review     INTEGER,
    reviewed         INTEGER DEFAULT 0,
    model            TEXT,
    input_tokens     INTEGER,
    output_tokens    INTEGER,
    data_json        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_end ON agreements(end_date);
CREATE INDEX IF NOT EXISTS idx_status ON agreements(status);
"""


@contextmanager
def connect(path: str | None = None):
    path = path or DB_PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA_SQL)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _columns(data: dict) -> dict:
    der = data.get("derived", {})
    return {
        "title": data.get("agreement_title"),
        "agreement_type": data.get("agreement_type"),
        "reference_number": data.get("reference_number"),
        "parties": "; ".join(p["name"] for p in data.get("parties", [])),
        "signing_date": data.get("signing_date"),
        "start_date": der.get("effective_start"),
        "end_date": der.get("effective_end"),
        "total_value": data.get("total_contract_value"),
        "currency": der.get("primary_currency"),
        "purpose": data.get("purpose"),
        "status": der.get("status"),
        "needs_review": int(bool(der.get("needs_review"))),
        "data_json": json.dumps(data, ensure_ascii=False),
    }


def find_by_hash(file_hash: str, path: str | None = None):
    with connect(path) as con:
        return con.execute("SELECT * FROM agreements WHERE file_hash = ?", (file_hash,)).fetchone()


def save(data: dict, *, file_hash: str, filename: str, stored_file: str | None, model: str,
         input_tokens: int, output_tokens: int, uploaded_by: str | None = None, path: str | None = None) -> int:
    cols = _columns(data) | {
        "file_hash": file_hash, "filename": filename, "stored_file": stored_file,
        "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "uploaded_by": uploaded_by, "model": model,
        "input_tokens": input_tokens, "output_tokens": output_tokens,
    }
    keys = ", ".join(cols)
    marks = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{k}=excluded.{k}" for k in cols if k != "file_hash")
    with connect(path) as con:
        con.execute(f"INSERT INTO agreements ({keys}) VALUES ({marks}) "
                    f"ON CONFLICT(file_hash) DO UPDATE SET {updates}, reviewed=0", list(cols.values()))
        return con.execute("SELECT id FROM agreements WHERE file_hash = ?", (file_hash,)).fetchone()["id"]


def update_reviewed(agreement_id: int, data: dict, path: str | None = None) -> None:
    """Save a reviewer's corrections and mark the record as reviewed."""
    cols = _columns(data) | {"reviewed": 1, "needs_review": 0}
    sets = ", ".join(f"{k}=?" for k in cols)
    with connect(path) as con:
        con.execute(f"UPDATE agreements SET {sets} WHERE id=?", [*cols.values(), agreement_id])


def list_all(path: str | None = None) -> list[dict]:
    with connect(path) as con:
        rows = con.execute("SELECT * FROM agreements ORDER BY uploaded_at DESC").fetchall()
    return [dict(r) for r in rows]


def get(agreement_id: int, path: str | None = None) -> dict | None:
    with connect(path) as con:
        r = con.execute("SELECT * FROM agreements WHERE id=?", (agreement_id,)).fetchone()
    return dict(r) if r else None


def delete(agreement_id: int, path: str | None = None) -> None:
    with connect(path) as con:
        con.execute("DELETE FROM agreements WHERE id=?", (agreement_id,))
