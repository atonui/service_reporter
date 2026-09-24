# Service Intelligence v0.11.0

An auditable pipeline for turning field-service work orders into trustworthy structured records and, later, quarterly SLA reports.

The authoritative human-readable requirements, scope, rules, and decision log are maintained in
[`PROJECT_REQUIREMENTS.md`](PROJECT_REQUIREMENTS.md).

The current milestones contain:

- a strict Pydantic `ServiceEvent` schema;
- separate raw, normalized, and computed values;
- field-level evidence and confidence;
- deterministic downtime and labour calculations;
- FastAPI endpoints for schema discovery and validation;
- PDF upload with page-aware embedded-text extraction;
- optional local OCR fallback for image-only PDF pages;
- SHA-256 document fingerprinting and OCR-needed page flags;
- schema-constrained model extraction into `ServiceEvent`;
- raw JSON validation fallback when an SDK does not populate `output_parsed`;
- evidence verification and deterministic human-review flags;
- browser workflow for upload, review, quarterly reporting, and PDF download;
- PCSN-aware machine and site availability reporting;
- a test fixture grounded in work order `WO-004479870`.

## Run locally

```bash
python -m venv .venv
```

On Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

On macOS or Linux:

```bash
source .venv/bin/activate
pip install -e '.[dev]'
```

Then:

```bash
pytest
uvicorn backend.app.main:app --reload
```

Tesseract is optional and only needed for scanned/image-only work orders. If all work orders
contain selectable text, skip this installation completely:

```powershell
winget install --id UB-Mannheim.TesseractOCR -e
```

Close and reopen PowerShell after installation. If Windows still cannot find it, set:

```powershell
$env:TESSERACT_CMD="C:\Program Files\Tesseract-OCR\tesseract.exe"
```

For AI extraction, set your API credentials in the same PowerShell session before starting the server:

```powershell
$env:OPENAI_API_KEY="your-api-key"
$env:OPENAI_MODEL="gpt-4o-mini"
$env:OPENAI_MAX_OUTPUT_TOKENS="16000"
$env:OPENAI_REASONING_EFFORT="low"
uvicorn backend.app.main:app --reload
```

Do not put the API key in source code or commit it to Git.

Open `http://127.0.0.1:8000/` for the application. The interactive API remains available at
`http://127.0.0.1:8000/docs`.

## Endpoints

- `GET /health`
- `GET /` (application home and workflow navigation)
- `GET /reports` (quarterly report dashboard and PDF download)
- `GET /v1/service-events/schema`
- `POST /v1/service-events/validate`
- `POST /v1/reports/quarterly`
- `GET /v1/service-events` (saved extraction records)
- `POST /v1/reports/quarterly/stored` (report from saved, report-ready events)
- `POST /v1/documents/parse` (multipart form field: `file`)
- `POST /v1/documents/extract` (multipart form field: `file`)
- `POST /v1/documents/extract/batch` (multipart form field: `files`, up to 20 PDFs)
- `GET /batch-upload` (browser page with a reliable multi-file picker)
- `GET /review` (manual correction, approval, and audit-history console)
- `GET /v1/service-events/{id}` (one saved event with review history)
- `PUT /v1/service-events/{id}` (save a corrected full event)
- `POST /v1/service-events/{id}/approve` (approve an event for reporting)
- `POST /v1/reports/quarterly/stored/pdf` (downloadable PDF)

The easiest way to try PDF parsing is through `http://127.0.0.1:8000/docs`:

1. Expand `POST /v1/documents/parse`.
2. Select **Try it out**.
3. Choose a work-order PDF and click **Execute**.
4. Inspect the per-page text and `pages_requiring_ocr` response.

To create a structured service event, repeat the process with `POST /v1/documents/extract`.
Its response includes the event, deterministic metrics, validation checks, and any review flags.

## Quarterly reporting

Open `http://127.0.0.1:8000/reports`, select the year and quarter, enter the default working hours
per machine, optionally filter by site or PCSN, and choose **Generate report**. The page displays
machine, site, and fleet availability with the quarterly detail tables and a **Download PDF** button.

Send validated `ServiceEvent` objects to `POST /v1/reports/quarterly` with a year, quarter,
and optional per-machine working-hours basis and PCSN overrides. The response contains auditable incident rows, unplanned
downtime and uptime, fault/intervention/service-type breakdowns, parts usage, repeat issues,
and report notes. Only `corrective_breakdown` events are included in uptime downtime; scheduled
maintenance and customer requests remain visible in the report but are excluded from that metric.

Every successful call to `POST /v1/documents/extract` is also saved locally in SQLite. To produce
a report without copying event JSON, call `POST /v1/reports/quarterly/stored` with only:

```json
{"year": 2026, "quarter": 3, "working_hours_per_machine": 504}
```

Automatic reports include only records where `review_required` is false. Inspect all saved records
with `GET /v1/service-events`; re-uploading the same PDF replaces its prior extracted record.

For several work orders, use `POST /v1/documents/extract/batch` and select up to 20 PDFs in the
`files` field. Each file gets its own success or failure result, and successful records are saved
even if another file requires OCR or fails validation.

If Swagger UI does not render its multiple-file input correctly, open `http://127.0.0.1:8000/batch-upload`.
This page provides a native multi-file picker and submits the same batch endpoint directly.

To download the stored report as a formatted A4 document, send the same year, quarter, and working
hours body to `POST /v1/reports/quarterly/stored/pdf`. The response downloads as
`service-report-Q<quarter>-<year>.pdf`.

## Manual review and approval

Open `http://127.0.0.1:8000/review` to review saved events. Select an event, edit its JSON,
enter your name, and choose **Save correction**. Corrected events return to pending-review status
until **Approve for reports** is selected. Each correction records the changed field paths, old and
new values, reviewer, timestamp, and note. Source filename and SHA-256 cannot be edited.

Existing SQLite databases are upgraded automatically when v0.8.0 starts. Events that previously
had `review_required=false` begin as approved; events that required review remain pending.

## Project structure

```text
service-intelligence/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── schemas/
│   │   └── services/
│   └── tests/
├── docs/
├── PROJECT_REQUIREMENTS.md
├── pyproject.toml
└── README.md
```

## Review behavior

`review_required` becomes true when, for example:

- quoted evidence is absent from the claimed page;
- a key field has no evidence;
- the model marks a claim as low confidence;
- reported labour or downtime values fail reconciliation checks.

## Next milestone

Add site alias management, PCSN catalogue management, and richer field-by-field review controls.

## Current limitations

- OCR requires the Tesseract desktop executable; Python packages alone are not sufficient.
- Files are parsed in memory and are not persisted.
- API extraction requires an OpenAI API key and consumes model tokens.
- Extraction has a 16,000-token output budget by default. This is needed for detailed
  work orders and their evidence records; lower it only after you have tested accuracy.
