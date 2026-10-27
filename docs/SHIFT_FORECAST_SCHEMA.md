# Owner-confirmed shift forecast — protected manual configuration

This is a **manual forecast only**. It does not change `totalCost`, GP, financial source, model context, scheduled report, approved plan, worker or existing price policies. The configuration and all real geography/rule identifiers/rates belong only in owner-protected DPAPI, not Git, chat or plaintext descriptors. The coordinating chat must check the exact owner-confirmed route selector against the already approved vehicle catalog before import; structural validation does not infer business intent.

The input DPAPI decrypts to UTF-8 JSON with these exact keys:

```json
{
  "schema": "supra-shift-forecast-owner-v1",
  "version": "synthetic-v1",
  "owner_id": 123,
  "group_id": -123,
  "booking_pin": {"spreadsheet_id":"SYNTHETIC", "tab":"SYNTHETIC", "gid":0, "range":"'SYNTHETIC'!A1:AK100"},
  "raw_pin": {"spreadsheet_id":"SYNTHETIC", "tab":"raw_current", "gid":1, "range":"'raw_current'!A1:Y100"},
  "period": "2026-01",
  "window": {"start":"2026-01-01", "end":"2026-01-03"},
  "vehicle_catalog_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "handling_catalog_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "route_selector": {"scope":"DISTRICT", "routes":[
    {"rule_id":"SYN1", "province":"SYNTHETIC", "district":"SYNTHETIC A"},
    {"rule_id":"SYN2", "province":"SYNTHETIC", "district":"SYNTHETIC B"}
  ]},
  "vehicle": {"source":"BOOKING_AG", "declared_class_kg":"7000", "class_basis":"OWNER_CONFIRMED_BOOKING_T_AG"},
  "time": {"source":"BOOKING_E", "counting":"DISTINCT_DATE_AG", "shifts_per_vehicle_day":"1", "shift_hours":"10"},
  "rate": {"amount_vnd":"1234567", "currency":"VND", "unit":"PER_SHIFT"},
  "inclusions": {"fuel":"UNKNOWN", "driver_wage":"UNKNOWN", "tolls":"UNKNOWN", "vat":"UNKNOWN"},
  "provenance": {"source":"USER_CONFIRMED_IN_CODEX", "confirmed_date":"2026-01-04"}
}
```

The example above is wholly synthetic and must not be imported as a real business configuration. Rates, class, dates and route IDs in the actual protected configuration must be built from the user's confirmation and approved catalog, never from this example. Exactly two owner-confirmed district rules are selected; each must match the pinned vehicle catalog's rule ID, normalized province/district, full effective interval and `TRANSFER_GXT` mode. A store override or unresolved/multi-vehicle route is not silently assigned to these rules. Both source pins include the exact range and must match the active paired snapshot. The two catalog hashes bind the forecast to the same approved routing and handling terms. Owner/group must match both the local owner and paired group.

The only supported counting protocol is `DISTINCT_DATE_AG` from Booking E/AG, using the configured positive shifts-per-vehicle-day and shift hours. Numbers/rate live only in DPAPI. `vehicle.class_basis` is **required** and accepts only `OWNER_CONFIRMED_BOOKING_T_AG`: the owner confirmed T is the capacity of AG. Missing/invalid/mixed T values for a vehicle/day are held; clearly different classes are excluded. T—not the older fixed class in the routing catalog—determines whether the configured vehicle rate can apply. The example's basis is synthetic only. Raw S never determines capacity or allocates a share of the shift price.

The forecast counts a vehicle/day once even when there are multiple DO/GHN lines. It is a Booking-based proxy, not an actual dispatch/shift, and applies only inside the configuration window. The price is the complete vehicle pool, not a Supra-only expense. Package inclusions/VAT remain unknown until separately confirmed. Missing/conflicting evidence is held separately, and neither the pool forecast nor the handling subtotal is added to financial total cost or GP.

Other routes or multiple bookings on the same vehicle/day produce a warning and do not multiply the assumed shifts. Conflicting route or vehicle evidence for the same order remains held. Potentially relevant rows without a usable Booking E date remain unscoped; they prevent treating the pool subtotal as complete.

Import contract: `python -m ops_team.telegram_vehicle_forecast --minimized <absolute-approved-dpapi>` with JSON descriptor `{"schema":"supra-shift-forecast-owner-v1","sha256":"<sha256 of exact decrypted JSON bytes>"}` on stdin. It checks the current paired owner/group, source pins, catalog hashes, scope, selector, units and descriptor, then atomically saves a protected manual forecast configuration. It does not alter plan/schedule/model/worker or send Telegram. A mismatch or cancellation leaves the previous configuration untouched. The SHA of the exact protected plaintext is included in the manual forecast record, along with Booking and catalog hashes; changing or invalidating any of these cannot reuse an old forecast. No forecast is put in the model or scheduled report.

## Multi-tariff v2

`supra-shift-forecast-owner-v2` retains the same top-level identity, pins, period, window, catalogs, route selector and provenance as v1. Replace v1's `vehicle`, `time`, `rate` and `inclusions` with the following fully synthetic fields:

```json
"vehicle": {"source":"BOOKING_AG", "class_basis":"OWNER_CONFIRMED_BOOKING_T_AG"},
"time": {"source":"BOOKING_E", "counting":"DISTINCT_DATE_AG"},
"tariffs": [
  {"declared_class_kg":"7000", "units_per_vehicle_day":"1", "shift_hours":"10",
   "rate":{"amount_vnd":"1234567", "currency":"VND", "unit":"PER_SHIFT"},
   "inclusions":{"fuel":"INCLUDED", "driver_wage":"INCLUDED", "tolls":"INCLUDED", "vat":"EXCLUDED", "dc_handling":"EXCLUDED"}},
  {"declared_class_kg":"5000", "units_per_vehicle_day":"1", "shift_hours":null,
   "rate":{"amount_vnd":"876543", "currency":"VND", "unit":"PER_TRIP"},
   "inclusions":{"fuel":"UNKNOWN", "driver_wage":"UNKNOWN", "tolls":"UNKNOWN", "vat":"UNKNOWN", "dc_handling":"UNKNOWN"}}
]
```

Each positive integer capacity in kg must occur once. Rates are positive integer VND per shift or trip; shift hours are mandatory for `PER_SHIFT` and null for `PER_TRIP`. `units_per_vehicle_day` is an explicit owner-approved proxy, not evidence of actual shifts or trips. Every inclusion value is `INCLUDED`, `EXCLUDED`, or `UNKNOWN`; no kilometer overage or other surcharge is inferred. The source is grouped once per date/AG, then matched by T to one tariff. A missing or mixed capacity, ambiguous route or conflicting order is held once, not once per tariff. Unmatched classes remain outside the subtotal. The result remains a full shared-pool base estimate; VAT, handling and additions follow each tariff's explicit terms and no estimate is added to financial totals or allocated to Supra.

To stage v2, construct the full exact JSON with the authorized owner-confirmed values in a DPAPI file, then pass the exact plaintext SHA-256 in a stdin descriptor using `"schema":"supra-shift-forecast-owner-v2"`. The descriptor schema must equal the decrypted JSON schema. The same CLI and atomic validation apply. The existing v1 format remains accepted and existing protected v1 configurations continue to calculate as before.
