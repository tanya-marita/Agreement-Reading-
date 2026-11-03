"""Offline tests: loaders, post-processing and the full pipeline with a fake Claude client.
Run: pytest -q"""
import json
import os
import sys
from datetime import date
from types import SimpleNamespace

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
SAMPLES = os.path.join(ROOT, "samples")

from extractor.loaders import UnsupportedFileError, load_file  # noqa: E402
from extractor.llm import (  # noqa: E402
    ExtractionError,
    _api_status_error_message,
    _google_compatible_schema,
    _google_api_error_message,
    _google_document_parts,
    api_key_configuration_error,
    extract_document,
    extract_with_google,
    get_client,
)
from extractor.postprocess import postprocess  # noqa: E402
from extractor.schema import AGREEMENT_SCHEMA  # noqa: E402


@pytest.mark.parametrize(
    "provider,api_key",
    [
        ("anthropic", ""),
        ("anthropic", "sk-ant-..."),
        ("anthropic", "your_api_key_here"),
        ("anthropic", "<your-api-key>"),
        ("anthropic", "AQ.example"),
        ("google", ""),
        ("google", "..."),
    ],
)
def test_get_client_rejects_missing_or_wrong_provider_keys(provider, api_key):
    with pytest.raises(ExtractionError):
        get_client(provider=provider, api_key=api_key)


def test_api_key_configuration_accepts_anthropic_key_format():
    assert api_key_configuration_error("anthropic", "sk-ant-api03-example") is None


def test_api_key_configuration_accepts_google_key_without_anthropic_prefix():
    assert api_key_configuration_error("google", "google-key-example") is None


def test_wrong_anthropic_key_gets_actionable_configuration_error():
    error = api_key_configuration_error("anthropic", "google-key-example")
    assert error is not None
    assert "not an Anthropic Console API key" in error
    assert "sk-ant-" in error


def test_google_auth_error_has_provider_specific_guidance():
    message = _google_api_error_message(401, "unauthorized")
    assert "GOOGLE_API_KEY" in message
    assert "AI Studio" in message


def test_google_overload_error_explains_retry_and_fallback():
    message = _google_api_error_message(503, "high demand")
    assert "automatic retries and the fallback model were unsuccessful" in message
    assert "retry" in message


def test_google_extraction_uses_structured_output():
    class FakeGoogleClient:
        def __init__(self):
            self.models = self
            self.request = None

        def generate_content(self, **kwargs):
            self.request = kwargs
            return SimpleNamespace(
                text=json.dumps(SAMPLE_OUTPUT),
                usage_metadata=SimpleNamespace(prompt_token_count=100, candidates_token_count=50),
            )

    client = FakeGoogleClient()
    doc = load_file(read("service_agreement_text.pdf"), "agreement.pdf")
    result = extract_with_google(doc, client=client, model="test-gemini")

    assert result.data == SAMPLE_OUTPUT
    assert result.model == "test-gemini"
    assert (result.input_tokens, result.output_tokens) == (100, 50)
    assert client.request["config"].response_mime_type == "application/json"
    google_schema = client.request["config"].response_schema
    assert google_schema["properties"]["signing_date"] == {
        "type": "string",
        "description": AGREEMENT_SCHEMA["properties"]["signing_date"]["description"],
        "nullable": True,
    }
    assert client.request["model"] == "test-gemini"


def test_google_schema_converts_nullable_unions():
    schema = _google_compatible_schema(AGREEMENT_SCHEMA)
    assert schema["properties"]["signing_date"]["type"] == "string"
    assert schema["properties"]["signing_date"]["nullable"] is True
    assert isinstance(schema["properties"]["parties"]["items"]["properties"]["name"]["type"], str)


def test_google_scanned_pdf_is_sent_as_pdf_part():
    from google.genai import types

    doc = load_file(read("service_agreement_scanned.pdf"), "scanned.pdf")
    parts = _google_document_parts(doc)
    assert len(parts) == 1
    assert isinstance(parts[0], types.Part)
    assert parts[0].inline_data.mime_type == "application/pdf"


def test_google_image_is_sent_as_image_part():
    from google.genai import types

    doc = load_file(read("service_agreement_photo.jpg"), "photo.jpg")
    parts = _google_document_parts(doc)
    assert len(parts) == 1
    assert isinstance(parts[0], types.Part)
    assert parts[0].inline_data.mime_type == "image/jpeg"


def test_google_extraction_uses_fallback_model_after_overload(monkeypatch):
    from google.genai import errors
    import extractor.llm as llm

    class FakeGoogleClient:
        def __init__(self):
            self.models = self
            self.requested_models = []

        def generate_content(self, *, model, **kwargs):
            self.requested_models.append(model)
            if model == "primary-model":
                raise errors.APIError(
                    503,
                    {"error": {"code": 503, "message": "high demand", "status": "UNAVAILABLE"}},
                )
            return SimpleNamespace(
                text=json.dumps(SAMPLE_OUTPUT),
                usage_metadata=SimpleNamespace(prompt_token_count=100, candidates_token_count=50),
            )

    monkeypatch.setattr(llm, "DEFAULT_GOOGLE_FALLBACK_MODEL", "fallback-model")
    client = FakeGoogleClient()
    doc = load_file(read("service_agreement_text.pdf"), "agreement.pdf")

    result = extract_with_google(doc, client=client, model="primary-model")

    assert client.requested_models == ["primary-model", "fallback-model"]
    assert result.model == "fallback-model"
    assert any("processed with fallback-model" in warning for warning in result.data["warnings"])


def test_unknown_provider_is_rejected():
    with pytest.raises(ExtractionError, match="LLM_PROVIDER"):
        extract_document(None, provider="unknown")


def test_invalid_api_key_error_gives_safe_recovery_steps():
    message = _api_status_error_message(401, "Invalid bearer token")
    assert "Anthropic rejected the API key" in message
    assert "replace ANTHROPIC_API_KEY" in message
    assert "Invalid bearer token" not in message


def test_forbidden_api_error_mentions_model_permissions():
    message = _api_status_error_message(403, "forbidden")
    assert "permission to use the configured model" in message


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
