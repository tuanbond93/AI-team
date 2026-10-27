# Paired vehicle staging v4

Import constants `PAIR_SCHEMA`, `BOOK_HEADERS`, `RAW_HEADERS`, `RAW_PIN`, `VEHICLE_POLICY`, `digest` from `ops_team.supra.booking_vehicles`.

DPAPI payload exact keys:

`schema, booking, data_supra, confirmed_business_rules, confirmed_weight_rules, confirmed_handling_policy, handling_catalog, handling_catalog_sha256, vehicle_policy, vehicle_catalog, vehicle_catalog_sha256`.

`schema=PAIR_SCHEMA`. Each source object exact keys:

`source_url, spreadsheet_id, tab, gid, range, retrieved_at, raw_sha256, raw_column_count, headers, rows`.

Primary headers=`BOOK_HEADERS` (13 business columns A/B/C/E/F/G/H/J/L/N/O/T/AG); raw headers=`RAW_HEADERS` (18 business fields). Each row `{sheet_row, values}`. Original column counts 37 and25 respectively. Raw range `'raw_current'!A1:Y100000` (explicit bounded range); primary range matches existing facts. Both capture times are real, not replaced by the other source time. Do not include driver/phone/customer/store names or legacy plate-role candidates.

Rules for J/L and handling retain staging-v3 shapes. Vehicle catalog exact keys:

`schema, version, source_pins, period, provenance, confirmed_date, representative_plates, transfer_class_kg, routing, evidence_hashes`.

Catalog schema=`supra-vehicle-catalog-v1`; source_pins=`{booking:{spreadsheet_id,tab,gid},data_supra:{spreadsheet_id,tab,gid}}`. Other fields follow approved handling catalog conventions (period YYYY-MM, provenance USER_CONFIRMED_IN_CODEX, confirmed_date, evidence_hashes list file_id/sha256). transfer_class_kg is a positive integer decimal string from owner semantics. representative_plates holds the approved representative identifier ONLY inside DPAPI. Each routing row exact keys:

`rule_id, scope, province, district, store_id, mode, handling_eligible, from, to, evidence_ref`.

scope DISTRICT/STORE; mode `TRANSFER_GXT`, `TRANSFER_REP_LASTMILE`, `GXT_DIRECT`, `NCC_DIRECT`, `LAI_CHAU_DIRECT`. Dates inclusive within period. `handling_eligible` preserves existing approved handling condition independently (not inferred from vehicle mode); TP Lai Châu remains eligible. No real routing or plate IDs in Git or plaintext descriptors.

Descriptor exact keys:

`schema, booking, data_supra, confirmed_business_rules, confirmed_weight_rules, confirmed_handling_policy, handling_catalog_sha256, handling_catalog_version, vehicle_policy, vehicle_catalog_sha256, vehicle_catalog_version, minimized_plaintext_sha256`.

The descriptor's two source objects contain all payload source keys EXCEPT rows, plus row_count. No catalogs/representative IDs. Both catalogs use digest() canonical sorted compact UTF-8 JSON. Package minimized_plaintext_sha256 hashes the exact JSON bytes encrypted. API `ingest_vehicle_pair(path,descriptor,state,owner_id,cancel=None)`; existing CLI `--minimized` detects v4. No automatic source/plan approval or schedule change. A paired-source plan identity is added, so parent must explicitly verify/approve the new plan after QA before scheduling; refreshing same two pins/catalogs does not change that identity.

Full37 primary refresh retains the previous paired raw capture and catalog with their original timestamp/digest, never pretends to refresh raw. Parent should export both sources then invoke the paired CLI for 13h automation. Migration is atomic at pointer commit; legacy v1/v2/v3 stays readable, vehicle N/A without pair.

## Daily paired producer and CLI

Export both pinned tabs separately, with full header validation before projecting. Booking has37 columns; select the13 `BOOK_HEADERS` business fields A/B/C/E/F/G/H/J/L/N/O/T/AG. Raw has25 columns; select the18 `RAW_HEADERS` fields by exact header, not by guessed numeric positions. Preserve each physical `sheet_row`, scalar value and original capture timestamp, hash the original export bytes in `raw_sha256`, and build each source object above. Do not replace either capture time with import time. Catalog hashes and versions come from the previously approved protected snapshot; retain its handling catalog, vehicle catalog and policies unchanged. Never rebuild them from observed plates or store names. For direct parser QA, `parse_booking(..., vehicle_fields=True)` explicitly permits T/AG projection; default legacy parsing still removes vehicle fields.

Build payload and descriptor in RAM using these constants, encrypt payload with `save_protected()` and pass descriptor JSON via stdin. The existing CLI supports v4 through the same entry point:

```python
# Producer already holds descriptor and an absolute DPAPI staging path.
subprocess.run(
    [python_exe, '-m', 'ops_team.telegram_snapshot',
     '--minimized', str(staging_path), '--send-scheduled'],
    input=json.dumps(descriptor, ensure_ascii=False).encode('utf-8'), check=True,
)
```

Omit `--send-scheduled` for import-only QA. The CLI loads existing local owner configuration; no credential comes from stdin or the staging. Approval, paired group, worker permission, due time, both pinned captures and the approved plan remain independent gates. **Both Booking and Raw must have been captured within15 minutes before enabling/sending a paired scheduled report.** A full37 Booking-only refresh retains Raw's age and cannot satisfy this gate. Failure waits without claiming/sending the daily job, so a genuinely fresh pair can still run once. The first v4 import changes the plan identity to include the secondary pin/policy/catalog; do not automatically approve it. Normal refreshes under that same identity preserve the plan hash.

## Evidence and presentation

District/store rules choose the physical mode before considering vehicle fields. Handling eligibility remains a separate approved catalog condition, including the direct Lai Châu exception. Representative identifiers stay protected and never count as physical inventory. Normal transfer separates AG's declared upstream class from observed lastmile S. Direct GXT requires normalized S=AG; mismatches and multiple AGs remain unresolved evidence, never a first/last winner.

Sharing uses only raw `type=DELIVER`, `load_date`, normalized real S and exact all-CS GHN membership; `client_type` is independent. Convert source grams to kg with Decimal. Any duplicate day/trip/order/S/DELIVER key holds the group's ratio, regardless of whether duplicate weights agree. Missing order/trip/weight in an identifiable group holds that ratio; missing date/S remains unassigned quality evidence. Zero denominator is N/A. Source-record ratios are neither verified client identity/completion nor a financial allocator. Never copy a lastmile share to AG or infer100% from a direct mode. J/L and handling keep their original grains.

`/xe` or `/vehicles [YYYY-MM-DD] [YYYY-MM-DD] [Vn]` defaults to the period of available data within the selected month/cutoff. One date selects one day; two dates select an inclusive range, optionally followed by one temporary vehicle alias. Dates outside the selected month/cutoff, reversed ranges and malformed filters are rejected. The main reply labels the selected range and sums Decimal kg over its eligible S/day groups. Its percentage is total Supra kg divided by total Supra plus other kg across **all** eligible source groups, including groups with no recorded Supra; it is never an average of individual ratios or a fleet utilization rate. Groups with unresolved evidence are held out. The result is source-record composition, not verified physical deliveries, a unique count of goods, AG/NCC share or financial allocation. The all-source aggregate is not a percentage for a fleet known to carry Supra; the separate subset described below has a different denominator.

For each request the responder counts missing/invalid S and representative plates from the authorized Raw rows in RAM, filtered by the same date range and source/catalog hashes; records with missing dates remain unassigned. These aggregate quality counters are captured with the existing per-user/topic trace, not inferred from month-wide counts. Missing-S rows cannot be assigned to a selected alias. `/details` also shows the separately labeled subset of S/day groups with recorded Supra and its own weighted ratio; that subset does not replace the all-source result. Updating later days is optional and never blocks analysis of the existing range. Dates/numbers use Vietnamese display conventions. An old capture gets a short saved-data notice; technical freshness limits remain in the audit view and existing sending guards.

Vehicle `/details` is owner-only and begins with the same selected-range analysis and its scoped unassigned/held items. It identifies the temporary aliases and explains how to inspect a day or `/details audit`. If the owner later chooses to extend coverage, the checklist retains the approved raw tab link, requirement for all25 original columns and both Supra/other goods, plus the supported handoff to this Codex conversation for source/capture checks before paired import. A Viewer asks the data exporter for an update and does not edit the company source. There is no Telegram-only vehicle importer, and general `/import` must not replace the primary source for this task. A new file is not assumed to contain new business days. Actual cost evidence is a later step, not another request for already-approved rules.

At most two cell examples may link Raw columnS. They require the captured approved secondary pin, a matching complete raw digest/reference prefix, a valid physical source row and a fresh capture. References are to the captured export; even fresh sheets may move rows, so the same trip must be checked before editing anything. Old captures omit row links and ask for a new export first. Missing/untrusted row metadata falls back to the correct tab/column; Booking AG row numbers are never invented from inventory. URLs are full standalone lines because Telegram messages use plain text. No raw plate/order identifiers are printed.

Vehicle `/details audit` remains owner-only and shows at most10 groups plus bounded row-reference samples, unresolved group flags, complete counters and machine codes/hashes. It also replays the exact captured main response, without a model call, source reload or state change. Other `/details` routes retain their existing behavior. Full captured graph stays in RAM and protected business sources; bounded display does not resolve hidden rows. Aliases belong to that source version. Fleet model context contains only aggregate metrics, both capture identities and limitations, with fixed source-coverage/capture notices in the renderer. No raw order/vehicle/store/routing catalog goes to the provider. Source/model caches bind both complete source digests, catalog hash, month and cutoff. Free Pilot retains1500 output tokens,7500 tokens/minute and existing daily caps/429 recovery; code views and Booking intake use0 HTTP.

Synthetic presentation samples: [SYNTHETIC_VEHICLE_REPLIES.md](SYNTHETIC_VEHICLE_REPLIES.md). They contain no business rows or real vehicle/order identifiers.

The manual vehicle view also reports scoped Booking E/L handling eligibility, preserving incomplete or cross-boundary GHNs for review. A separately configured, owner-confirmed vehicle shift forecast is a shared-pool estimate, not actual expense or a Supra allocation. Its protected import and binding contract are described in [SHIFT_FORECAST_SCHEMA.md](SHIFT_FORECAST_SCHEMA.md). Neither estimate changes financial total cost, GP, the model context, the approved plan or scheduled reports. Manual details and audit retain the separate amounts and evidence.

Booking's primary response is a short PARTIAL/UNVERIFIED intake with J, whole/eligible kg, DC fee component/subtotal, open checks and one next step. The complete deterministic report/version/rules remain under `/details`; financial-source reporting keeps its existing four-section layout. Runtime import, plan approval, worker reload and schedule activation are separate deployment actions, never effects of running tests.
