from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from backend.app.schemas.document import ParsedDocument
from backend.app.schemas.extraction import BatchExtractionItem, BatchExtractionResult, ExtractionResult
from backend.app.schemas.report import (
    QuarterlyReport,
    QuarterlyReportRequest,
    StoredQuarterlyReportRequest,
)
from backend.app.schemas.service_event import ServiceEvent
from backend.app.schemas.storage import (
    EventApprovalRequest,
    EventCorrectionRequest,
    StoredServiceEvent,
)
from backend.app.services.extraction import (
    ExtractionConfigurationError,
    ExtractionError,
    extract_service_event,
)
from backend.app.services.pdf_parser import (
    MAX_PDF_BYTES,
    OcrError,
    PdfValidationError,
    parse_pdf_bytes_with_ocr,
)
from backend.app.services.reporting import build_quarterly_report
from backend.app.services.event_store import (
    approve_event,
    correct_event,
    get_event,
    list_events,
    reportable_events,
    save_extraction,
)
from backend.app.services.report_pdf import render_quarterly_report_pdf
from backend.app.services.validation import validate_service_event
from backend.app.ui import home_page, reports_page

router = APIRouter()


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def application_home() -> str:
    return home_page()


@router.get("/reports", response_class=HTMLResponse, include_in_schema=False)
def quarterly_reports_page() -> str:
    return reports_page()


@router.get("/review", response_class=HTMLResponse, include_in_schema=False)
def review_page() -> str:
    """Browser review console for corrections, approval, and audit history."""
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Service Intelligence - Event Review</title>
  <style>
    :root { --navy:#17324d; --blue:#2878b5; --pale:#eaf3f9; --line:#ced8e1; --text:#263442; --danger:#a52828; --success:#19723b; }
    * { box-sizing:border-box; }
    body { margin:0; background:#f4f7fa; color:var(--text); font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }
    main { width:min(1180px,calc(100% - 32px)); margin:32px auto; }
    .card { background:white; border:1px solid var(--line); border-radius:14px; padding:24px; box-shadow:0 10px 30px rgba(23,50,77,.08); }
    h1,h2 { color:var(--navy); } h1 { margin:0 0 6px; } h2 { margin:0 0 12px; font-size:19px; }
    .layout { display:grid; grid-template-columns:320px 1fr; gap:22px; margin-top:22px; }
    #events { list-style:none; margin:0; padding:0; max-height:680px; overflow:auto; }
    #events button { width:100%; text-align:left; background:white; color:var(--text); border:1px solid var(--line); margin:0 0 8px; padding:11px; }
    #events button.selected { border-color:var(--blue); background:var(--pale); }
    .badge { float:right; padding:2px 7px; border-radius:10px; font-size:12px; background:#ffe9b3; }
    .badge.approved { background:#d9f2e2; color:var(--success); }
    textarea { width:100%; min-height:480px; resize:vertical; padding:12px; border:1px solid var(--line); border-radius:8px; font:13px/1.45 Consolas,monospace; }
    input { width:100%; padding:9px; border:1px solid var(--line); border-radius:7px; }
    .form { display:grid; grid-template-columns:1fr 2fr; gap:12px; margin:12px 0; }
    .actions { display:flex; gap:10px; align-items:center; }
    button { border:0; border-radius:8px; padding:10px 15px; background:var(--navy); color:white; font-weight:700; cursor:pointer; }
    button.secondary { background:var(--blue); } button:disabled { opacity:.45; cursor:not-allowed; }
    #message { font-weight:600; } .error { color:var(--danger); } .ok { color:var(--success); }
    details { margin-top:16px; } pre { white-space:pre-wrap; background:#f6f8fa; padding:12px; border-radius:8px; max-height:260px; overflow:auto; }
    .links { margin-top:18px; } .links a { color:var(--blue); margin-right:18px; }
    @media (max-width:800px) { .layout,.form { grid-template-columns:1fr; } }
  </style>
</head>
<body><main><section class="card">
  <h1>Service Event Review</h1>
  <p>Correct a saved event, then approve it for quarterly reporting. Every change is retained.</p>
  <div class="layout">
    <aside><h2>Saved events</h2><ul id="events"></ul></aside>
    <section>
      <h2 id="title">Select an event</h2>
      <textarea id="editor" disabled aria-label="Service event JSON"></textarea>
      <div class="form">
        <label>Reviewer<input id="actor" placeholder="Your name"></label>
        <label>Note (optional)<input id="note" placeholder="What was checked or corrected"></label>
      </div>
      <div class="actions">
        <button id="save" class="secondary" disabled>Save correction</button>
        <button id="approve" disabled>Approve for reports</button>
        <span id="message" role="status"></span>
      </div>
      <details><summary>Correction and approval history</summary><pre id="history">[]</pre></details>
    </section>
  </div>
    <div class="links"><a href="/">Home</a><a href="/batch-upload">Batch upload</a><a href="/reports">Reports</a><a href="/docs">API documentation</a></div>
</section></main>
<script>
  const list = document.getElementById('events');
  const editor = document.getElementById('editor');
  const title = document.getElementById('title');
  const actor = document.getElementById('actor');
  const note = document.getElementById('note');
  const save = document.getElementById('save');
  const approve = document.getElementById('approve');
  const message = document.getElementById('message');
  const history = document.getElementById('history');
  let selected = null;

  function setMessage(text, kind='') { message.textContent=text; message.className=kind; }
  async function request(url, options={}) {
    const response = await fetch(url, options);
    const payload = await response.json();
    if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : JSON.stringify(payload.detail));
    return payload;
  }
  async function refresh(selectId=null) {
    const records = await request('/v1/service-events?include_review_required=true');
    list.replaceChildren();
    records.forEach(record => {
      const item=document.createElement('li'); const button=document.createElement('button');
      const badge=document.createElement('span'); badge.className='badge '+(record.approval_status==='approved'?'approved':'');
      badge.textContent=record.approval_status==='approved'?'approved':'review';
      button.textContent=record.event.identification.work_order_number+' ';
      button.append(badge); button.onclick=()=>selectRecord(record,button); item.append(button); list.append(item);
      if (record.id === selectId) selectRecord(record,button);
    });
  }
  function selectRecord(record, button) {
    selected=record; document.querySelectorAll('#events button').forEach(x=>x.classList.remove('selected')); button.classList.add('selected');
    title.textContent=record.event.identification.work_order_number+' — '+record.approval_status;
    editor.value=JSON.stringify(record.event,null,2); editor.disabled=false; save.disabled=false; approve.disabled=false;
    history.textContent=JSON.stringify(record.correction_history,null,2); setMessage('');
  }
  async function act(kind) {
    if (!selected || !actor.value.trim()) { setMessage('Enter the reviewer name.','error'); return; }
    save.disabled=true; approve.disabled=true; setMessage(kind==='save'?'Saving...':'Approving...');
    try {
      const options={method:kind==='save'?'PUT':'POST',headers:{'Content-Type':'application/json'}};
      if (kind==='save') options.body=JSON.stringify({event:JSON.parse(editor.value),corrected_by:actor.value.trim(),note:note.value.trim()||null});
      else options.body=JSON.stringify({approved_by:actor.value.trim(),note:note.value.trim()||null});
      const suffix=kind==='save'?'':'/approve'; await request('/v1/service-events/'+selected.id+suffix,options);
      await refresh(selected.id); setMessage(kind==='save'?'Correction saved; approval is still required.':'Approved and ready for reports.','ok');
    } catch(error) { setMessage(error.message,'error'); save.disabled=false; approve.disabled=false; }
  }
  save.onclick=()=>act('save'); approve.onclick=()=>act('approve');
  refresh().catch(error=>setMessage(error.message,'error'));
</script></body></html>"""


@router.get("/batch-upload", response_class=HTMLResponse, include_in_schema=False)
def batch_upload_page() -> str:
    """Small browser UI that avoids Swagger UI's inconsistent multi-file control."""
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Service Intelligence - Batch Upload</title>
  <style>
    :root { color-scheme: light; --navy:#17324d; --blue:#2878b5; --pale:#eaf3f9; --line:#ced8e1; --text:#263442; --danger:#a52828; --success:#19723b; }
    * { box-sizing:border-box; }
    body { margin:0; background:#f4f7fa; color:var(--text); font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }
    main { width:min(900px,calc(100% - 32px)); margin:48px auto; }
    .card { background:white; border:1px solid var(--line); border-radius:14px; padding:28px; box-shadow:0 10px 30px rgba(23,50,77,.08); }
    h1 { margin:0 0 8px; color:var(--navy); font-size:28px; }
    p { margin:0 0 22px; }
    .drop { display:block; padding:28px; border:2px dashed var(--blue); border-radius:12px; background:var(--pale); text-align:center; cursor:pointer; }
    .drop:hover { background:#dceef8; }
    input[type=file] { position:absolute; width:1px; height:1px; opacity:0; }
    .choose { display:inline-block; padding:10px 16px; border-radius:8px; background:var(--blue); color:white; font-weight:700; }
    #selection { margin:18px 0; padding-left:20px; max-height:180px; overflow:auto; }
    button { border:0; border-radius:8px; padding:11px 18px; background:var(--navy); color:white; font-weight:700; cursor:pointer; }
    button:disabled { opacity:.45; cursor:not-allowed; }
    #status { margin-left:12px; font-weight:600; }
    .summary { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; margin-top:24px; }
    .metric { padding:14px; border:1px solid var(--line); border-radius:10px; text-align:center; }
    .metric strong { display:block; color:var(--navy); font-size:24px; }
    table { width:100%; border-collapse:collapse; margin-top:18px; }
    th,td { padding:10px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }
    th { color:var(--navy); }
    .ok { color:var(--success); font-weight:700; }
    .failed { color:var(--danger); font-weight:700; }
    .links { margin-top:22px; }
    .links a { color:var(--blue); margin-right:18px; }
    @media (max-width:620px) { .summary { grid-template-columns:1fr; } .card { padding:20px; } }
  </style>
</head>
<body>
<main>
  <section class="card">
    <h1>Batch Work-Order Extraction</h1>
    <p>Select up to 20 PDF work orders. Successful files are saved even when another file fails.</p>
    <label class="drop" for="files">
      <span class="choose">Choose PDF files</span><br>
      <small>Use Ctrl or Shift in the Windows dialog to select multiple files.</small>
    </label>
    <input id="files" type="file" accept="application/pdf,.pdf" multiple>
    <ol id="selection"><li>No files selected.</li></ol>
    <button id="upload" type="button" disabled>Extract and save</button>
    <span id="status" role="status"></span>
    <div id="results" hidden>
      <div class="summary">
        <div class="metric"><strong id="received">0</strong>Received</div>
        <div class="metric"><strong id="succeeded">0</strong>Succeeded</div>
        <div class="metric"><strong id="failed">0</strong>Failed</div>
      </div>
      <table>
        <thead><tr><th>File</th><th>Status</th><th>Review</th><th>Message</th></tr></thead>
        <tbody id="rows"></tbody>
      </table>
    </div>
    <div class="links"><a href="/">Home</a><a href="/reports">Reports</a><a href="/docs">API documentation</a><a href="/v1/service-events">Saved events</a></div>
  </section>
</main>
<script>
  const picker = document.getElementById('files');
  const upload = document.getElementById('upload');
  const selection = document.getElementById('selection');
  const status = document.getElementById('status');
  const results = document.getElementById('results');
  const rows = document.getElementById('rows');

  picker.addEventListener('change', () => {
    selection.replaceChildren();
    const files = [...picker.files];
    if (!files.length) {
      const item = document.createElement('li');
      item.textContent = 'No files selected.';
      selection.append(item);
    } else {
      files.forEach(file => {
        const item = document.createElement('li');
        item.textContent = `${file.name} (${Math.ceil(file.size / 1024)} KB)`;
        selection.append(item);
      });
    }
    upload.disabled = files.length === 0 || files.length > 20;
    status.textContent = files.length > 20 ? 'Select no more than 20 files.' : '';
  });

  upload.addEventListener('click', async () => {
    const files = [...picker.files];
    if (!files.length || files.length > 20) return;
    const form = new FormData();
    files.forEach(file => form.append('files', file, file.name));
    upload.disabled = true;
    status.textContent = `Processing ${files.length} file(s)...`;
    results.hidden = true;
    try {
      const response = await fetch('/v1/documents/extract/batch', { method:'POST', body:form });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
      document.getElementById('received').textContent = payload.files_received;
      document.getElementById('succeeded').textContent = payload.files_succeeded;
      document.getElementById('failed').textContent = payload.files_failed;
      rows.replaceChildren();
      payload.items.forEach(item => {
        const row = document.createElement('tr');
        const review = item.extraction ? String(item.extraction.review_required) : '-';
        const values = [item.file_name, item.status, review, item.error || 'Saved'];
        values.forEach((value, index) => {
          const cell = document.createElement('td');
          cell.textContent = value;
          if (index === 1) cell.className = item.status === 'succeeded' ? 'ok' : 'failed';
          row.append(cell);
        });
        rows.append(row);
      });
      results.hidden = false;
      status.textContent = 'Batch complete.';
    } catch (error) {
      status.textContent = `Upload failed: ${error.message}`;
    } finally {
      upload.disabled = false;
    }
  });
</script>
</body>
</html>"""


async def _parse_upload(file: UploadFile) -> ParsedDocument:
    if file.content_type not in {"application/pdf", "application/octet-stream"}:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only PDF uploads are supported.",
        )

    data = await file.read(MAX_PDF_BYTES + 1)
    await file.close()
    try:
        return await run_in_threadpool(
            parse_pdf_bytes_with_ocr, file.filename or "uploaded.pdf", data
        )
    except OcrError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except PdfValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/v1/service-events/schema")
def service_event_schema() -> dict:
    """Expose the exact JSON Schema expected from the extraction layer."""
    return ServiceEvent.model_json_schema()


@router.post("/v1/documents/parse", response_model=ParsedDocument)
async def parse_document(file: UploadFile = File(...)) -> ParsedDocument:
    """Validate a PDF and extract embedded text while preserving page boundaries."""
    return await _parse_upload(file)


@router.post("/v1/documents/extract", response_model=ExtractionResult)
async def extract_document(file: UploadFile = File(...)) -> ExtractionResult:
    """Parse a PDF, extract a schema-constrained event, and run review checks."""
    document = await _parse_upload(file)
    if document.pages_requiring_ocr:
        pages = ", ".join(str(page) for page in document.pages_requiring_ocr)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"OCR is required before extraction for page(s): {pages}.",
        )
    try:
        result = await run_in_threadpool(extract_service_event, document)
        await run_in_threadpool(save_extraction, result)
        return result
    except ExtractionConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except ExtractionError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc


@router.post("/v1/documents/extract/batch", response_model=BatchExtractionResult)
async def extract_documents_batch(
    files: Annotated[
        list[UploadFile],
        File(description="Select between 1 and 20 PDF work orders."),
    ],
) -> BatchExtractionResult:
    """Extract and save up to 20 PDFs; one bad file does not discard successful files."""
    if not files or len(files) > 20:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Upload between 1 and 20 PDF files.",
        )
    items: list[BatchExtractionItem] = []
    for file in files:
        file_name = file.filename or "uploaded.pdf"
        try:
            document = await _parse_upload(file)
            if document.pages_requiring_ocr:
                pages = ", ".join(str(page) for page in document.pages_requiring_ocr)
                raise ValueError(f"OCR is required before extraction for page(s): {pages}.")
            result = await run_in_threadpool(extract_service_event, document)
            await run_in_threadpool(save_extraction, result)
            items.append(
                BatchExtractionItem(
                    file_name=file_name,
                    status="succeeded",
                    extraction=result,
                )
            )
        except HTTPException as exc:
            items.append(
                BatchExtractionItem(file_name=file_name, status="failed", error=str(exc.detail))
            )
        except (ExtractionConfigurationError, ExtractionError, ValueError) as exc:
            items.append(BatchExtractionItem(file_name=file_name, status="failed", error=str(exc)))
    succeeded = sum(item.status == "succeeded" for item in items)
    return BatchExtractionResult(
        files_received=len(items),
        files_succeeded=succeeded,
        files_failed=len(items) - succeeded,
        items=items,
    )


@router.post("/v1/service-events/validate")
def validate_event(event: ServiceEvent) -> dict:
    """Validate an extracted event and return deterministic checks."""
    return {
        "valid": True,
        "event": event.model_dump(mode="json"),
        "checks": validate_service_event(event),
    }


@router.post("/v1/reports/quarterly", response_model=QuarterlyReport)
def quarterly_report(request: QuarterlyReportRequest) -> QuarterlyReport:
    """Aggregate validated work-order events into an auditable quarterly service report."""
    return build_quarterly_report(request)


@router.get("/v1/service-events", response_model=list[StoredServiceEvent])
def stored_service_events(include_review_required: bool = True) -> list[dict]:
    """List persisted extraction records. Use the default view to find records needing review."""
    return list_events(include_review_required)


@router.get("/v1/service-events/{event_id}", response_model=StoredServiceEvent)
def stored_service_event(event_id: int) -> dict:
    """Return one saved event, including its review history."""
    record = get_event(event_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service event not found.")
    return record


@router.put("/v1/service-events/{event_id}", response_model=StoredServiceEvent)
def update_stored_service_event(event_id: int, request: EventCorrectionRequest) -> dict:
    """Save a manual correction and return the event to pending-review status."""
    try:
        record = correct_event(event_id, request)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service event not found.")
    return record


@router.post("/v1/service-events/{event_id}/approve", response_model=StoredServiceEvent)
def approve_stored_service_event(event_id: int, request: EventApprovalRequest) -> dict:
    """Approve a reviewed event so stored quarterly reports can include it."""
    record = approve_event(event_id, request)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service event not found.")
    return record


@router.post("/v1/reports/quarterly/stored", response_model=QuarterlyReport)
def stored_quarterly_report(request: StoredQuarterlyReportRequest) -> QuarterlyReport:
    """Build a report from all stored, report-ready service events."""
    return _build_stored_quarterly_report(request)


def _build_stored_quarterly_report(request: StoredQuarterlyReportRequest) -> QuarterlyReport:
    events = reportable_events()
    if not events:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No report-ready stored service events are available.",
        )
    return build_quarterly_report(
        QuarterlyReportRequest(
            year=request.year,
            quarter=request.quarter,
            working_hours_basis=request.working_hours_basis,
            working_hours_per_machine=request.working_hours_per_machine,
            machine_hours_overrides=request.machine_hours_overrides,
            site_name=request.site_name,
            pcsn=request.pcsn,
            events=events,
        )
    )


@router.post(
    "/v1/reports/quarterly/stored/pdf",
    responses={200: {"content": {"application/pdf": {}}}},
)
def stored_quarterly_report_pdf(request: StoredQuarterlyReportRequest) -> Response:
    """Download the stored quarterly report as a printable PDF."""
    report = _build_stored_quarterly_report(request)
    content = render_quarterly_report_pdf(report)
    file_name = f"service-report-Q{request.quarter}-{request.year}.pdf"
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )
