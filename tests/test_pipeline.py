"""Offline tests: loaders, post-processing and the full pipeline with a fake Claude client.
Run: pytest -q"""
import os
import sys
from datetime import date
from types import SimpleNamespace

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
SAMPLES = os.path.join(ROOT, "samples")

from extractor.loaders import UnsupportedFileError, load_file  # noqa: E402
from extractor.llm import ExtractionError, get_client  # noqa: E402
from extractor.postprocess import postprocess  # noqa: E402


@pytest.mark.parametrize("api_key", ["", "sk-ant-...", "your_api_key_here", "<your-api-key>"])
def test_get_client_rejects_missing_or_placeholder_api_keys(api_key, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", api_key)
    with pytest.raises(ExtractionError, match="Set a real ANTHROPIC_API_KEY"):
        get_client()


def read(name):
    with open(os.path.join(SAMPLES, name), "rb") as f:
        return f.read()


@pytest.mark.parametrize("name,mode", [
    ("service_agreement_text.pdf", "text"),
    ("service_agreement_scanned.pdf", "pdf"),
    ("service_agreement_photo.jpg", "image"),
    ("service_agreement.docx", "text"),
])
def test_loader_modes(name, mode):
    doc = load_file(read(name), name)
    assert doc.mode == mode
    assert doc.blocks


def test_text_pdf_has_content():
    doc = load_file(read("service_agreement_text.pdf"), "a.pdf")
    assert "Lanka Data Solutions" in doc.blocks[0]["text"]


def test_docx_includes_tables():
    doc = load_file(read("service_agreement.docx"), "a.docx")
    assert "Kamala Silva | Nimal Perera" in doc.blocks[0]["text"]


def test_unsupported():
    with pytest.raises(UnsupportedFileError):
        load_file(b"abc", "file.xyz")


SAMPLE_OUTPUT = {
    "is_agreement": True,
    "agreement_title": "Service Agreement",
    "agreement_type": "service",
    "reference_number": "SA-2026-014",
    "signing_date": "2026-03-15",
    "start_date": "2026-04-01",
    "end_date": None,
    "duration_text": "twelve (12) months",
    "duration_months": 12,
    "purpose": "Lanka Data Solutions engages Nimal Perera to build and maintain its customer data dashboard.",
    "parties": [
        {"name": "Lanka Data Solutions (Pvt) Ltd", "role": "Company", "party_type": "company",
         "representative_name": "Kamala Silva", "representative_designation": "Managing Director"},
        {"name": "Nimal Perera", "role": "Service Provider", "party_type": "individual"},
    ],
    "financial_terms": [
        {"description": "Monthly fee", "amount": 150000, "currency": "Rs.", "frequency": "monthly"},
        {"description": "Setup fee", "amount": "75,000", "currency": "LKR", "frequency": "one_time"},
        {"description": "Late payment interest", "amount": None, "currency": None, "frequency": "percentage", "terms": "2% per month"},
    ],
    "total_contract_value": 1875000,
    "total_contract_value_currency": "LKR",
    "confidence": {"start_date": "high", "end_date": "medium"},
    "warnings": [],
}


def test_postprocess_calculates_end_and_status():
    out = postprocess(SAMPLE_OUTPUT, today=date(2026, 10, 9))
    d = out["derived"]
    assert d["effective_end"] == "2027-03-31"
    assert d["end_date_calculated"]
    assert d["status"] == "active"
    assert out["financial_terms"][0]["currency"] == "LKR"
    assert out["financial_terms"][1]["amount"] == 75000.0
    assert not d["needs_review"]


def test_postprocess_expiring_and_placeholders():
    raw = dict(SAMPLE_OUTPUT, agreement_title="[AGREEMENT TITLE]", start_date="01/09/2025", duration_months=None,
               end_date="2026-11-15")
    out = postprocess(raw, today=date(2026, 10, 9))
    assert out["agreement_title"] is None
    assert out["start_date"] == "2025-09-01"          # day-first parsing
    assert out["derived"]["status"] == "expiring_soon"
    assert "agreement_title" in out["derived"]["missing_fields"]


def test_end_before_start_warns():
    out = postprocess(dict(SAMPLE_OUTPUT, end_date="2026-01-01"), today=date(2026, 10, 9))
    assert any("before the start" in w for w in out["warnings"])


class FakeClient:
    """Mimics anthropic.Anthropic().messages.create and records the request."""
    def __init__(self):
        self.calls = []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        block = SimpleNamespace(type="tool_use", input=SAMPLE_OUTPUT)
        return SimpleNamespace(content=[block], stop_reason="tool_use",
                               usage=SimpleNamespace(input_tokens=1200, output_tokens=600))


def test_full_pipeline(tmp_path, monkeypatch):
    import extractor
    from extractor import storage

    monkeypatch.setattr(storage, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(extractor, "UPLOAD_DIR", str(tmp_path / "up"))
    fake = FakeClient()

    for name in ["service_agreement_text.pdf", "service_agreement_scanned.pdf",
                 "service_agreement_photo.jpg", "service_agreement.docx"]:
        res = extractor.process_file(read(name), name, client=fake, model="test-model")
        assert not res.duplicate
        assert res.data["derived"]["effective_end"] == "2027-03-31"

    # forced tool call with the schema
    req = fake.calls[1]
    assert req["tool_choice"] == {"type": "tool", "name": "record_agreement_details"}
    assert req["messages"][0]["content"][0]["type"] == "document"     # scanned PDF sent as document
    assert fake.calls[2]["messages"][0]["content"][0]["type"] == "image"

    # duplicate upload doesn't call the API again
    res = extractor.process_file(read("service_agreement.docx"), "again.docx", client=fake)
    assert res.duplicate and len(fake.calls) == 4

    rows = storage.list_all()
    assert len(rows) == 4 and rows[0]["parties"].startswith("Lanka Data")
