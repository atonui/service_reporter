# Work-order extraction contract v0.1

Convert one work-order document into exactly one `ServiceEvent` object that conforms to the API's JSON Schema.

## Non-negotiable rules

- Use only information supported by the document.
- Preserve document wording in `raw_` fields.
- Use `null`, empty lists, or an `unknown` enum when information is absent.
- Do not infer root cause from symptoms, repairs, or parts consumption.
- Do not calculate durations or totals. Extract reported values and timestamps; application code performs calculations.
- Add page-level evidence for each important claim. Quote the smallest source fragment that supports the value.
- Do not treat signatures as proof that the technical issue was resolved.
- Return JSON only. Do not add prose or Markdown.

## Classification guidance

- `preventive_maintenance`: scheduled inspection or PMP activity.
- `corrective_breakdown`: work performed to restore or repair a faulted machine.
- `remote_support`: intervention performed without an onsite visit.
- `customer_request`: information or assistance request that is not maintenance or breakdown repair.
- `training`: operator or technical training.
- `other`: supported activity that fits none of the above.
- `unknown`: the source does not establish the service type.

The JSON Schema returned by `GET /v1/service-events/schema` is authoritative.

