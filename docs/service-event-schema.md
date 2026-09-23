# ServiceEvent schema v0.1

`ServiceEvent` is the auditable boundary between a work-order document and all later analytics.

## Rules

1. Preserve the source wording in fields prefixed with `raw_`.
2. Store classifications and cleaned wording separately as normalized values.
3. Use `null` or `unknown` when the document does not support a value. Never infer a root cause merely from a replaced part.
4. Every important extracted claim should have one `evidence` record containing the page and exact source text.
5. The extraction model does not calculate downtime, uptime, elapsed time, or totals. Application code calculates them from extracted facts.
6. Unknown JSON properties are rejected so changes to the extraction output cannot pass unnoticed.

## Time semantics

Work-order timestamps are stored as written, with the document's IANA timezone stored in `timing.timezone`. Offset-aware signature timestamps retain their explicit UTC offset. Downtime is calculated from `malfunction_start` to `machine_release`; onsite elapsed time is calculated from `time_in` to `time_out`.

## Missing and ambiguous information

The sample document does not explicitly state a root cause or a resolution sentence. Those fields remain `null`. The presence of replacement parts does not, by itself, prove which component caused the fault.

## Scope

Version 0.1 validates one corrective work-order fixture. PDF ingestion, OCR, model-based extraction, persistence, and a review UI are later milestones.

