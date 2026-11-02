# Agreement Extractor

Upload an agreement (typed PDF, scanned PDF, photo or Word file) and the system pulls out its key details with Claude, saves them to a database, and flags anything a person should check.

**Extracted fields:** title, type, reference no., parties (name, role, company/individual, address, reg./NIC no., signatory and title), signing date, start date, end date, term, renewal, termination notice, purpose, every money term (amount, currency, frequency, payer/payee, conditions), total contract value, governing law, key obligations, whether it is signed, plus evidence snippets, confidence levels and warnings.

**Calculated by the system:** end date from start date + term, status (active / expiring within 60 days / expired / not started / no end date), currency normalisation (Rs. → LKR), missing fields and a "needs review" flag.

## How it works

```
upload ─► loaders.py ─────────────► llm.py ─────────────► postprocess.py ─► storage.py (SQLite)
          text PDF  → text            Claude API,           clean dates,      + original file
          scanned   → PDF pages       forced tool call      calc end date,    in data/uploads
          image     → image blocks    with JSON schema      status, checks
          .docx     → text + tables   (schema.py)
```

- Scanned PDFs and photos are read directly by Claude's vision, so no Tesseract/OCR install is needed.
- Uploading the same file twice reuses the saved result (matched by SHA-256 hash), so you don't pay twice.
- The prompt tells the model to return `null` instead of guessing, and to treat placeholders like `[date]` as empty.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

On Windows PowerShell, use:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Run these commands from the project directory (the directory containing `requirements.txt`). If PowerShell blocks the activation script, allow locally created scripts for your user with `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

Get an API key at https://console.anthropic.com. Check https://docs.claude.com for current model names and set `CLAUDE_MODEL` in `.env` if needed.
Replace the example `ANTHROPIC_API_KEY` value in `.env` with your real key before uploading documents.

## Run

**Web app (for staff):**
```bash
streamlit run app.py
```
- *Upload* – drop one or many files, click **Extract details**.
- *All agreements* – search, filter by status/type, open a record, export to Excel/CSV.
- *Review* – records with warnings or missing fields; correct them and save (marked as reviewed).

**REST API (to connect your existing system):**
```bash
uvicorn api:app --port 8000
curl -H "X-API-Key: $API_TOKEN" -F "file=@contract.pdf" http://localhost:8000/agreements
```
Endpoints: `POST /agreements`, `GET /agreements?status=expiring_soon`, `GET /agreements/{id}`, `GET /health`. Docs at `/docs`.

## Tests

```bash
python samples/make_samples.py   # creates text PDF, scanned PDF, photo and .docx samples
pytest -q                        # runs offline with a fake Claude client
```
To check real accuracy, upload the files in `samples/` (and 20–30 of your own agreements) and compare against what a person reads from them.

## Before using with real company documents

- **Data privacy:** agreement contents are sent to the Anthropic API. Check this against your company's data policy and any client confidentiality terms. Anthropic's commercial terms and data-retention options are on their website.
- **Access:** set `APP_PASSWORD` and `API_TOKEN` in `.env`. For wider use, put the app behind your company login (SSO/reverse proxy) and HTTPS.
- **Storage:** `data/` holds the database and original files. Back it up, and restrict who can read it. For many users, swap SQLite for PostgreSQL (only `storage.py` changes).
- **Human check:** treat extracted dates and amounts as a draft until someone reviews flagged records, especially for scanned or handwritten documents.
- **Cost:** each new document is one API call. Text documents are cheap; scanned PDFs cost more because each page is sent as an image. Token counts are stored per record (`input_tokens`, `output_tokens`).

## Project layout

```
app.py                 Streamlit web app
api.py                 FastAPI REST API
extractor/
  schema.py            fields the model must return
  loaders.py           PDF / scan / image / Word → API content
  llm.py               Claude call and prompt
  postprocess.py       cleaning, end-date calculation, status, warnings
  storage.py           SQLite database
samples/               sample generator + sample agreements
tests/                 pytest suite
```

## Ideas for next steps

- Email or Slack reminders for agreements expiring in the next 30/60 days (a daily job calling `GET /agreements?status=expiring_soon`).
- Add custom fields your company cares about (e.g. insurance clauses, penalties) by adding them to `schema.py`.
- Accuracy dashboard: keep reviewer corrections and measure field-level accuracy over time.
