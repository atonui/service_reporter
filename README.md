# Service Intelligence v0.10.0

An auditable pipeline for turning field-service work orders into structured records and quarterly reports.

## How To Run Locally

On Windows PowerShell:
```powershell
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```
Then:
```bash
pytest
uvicorn backend.app.main:app --reload
```
For AI extraction, set your API credentials in the same PowerShell session before starting the server:
```powershell
$env:OPENAI_API_KEY="your-api-key"
$env:OPENAI_MODEL="gpt-4o-mini"
$env:OPENAI_MAX_OUTPUT_TOKENS="16000"
$env:OPENAI_REASONING_EFFORT="low"
uvicorn backend.app.main:app --reload
