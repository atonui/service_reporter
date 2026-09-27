from calendar import monthrange
from datetime import date
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from backend.app.schemas.customer_alias import CustomerAlias, CustomerAliasInput
from backend.app.schemas.document import ParsedDocument
from backend.app.schemas.extraction import (
    BatchExtractionItem,
    BatchExtractionResult,
    ExtractionResult,
)
from backend.app.schemas.holiday import Holiday, HolidayInput
from backend.app.schemas.machine_registry import MachineRegistrationInput, RegisteredMachine
from backend.app.schemas.product_catalog import ProductCatalogEntry, ProductCatalogInput
from backend.app.schemas.report import (
    QuarterlyReport,
    QuarterlyReportRequest,
    ReportFilterOptions,
    StoredQuarterlyReportRequest,
    StoredTrendReportRequest,
    TrendPoint,
    TrendReport,
)
from backend.app.schemas.report_profile import ReportProfile, ReportProfileInput
from backend.app.schemas.service_event import ServiceEvent
from backend.app.schemas.storage import (
    EventApprovalRequest,
    EventArchiveRequest,
    EventCorrectionRequest,
    StoredServiceEvent,
)
from backend.app.services.customer_alias_store import (
    canonical_customer_name,
    create_customer_alias,
    customer_alias_map,
    delete_customer_alias,
    list_customer_aliases,
)
from backend.app.services.event_store import (
    approve_event,
    archive_event,
    correct_event,
    get_event,
    list_events,
    reportable_events,
    restore_event,
    save_extraction,
)
from backend.app.services.extraction import (
    ExtractionConfigurationError,
    ExtractionError,
    extract_service_event,
)
from backend.app.services.holiday_store import (
    create_holiday,
    delete_holiday,
    holiday_dates_for_range,
    list_holidays,
    update_holiday,
)
from backend.app.services.machine_store import (
    create_registered_machine,
    list_registered_machines,
    update_registered_machine,
)
from backend.app.services.pdf_parser import (
    MAX_PDF_BYTES,
    OcrError,
    PdfValidationError,
    parse_pdf_bytes_with_ocr,
)
from backend.app.services.product_catalog_store import (
    create_product_catalog_entry,
    delete_product_catalog_entry,
    list_product_catalog,
    update_product_catalog_entry,
)
from backend.app.services.report_excel import render_quarterly_report_excel
from backend.app.services.report_pdf import render_quarterly_report_pdf
from backend.app.services.report_profile_store import (
    create_report_profile,
    delete_report_profile,
    get_report_profile,
    list_report_profiles,
    update_report_profile,
)
from backend.app.services.reporting import (
    build_quarterly_report,
    report_filter_options,
    report_period,
)
from backend.app.services.validation import validate_service_event
from backend.app.ui import (
    holiday_calendar_page,
    home_page,
    machine_register_page,
    report_profiles_page,
    reports_page,
)

router = APIRouter()


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def application_home() -> str:
    return home_page()


@router.get("/reports", response_class=HTMLResponse, include_in_schema=False)
def quarterly_reports_page() -> str:
    return reports_page()


@router.get("/machines", response_class=HTMLResponse, include_in_schema=False)
def machines_page() -> str:
    return machine_register_page()


@router.get("/holidays", response_class=HTMLResponse, include_in_schema=False)
def holidays_page() -> str:
    return holiday_calendar_page()


@router.get("/report-profiles", response_class=HTMLResponse, include_in_schema=False)
def report_profile_page() -> str:
    return report_profiles_page()


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
    :root { --navy:#006b6b; --blue:#009999; --orange:#ec6602; --orange-dark:#b94f00; --pale:#e6f5f5; --line:#c9dddd; --text:#273536; --danger:#a52828; --success:#19723b; }
    * { box-sizing:border-box; }
    body { margin:0; background:#f4f7fa; color:var(--text); font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }
    main { width:min(1180px,calc(100% - 32px)); margin:32px auto; }
    .card { background:white; border:1px solid var(--line); border-radius:14px; padding:24px; box-shadow:0 10px 30px rgba(0,107,107,.08); border-top:4px solid var(--orange); }
    h1,h2 { color:var(--navy); } h1 { margin:0 0 6px; } h2 { margin:0 0 12px; font-size:19px; }
    .layout { display:grid; grid-template-columns:320px 1fr; gap:22px; margin-top:22px; }
    #events { list-style:none; margin:0; padding:0; max-height:680px; overflow:auto; }
    #events button { width:100%; text-align:left; background:white; color:var(--text); border:1px solid var(--line); margin:0 0 8px; padding:11px; }
    #events button.selected { border-color:var(--blue); background:var(--pale); }
    .badge { float:right; padding:2px 7px; border-radius:10px; font-size:12px; background:#ffe9b3; }
    .badge.approved { background:#d9f2e2; color:var(--success); }
    input,select,textarea { width:100%; padding:9px; border:1px solid var(--line); border-radius:7px; background:white; color:var(--text); font:inherit; }
    textarea { min-height:92px; resize:vertical; }
    label { display:block; font-weight:700; color:var(--navy); }
    .review-form { display:grid; gap:14px; }
    .field-card { display:grid; grid-template-columns:minmax(260px,1fr) minmax(260px,.9fr); gap:16px; padding:15px; border:1px solid var(--line); border-radius:10px; background:#fff; }
    .field-card:focus-within { border-color:var(--blue); box-shadow:0 0 0 2px rgba(0,153,153,.12); }
    .hint { color:#647483; font-size:12px; font-weight:400; margin-top:4px; }
    .evidence { background:#f6f8fa; border-left:3px solid var(--blue); border-radius:6px; padding:10px; min-height:64px; }
    .evidence strong { display:block; color:var(--navy); font-size:12px; text-transform:uppercase; letter-spacing:.04em; }
    .quote { margin:5px 0; white-space:pre-wrap; }
    .meta { color:#647483; font-size:12px; }
    .no-evidence { color:#7b8792; font-style:italic; }
    .parts { border:1px solid var(--line); border-radius:10px; padding:15px; }
    .parts-head { display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:10px; }
    .parts-head h3 { margin:0; color:var(--navy); }
    .parts-table { width:100%; border-collapse:collapse; }
    .parts-table th,.parts-table td { padding:7px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }
    .parts-table th { font-size:12px; color:var(--navy); background:#f7f9fb; }
    .parts-table input { min-width:90px; }
    .parts-table .description { min-width:220px; }
    .remove { background:#8b2d2d; padding:8px 10px; }
    .review-flags { margin:0 0 14px; padding:12px 16px 12px 32px; background:#fff8e5; border:1px solid #ead59b; border-radius:8px; }
    .review-flags:empty { display:none; }
    .form { display:grid; grid-template-columns:1fr 2fr; gap:12px; margin:16px 0 12px; }
    .actions { display:flex; gap:10px; align-items:center; }
    button { border:0; border-radius:8px; padding:10px 15px; background:var(--orange-dark); color:white; font-weight:700; cursor:pointer; }
    button.secondary { background:var(--blue); } button:disabled { opacity:.45; cursor:not-allowed; }
    #message { font-weight:600; } .error { color:var(--danger); } .ok { color:var(--success); }
    details { margin-top:18px; } details summary { cursor:pointer; color:var(--navy); font-weight:700; }
    .history-list { display:grid; gap:12px; margin-top:12px; }
    .history-entry { border:1px solid var(--line); border-radius:9px; overflow:hidden; }
    .history-head { display:grid; grid-template-columns:auto 1fr auto; gap:10px; align-items:center; padding:10px 12px; background:#f6f8fa; }
    .history-action { display:inline-block; border-radius:12px; padding:2px 8px; background:#dcecf7; color:var(--navy); font-size:12px; font-weight:800; text-transform:uppercase; }
    .history-action.approval,.history-action.restore { background:#d9f2e2; color:var(--success); }
    .history-action.archive { background:#f7dddd; color:var(--danger); }
    .history-date { color:#647483; font-size:12px; }
    .history-note { margin:0; padding:8px 12px; color:#4c5b68; border-top:1px solid var(--line); }
    .change-table { width:100%; border-collapse:collapse; }
    .change-table th,.change-table td { padding:8px 10px; border-top:1px solid var(--line); text-align:left; vertical-align:top; }
    .change-table th { color:var(--navy); font-size:12px; background:#fbfcfd; }
    .change-value { max-width:260px; white-space:pre-wrap; overflow-wrap:anywhere; }
    .empty-history { color:#647483; font-style:italic; padding:12px; }
    .links { margin-top:18px; } .links a { color:var(--blue); margin-right:18px; }
    @media (max-width:900px) { .layout,.form,.field-card { grid-template-columns:1fr; } .parts-table { display:block; overflow:auto; } }
  </style>
</head>
<body><main><section class="card">
  <h1>Service Event Review</h1>
  <p>Correct and approve saved events for reporting, or archive erroneous records without deleting their history.</p>
  <div class="layout">
    <aside><h2>Saved events</h2><label><input id="include-archived" type="checkbox"> Show archived</label><ul id="events"></ul></aside>
    <section>
      <h2 id="title">Select an event</h2>
      <ul id="review-flags" class="review-flags"></ul>
      <form id="field-editor" class="review-form" hidden>
        <div class="field-card"><label>Customer<input id="customer" autocomplete="off"><span class="hint">Customer and site are one identity.</span></label><div id="evidence-customer" class="evidence"></div></div>
        <div class="field-card"><label>PCSN<input id="pcsn" pattern="[A-Za-z0-9]+" autocomplete="off"></label><div id="evidence-pcsn" class="evidence"></div></div>
        <div class="field-card"><label>Service type<select id="service-type"><option value="preventive_maintenance">Preventive maintenance</option><option value="corrective_breakdown">Corrective breakdown</option><option value="remote_support">Remote support</option><option value="customer_request">Customer request</option><option value="training">Training</option><option value="other">Other</option><option value="unknown">Unknown</option></select></label><div id="evidence-service-type" class="evidence"></div></div>
        <div class="field-card"><label>Fault category<input id="fault-category" list="fault-category-options" autocomplete="off" placeholder="Select or enter a category"><datalist id="fault-category-options"><option value="MLC"><option value="Beam generation"><option value="Imaging"><option value="Patient support"><option value="Cooling"><option value="Electrical power"><option value="Software and controls"><option value="Vacuum"><option value="Safety system"><option value="Mechanical"><option value="Dosimetry and beam quality"><option value="Other"></datalist><span class="hint">Use the suggested taxonomy where possible; free entry remains available.</span></label><div id="evidence-fault-category" class="evidence"></div></div>
        <div class="field-card"><label>Fault subcategory<input id="fault-subcategory" list="fault-subcategory-options" autocomplete="off" placeholder="Select or enter a subcategory"><datalist id="fault-subcategory-options"><option value="MLC interlock"><option value="MLC leaf or motor"><option value="Modulator / thyratron"><option value="Gun / filament"><option value="RF driver / klystron"><option value="Beam steering / tuning"><option value="kV imaging / CBCT"><option value="MV imaging"><option value="Patient support / couch"><option value="Chiller / water cooling"><option value="Mains / UPS / power supply"><option value="Software / workstation"><option value="Network / communication"><option value="Vacuum / HVOC"><option value="Door / safety interlock"><option value="Mechanical motion"><option value="Dosimetry / calibration"></datalist></label><div id="evidence-fault-subcategory" class="evidence"></div></div>
        <div class="field-card"><label>Reported downtime (hours)<input id="downtime" type="number" min="0" step="0.01" placeholder="Not reported"><span class="hint">Only corrective-breakdown downtime reduces uptime.</span></label><div id="evidence-downtime" class="evidence"></div></div>
        <div class="field-card"><label>Subject<textarea id="subject"></textarea></label><div id="evidence-subject" class="evidence"></div></div>
        <div class="field-card"><label>Intervention<textarea id="intervention"></textarea><span class="hint">Use the source wording from the work order.</span></label><div id="evidence-intervention" class="evidence"></div></div>
        <section class="parts"><div class="parts-head"><h3>Parts</h3><button id="add-part" type="button" class="secondary">Add part</button></div><div class="parts-table"><table><thead><tr><th>Part number</th><th>Description</th><th>Quantity</th><th>Source</th><th></th></tr></thead><tbody id="parts-body"></tbody></table></div><div id="evidence-parts" class="evidence"></div></section>
      </form>
      <div class="form">
        <label>Reviewer<input id="actor" placeholder="Your name"></label>
        <label>Note / archive reason<input id="note" placeholder="What was checked, corrected, or why this record is archived"></label>
      </div>
      <div class="actions">
        <button id="save" class="secondary" disabled>Save correction</button>
        <button id="approve" disabled>Approve for reports</button>
        <button id="archive" class="remove" disabled>Archive event</button>
        <button id="restore" class="secondary" hidden disabled>Restore event</button>
        <span id="message" role="status"></span>
      </div>
      <details open><summary>Event history</summary><div id="history" class="history-list"></div></details>
    </section>
  </div>
    <div class="links"><a href="/">Home</a><a href="/batch-upload">Batch upload</a><a href="/reports">Reports</a><a href="/docs">API documentation</a></div>
</section></main>
<script>
  const list = document.getElementById('events');
  const title = document.getElementById('title');
  const fieldEditor = document.getElementById('field-editor');
  const customer = document.getElementById('customer');
  const pcsn = document.getElementById('pcsn');
  const serviceType = document.getElementById('service-type');
  const faultCategory = document.getElementById('fault-category');
  const faultSubcategory = document.getElementById('fault-subcategory');
  const downtime = document.getElementById('downtime');
  const subject = document.getElementById('subject');
  const intervention = document.getElementById('intervention');
  const partsBody = document.getElementById('parts-body');
  const flags = document.getElementById('review-flags');
  const actor = document.getElementById('actor');
  const note = document.getElementById('note');
  const save = document.getElementById('save');
  const approve = document.getElementById('approve');
  const archive = document.getElementById('archive');
  const restore = document.getElementById('restore');
  const includeArchived = document.getElementById('include-archived');
  const message = document.getElementById('message');
  const history = document.getElementById('history');
  let selected = null, dirty = false;

  function setMessage(text, kind='') { message.textContent=text; message.className=kind; }
  async function request(url, options={}) {
    const response = await fetch(url, options);
    const payload = await response.json();
    if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : JSON.stringify(payload.detail));
    return payload;
  }
  async function refresh(selectId=null) {
    const records = await request('/v1/service-events?include_review_required=true&include_archived='+includeArchived.checked);
    list.replaceChildren();
    if (selectId===null) { selected=null;fieldEditor.hidden=true;title.textContent='Select an event';save.disabled=true;approve.disabled=true;archive.disabled=true;restore.hidden=true; }
    records.forEach(record => {
      const item=document.createElement('li'); const button=document.createElement('button');
      const badge=document.createElement('span'); badge.className='badge '+(record.archived?'':record.approval_status==='approved'?'approved':'');
      badge.textContent=record.archived?'archived':record.approval_status==='approved'?'approved':'review';
      button.textContent=record.event.identification.work_order_number+' ';
      button.append(badge); button.onclick=()=>selectRecord(record,button); item.append(button); list.append(item);
      if (record.id === selectId) selectRecord(record,button);
    });
  }
  function evidenceItems(paths=[],prefixes=[]) {
    if (!selected) return [];
    return selected.event.evidence.filter(item=>paths.includes(item.field_path)||prefixes.some(prefix=>item.field_path.startsWith(prefix)));
  }
  function renderEvidence(id,paths=[],prefixes=[]) {
    const target=document.getElementById(id); target.replaceChildren();
    const heading=document.createElement('strong'); heading.textContent='Source evidence'; target.append(heading);
    const items=evidenceItems(paths,prefixes);
    if (!items.length) { const empty=document.createElement('div'); empty.className='no-evidence'; empty.textContent='No field-level evidence was recorded.'; target.append(empty); return; }
    items.forEach(item=>{
      const quote=document.createElement('div'); quote.className='quote'; quote.textContent='“'+item.raw_text+'”';
      const meta=document.createElement('div'); meta.className='meta'; meta.textContent='Page '+item.page+(item.source_section?' · '+item.source_section:'')+' · '+item.confidence+' · '+item.method;
      target.append(quote,meta);
    });
  }
  function markDirty() { if (!selected) return; dirty=true; approve.disabled=true; setMessage('Unsaved corrections. Save before approval.'); }
  function partRow(part={}) {
    const tr=document.createElement('tr'); tr._part=part;
    const fields=[['part_number',''],['raw_description','description'],['quantity',''],['source','']];
    fields.forEach(([name,className])=>{const td=document.createElement('td'),input=document.createElement('input');input.dataset.field=name;input.value=part[name]??'';if(className)input.className=className;if(name==='quantity'){input.type='number';input.min='0.01';input.step='0.01';input.required=true}else if(name==='raw_description')input.required=true;input.addEventListener('input',markDirty);td.append(input);tr.append(td)});
    const td=document.createElement('td'),remove=document.createElement('button');remove.type='button';remove.className='remove';remove.textContent='Remove';remove.onclick=()=>{tr.remove();markDirty()};td.append(remove);tr.append(td);partsBody.append(tr);
  }
  function renderParts(parts) { partsBody.replaceChildren(); parts.forEach(part=>partRow(part)); }
  function collectParts() {
    return [...partsBody.querySelectorAll('tr')].map(row=>{
      const value={...(row._part||{})};
      row.querySelectorAll('input').forEach(input=>{value[input.dataset.field]=input.dataset.field==='quantity'?input.value:input.value.trim()||null});
      value.raw_description=value.raw_description||''; value.quantity=value.quantity||'1'; return value;
    });
  }
  function renderFlags(record) {
    flags.replaceChildren(); record.review_flags.forEach(flag=>{const li=document.createElement('li');li.textContent=(flag.field_path?flag.field_path+': ':'')+flag.message;flags.append(li)});
  }
  const fieldLabels={
    'customer_site.customer_name':'Customer','customer_site.site_name':'Customer','machine.pcsn':'PCSN','machine.asset_id':'PCSN',
    'classification.service_type':'Service type','classification.fault_category':'Fault category','classification.fault_subcategory':'Fault subcategory','timing.reported_downtime_hours':'Reported downtime','classification.raw_subject':'Subject',
    'intervention.raw_closure_summary':'Intervention','intervention.normalized_summary':'Normalized intervention','intervention.activities':'Intervention activities'
  };
  function friendlyField(path) {
    if (fieldLabels[path]) return fieldLabels[path];
    const part=path.match(/^parts\\[(\\d+)\\]\\.(.+)$/);
    if (part) return 'Part '+(Number(part[1])+1)+' · '+part[2].replaceAll('_',' ').replace(/^./,x=>x.toUpperCase());
    return path.replaceAll('_',' ').replaceAll('.',' › ').replace(/^./,x=>x.toUpperCase());
  }
  function displayValue(value) {
    if (value===null||value===undefined||value==='') return '—';
    if (Array.isArray(value)) return value.length?value.map(displayValue).join(', '):'—';
    if (typeof value==='object') return JSON.stringify(value);
    if (typeof value==='boolean') return value?'Yes':'No';
    return String(value).replaceAll('_',' ');
  }
  function renderHistory(entries) {
    history.replaceChildren();
    if (!entries.length) { const empty=document.createElement('div');empty.className='empty-history';empty.textContent='No corrections or approvals have been recorded.';history.append(empty);return; }
    [...entries].reverse().forEach(entry=>{
      const card=document.createElement('section');card.className='history-entry';
      const head=document.createElement('div');head.className='history-head';
      const action=document.createElement('span');action.className='history-action '+entry.action;action.textContent=entry.action;
      const actorName=document.createElement('strong');actorName.textContent=entry.actor;
      const date=document.createElement('span');date.className='history-date';date.textContent=new Date(entry.timestamp).toLocaleString();head.append(action,actorName,date);card.append(head);
      if (entry.note) { const noteText=document.createElement('p');noteText.className='history-note';noteText.textContent=entry.note;card.append(noteText); }
      if (entry.changes.length) {
        const table=document.createElement('table');table.className='change-table';table.innerHTML='<thead><tr><th>Field</th><th>Previous value</th><th>New value</th></tr></thead>';
        const body=document.createElement('tbody');entry.changes.forEach(change=>{const row=document.createElement('tr');[friendlyField(change.field_path),displayValue(change.old_value),displayValue(change.new_value)].forEach((value,index)=>{const cell=document.createElement('td');cell.textContent=value;if(index)cell.className='change-value';row.append(cell)});body.append(row)});table.append(body);card.append(table);
      } else if (entry.action==='approval') { const approved=document.createElement('p');approved.className='history-note';approved.textContent='Event approved for reporting.';card.append(approved); }
      history.append(card);
    });
  }
  function selectRecord(record, button) {
    selected=record; document.querySelectorAll('#events button').forEach(x=>x.classList.remove('selected')); button.classList.add('selected');
    title.textContent=record.event.identification.work_order_number+' — '+(record.archived?'archived':record.approval_status);
    customer.value=record.event.customer_site.customer_name||record.event.customer_site.site_name||'';
    pcsn.value=record.event.machine.pcsn||record.event.machine.asset_id||'';
    serviceType.value=record.event.classification.service_type;
    faultCategory.value=record.event.classification.fault_category||'';
    faultSubcategory.value=record.event.classification.fault_subcategory||'';
    downtime.value=record.event.timing.reported_downtime_hours??'';
    subject.value=record.event.classification.raw_subject||'';
    intervention.value=record.event.intervention.raw_closure_summary||record.event.intervention.normalized_summary||'';
    renderParts(record.event.parts); renderFlags(record);
    renderEvidence('evidence-customer',['customer_site.customer_name','customer_site.site_name']);
    renderEvidence('evidence-pcsn',['machine.pcsn','machine.asset_id']);
    renderEvidence('evidence-service-type',['classification.service_type']);
    renderEvidence('evidence-fault-category',['classification.fault_category','classification.raw_subject'],['diagnosis.','intervention.']);
    renderEvidence('evidence-fault-subcategory',['classification.fault_subcategory','classification.raw_subject'],['diagnosis.','intervention.']);
    renderEvidence('evidence-downtime',['timing.reported_downtime_hours']);
    renderEvidence('evidence-subject',['classification.raw_subject']);
    renderEvidence('evidence-intervention',['intervention.raw_closure_summary','intervention.normalized_summary'],['intervention.activities']);
    renderEvidence('evidence-parts',[],['parts']);
    dirty=false; fieldEditor.hidden=false; fieldEditor.querySelectorAll('input,select,textarea,button').forEach(control=>control.disabled=record.archived);save.disabled=record.archived;approve.disabled=record.archived;archive.hidden=record.archived;archive.disabled=record.archived;restore.hidden=!record.archived;restore.disabled=!record.archived;
    renderHistory(record.correction_history); setMessage('');
  }
  function correctedEvent() {
    if (!fieldEditor.reportValidity()) throw new Error('Correct the highlighted fields before saving.');
    const event=JSON.parse(JSON.stringify(selected.event)),name=customer.value.trim()||null,identity=pcsn.value.trim().toUpperCase()||null;
    event.customer_site.customer_name=name; event.customer_site.site_name=name;
    event.machine.pcsn=identity; event.machine.asset_id=identity;
    event.classification.service_type=serviceType.value;
    event.classification.fault_category=faultCategory.value.trim()||null;
    event.classification.fault_subcategory=faultSubcategory.value.trim()||null;
    event.timing.reported_downtime_hours=downtime.value===''?null:downtime.value;
    event.classification.raw_subject=subject.value.trim()||null;
    const changedIntervention=intervention.value.trim()!==(event.intervention.raw_closure_summary||event.intervention.normalized_summary||'');
    event.intervention.raw_closure_summary=intervention.value.trim()||null;
    if (changedIntervention) { event.intervention.normalized_summary=null; event.intervention.activities=[]; }
    event.parts=collectParts(); return event;
  }
  async function act(kind) {
    if (!selected || !actor.value.trim()) { setMessage('Enter the reviewer name.','error'); return; }
    save.disabled=true; approve.disabled=true; setMessage(kind==='save'?'Saving...':'Approving...');
    try {
      const options={method:kind==='save'?'PUT':'POST',headers:{'Content-Type':'application/json'}};
      if (kind==='save') options.body=JSON.stringify({event:correctedEvent(),corrected_by:actor.value.trim(),note:note.value.trim()||null});
      else options.body=JSON.stringify({approved_by:actor.value.trim(),note:note.value.trim()||null});
      const suffix=kind==='save'?'':'/approve'; await request('/v1/service-events/'+selected.id+suffix,options);
      await refresh(selected.id); setMessage(kind==='save'?'Correction saved; approval is still required.':'Approved and ready for reports.','ok');
    } catch(error) { setMessage(error.message,'error'); save.disabled=false; approve.disabled=dirty; }
  }
  async function archiveAction(kind) {
    if (!selected || !actor.value.trim()) { setMessage('Enter the reviewer name.','error'); return; }
    if (!note.value.trim()) { setMessage('Enter a reason for '+kind+'.','error'); return; }
    archive.disabled=true; restore.disabled=true; setMessage(kind==='archive'?'Archiving...':'Restoring...');
    try {
      await request('/v1/service-events/'+selected.id+'/'+kind,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({actor:actor.value.trim(),reason:note.value.trim()})});
      const selectedId=kind==='restore'?selected.id:null; await refresh(selectedId); setMessage(kind==='archive'?'Event archived and excluded from reports.':'Event restored.','ok');
    } catch(error) { setMessage(error.message,'error'); archive.disabled=selected.archived; restore.disabled=!selected.archived; }
  }
  fieldEditor.addEventListener('input',markDirty);
  fieldEditor.addEventListener('change',markDirty);
  document.getElementById('add-part').onclick=()=>{partRow({quantity:'1'});markDirty()};
  save.onclick=()=>act('save'); approve.onclick=()=>act('approve');archive.onclick=()=>archiveAction('archive');restore.onclick=()=>archiveAction('restore');includeArchived.onchange=()=>refresh().catch(error=>setMessage(error.message,'error'));
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
    :root { color-scheme: light; --navy:#006b6b; --blue:#009999; --orange:#ec6602; --orange-dark:#b94f00; --pale:#e6f5f5; --line:#c9dddd; --text:#273536; --danger:#a52828; --success:#19723b; }
    * { box-sizing:border-box; }
    body { margin:0; background:#f4f7fa; color:var(--text); font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }
    main { width:min(900px,calc(100% - 32px)); margin:48px auto; }
    .card { background:white; border:1px solid var(--line); border-radius:14px; padding:28px; box-shadow:0 10px 30px rgba(0,107,107,.08); border-top:4px solid var(--orange); }
    h1 { margin:0 0 8px; color:var(--navy); font-size:28px; }
    p { margin:0 0 22px; }
    .drop { display:block; padding:28px; border:2px dashed var(--blue); border-radius:12px; background:var(--pale); text-align:center; cursor:pointer; }
    .drop:hover { background:#d8eeee; }
    input[type=file] { position:absolute; width:1px; height:1px; opacity:0; }
    .choose { display:inline-block; padding:10px 16px; border-radius:8px; background:var(--blue); color:white; font-weight:700; }
    #selection { margin:18px 0; padding-left:20px; max-height:180px; overflow:auto; }
    button { border:0; border-radius:8px; padding:11px 18px; background:var(--orange-dark); color:white; font-weight:700; cursor:pointer; }
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
def stored_service_events(
    include_review_required: bool = True, include_archived: bool = False
) -> list[dict]:
    """List persisted extraction records. Use the default view to find records needing review."""
    return list_events(include_review_required, include_archived)


@router.get("/v1/service-events/{event_id}", response_model=StoredServiceEvent)
def stored_service_event(event_id: int) -> dict:
    """Return one saved event, including its review history."""
    record = get_event(event_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Service event not found."
        )
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Service event not found."
        )
    return record


@router.post("/v1/service-events/{event_id}/approve", response_model=StoredServiceEvent)
def approve_stored_service_event(event_id: int, request: EventApprovalRequest) -> dict:
    """Approve a reviewed event so stored reports can include it."""
    try:
        record = approve_event(event_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Service event not found."
        )
    return record


@router.post("/v1/service-events/{event_id}/archive", response_model=StoredServiceEvent)
def archive_stored_service_event(event_id: int, request: EventArchiveRequest) -> dict:
    record = archive_event(event_id, request)
    if not record:
        raise HTTPException(status_code=404, detail="Service event not found.")
    return record


@router.post("/v1/service-events/{event_id}/restore", response_model=StoredServiceEvent)
def restore_stored_service_event(event_id: int, request: EventArchiveRequest) -> dict:
    record = restore_event(event_id, request)
    if not record:
        raise HTTPException(status_code=404, detail="Service event not found.")
    return record


@router.post("/v1/reports/quarterly/stored", response_model=QuarterlyReport)
def stored_quarterly_report(request: StoredQuarterlyReportRequest) -> QuarterlyReport:
    """Build a report from all stored, report-ready service events."""
    return _build_stored_quarterly_report(request)


def _trend_periods(start_date: date, end_date: date, interval: str):
    current = start_date
    while current <= end_date:
        if interval == "quarter":
            first_month = ((current.month - 1) // 3) * 3 + 1
            natural_start = date(current.year, first_month, 1)
            last_month = first_month + 2
            natural_end = date(current.year, last_month, monthrange(current.year, last_month)[1])
        else:
            natural_start = date(current.year, current.month, 1)
            natural_end = date(current.year, current.month, monthrange(current.year, current.month)[1])
        period_start = max(start_date, natural_start)
        period_end = min(end_date, natural_end)
        if interval == "quarter":
            label = f"Q{((first_month - 1) // 3) + 1} {current.year}"
        else:
            label = period_start.strftime("%b %Y")
        yield label, period_start, period_end
        if natural_end.month == 12:
            current = date(natural_end.year + 1, 1, 1)
        else:
            current = date(natural_end.year, natural_end.month + 1, 1)


@router.post("/v1/reports/trends/stored", response_model=TrendReport)
def stored_trend_report(request: StoredTrendReportRequest) -> TrendReport:
    """Build month- or quarter-level trend points using the normal report calculation."""
    points: list[TrendPoint] = []
    for label, period_start, period_end in _trend_periods(
        request.start_date, request.end_date, request.interval
    ):
        report = _build_stored_quarterly_report(
            StoredQuarterlyReportRequest(
                start_date=period_start,
                end_date=period_end,
                report_profile_id=request.report_profile_id,
                site_name=request.site_name,
                pcsn=request.pcsn,
            )
        )
        points.append(
            TrendPoint(
                period=report.period.model_copy(update={"label": label}),
                events=report.events_in_period,
                corrective_events=sum(item.corrective_event_count for item in report.machine_breakdown),
                machine_count=report.machine_count,
                working_hours_basis=report.working_hours_basis,
                unplanned_downtime_hours=report.unplanned_downtime_hours,
                uptime_percent=report.uptime_percent,
            )
        )
    return TrendReport(
        interval=request.interval,
        start_date=request.start_date,
        end_date=request.end_date,
        site_name=request.site_name,
        pcsn=request.pcsn,
        points=points,
    )


@router.get("/v1/machines", response_model=list[RegisteredMachine])
def registered_machines() -> list[RegisteredMachine]:
    return list_registered_machines()


@router.post("/v1/machines", response_model=RegisteredMachine, status_code=201)
def register_machine(request: MachineRegistrationInput) -> RegisteredMachine:
    try:
        return create_registered_machine(request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.put("/v1/machines/{machine_id}", response_model=RegisteredMachine)
def update_machine(machine_id: int, request: MachineRegistrationInput) -> RegisteredMachine:
    try:
        machine = update_registered_machine(machine_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not machine:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Machine not found.")
    return machine


@router.get("/v1/customer-aliases", response_model=list[CustomerAlias])
def customer_aliases() -> list[CustomerAlias]:
    return list_customer_aliases()


@router.post("/v1/customer-aliases", response_model=CustomerAlias, status_code=201)
def add_customer_alias(request: CustomerAliasInput) -> CustomerAlias:
    try:
        return create_customer_alias(request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/v1/customer-aliases/{alias_id}", status_code=204)
def remove_customer_alias(alias_id: int) -> Response:
    if not delete_customer_alias(alias_id):
        raise HTTPException(status_code=404, detail="Customer alias not found.")
    return Response(status_code=204)


@router.get("/v1/product-catalog", response_model=list[ProductCatalogEntry])
def product_catalog() -> list[ProductCatalogEntry]:
    return list_product_catalog()


@router.post("/v1/product-catalog", response_model=ProductCatalogEntry, status_code=201)
def add_product_catalog_entry(request: ProductCatalogInput) -> ProductCatalogEntry:
    try:
        return create_product_catalog_entry(request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/v1/product-catalog/{entry_id}", response_model=ProductCatalogEntry)
def edit_product_catalog_entry(
    entry_id: int, request: ProductCatalogInput
) -> ProductCatalogEntry:
    try:
        entry = update_product_catalog_entry(entry_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not entry:
        raise HTTPException(status_code=404, detail="Product-code entry not found.")
    return entry


@router.delete("/v1/product-catalog/{entry_id}", status_code=204)
def remove_product_catalog_entry(entry_id: int) -> Response:
    if not delete_product_catalog_entry(entry_id):
        raise HTTPException(status_code=404, detail="Product-code entry not found.")
    return Response(status_code=204)


@router.get("/v1/holidays", response_model=list[Holiday])
def holidays(year: int) -> list[Holiday]:
    if year < 2020 or year > 2100:
        raise HTTPException(status_code=422, detail="Year must be between 2020 and 2100.")
    return list_holidays(year)


@router.post("/v1/holidays", response_model=Holiday, status_code=201)
def add_holiday(request: HolidayInput) -> Holiday:
    try:
        return create_holiday(request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/v1/holidays/{holiday_id}", response_model=Holiday)
def edit_holiday(holiday_id: int, request: HolidayInput) -> Holiday:
    try:
        holiday = update_holiday(holiday_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not holiday:
        raise HTTPException(status_code=404, detail="Holiday not found.")
    return holiday


@router.delete("/v1/holidays/{holiday_id}", status_code=204)
def remove_holiday(holiday_id: int) -> Response:
    if not delete_holiday(holiday_id):
        raise HTTPException(status_code=404, detail="Holiday not found.")
    return Response(status_code=204)


@router.get("/v1/report-profiles", response_model=list[ReportProfile])
def report_profiles() -> list[ReportProfile]:
    return list_report_profiles()


@router.post("/v1/report-profiles", response_model=ReportProfile, status_code=201)
def add_report_profile(request: ReportProfileInput) -> ReportProfile:
    try:
        return create_report_profile(request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/v1/report-profiles/{profile_id}", response_model=ReportProfile)
def edit_report_profile(profile_id: int, request: ReportProfileInput) -> ReportProfile:
    try:
        profile = update_report_profile(profile_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not profile:
        raise HTTPException(status_code=404, detail="Report profile not found.")
    return profile


@router.delete("/v1/report-profiles/{profile_id}", status_code=204)
def remove_report_profile(profile_id: int) -> Response:
    try:
        removed = delete_report_profile(profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail="Report profile not found.")
    return Response(status_code=204)


@router.get("/v1/reports/filter-options", response_model=ReportFilterOptions)
def stored_report_filter_options() -> ReportFilterOptions:
    """List sites and PCSNs available for editable report filter controls."""
    aliases = customer_alias_map()
    options = report_filter_options(reportable_events(), aliases)
    machines = list_registered_machines()
    return ReportFilterOptions(
        sites=sorted(
            {
                *options.sites,
                *(canonical_customer_name(item.customer_name, aliases) for item in machines),
            },
            key=str.casefold,
        ),
        pcsns=sorted({*options.pcsns, *(item.pcsn for item in machines)}),
    )


def _build_stored_quarterly_report(request: StoredQuarterlyReportRequest) -> QuarterlyReport:
    events = reportable_events()
    machines = list_registered_machines()
    profile = get_report_profile(request.report_profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Report profile not found.")
    if not events and not machines:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No report-ready stored service events are available.",
        )
    report_request = QuarterlyReportRequest(
            year=request.year,
            quarter=request.quarter,
            start_date=request.start_date,
            end_date=request.end_date,
            working_hours_basis=request.working_hours_basis,
            working_hours_per_machine=request.working_hours_per_machine,
            machine_hours_overrides=request.machine_hours_overrides,
            registered_machines=machines,
            customer_aliases=customer_alias_map(),
            operating_weekdays=profile.working_days,
            daily_operating_hours=profile.daily_hours,
            site_name=request.site_name,
            pcsn=request.pcsn,
            events=events,
        )
    period = report_period(report_request)
    return build_quarterly_report(
        report_request.model_copy(
            update={
                "holiday_dates": holiday_dates_for_range(
                    period.start_date, period.end_date
                ),
                "use_supplied_holiday_calendar": True,
            }
        )
    )


@router.post(
    "/v1/reports/quarterly/stored/pdf",
    responses={200: {"content": {"application/pdf": {}}}},
)
def stored_quarterly_report_pdf(request: StoredQuarterlyReportRequest) -> Response:
    """Download the stored service report as a printable PDF."""
    report = _build_stored_quarterly_report(request)
    content = render_quarterly_report_pdf(report)
    if request.start_date and request.end_date:
        file_name = f"service-report-{request.start_date}-to-{request.end_date}.pdf"
    else:
        file_name = f"service-report-Q{request.quarter}-{request.year}.pdf"
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )


@router.post(
    "/v1/reports/quarterly/stored/xlsx",
    responses={
        200: {
            "content": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}
            }
        }
    },
)
def stored_quarterly_report_excel(request: StoredQuarterlyReportRequest) -> Response:
    """Download the stored report as a formatted Excel workbook."""
    report = _build_stored_quarterly_report(request)
    content = render_quarterly_report_excel(report)
    if request.start_date and request.end_date:
        file_name = f"service-report-{request.start_date}-to-{request.end_date}.xlsx"
    else:
        file_name = f"service-report-Q{request.quarter}-{request.year}.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )
