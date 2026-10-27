# Protected manual revenue and allocation overlay

The optional `supra-manual-revenue-allocation-v1` overlay is protected separately from the existing manual cost policy. Absence preserves the prior report. If it exists but fails validation, the report does not use its allocations or revenue. It does not change financial total cost, GP, schedules, plan, source, model context or the upstream vehicle evidence.

Full JSON shape, with fictional examples only:

```json
{
  "schema":"supra-manual-revenue-allocation-v1",
  "base_policy_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "forecast_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "upstream_ratio":"1", "warehouse_ratio":"1",
  "revenue":{"unit":"VND_PER_PRODUCT_UNIT", "vat":"EXCLUDED", "price_source_sha256":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd", "prices":[
    {"province":"Synthetic A","from":"2026-01-01","to":"2026-01-31","vnd_per_unit":"2"},
    {"province":"Synthetic B","from":"2026-01-01","to":"2026-01-31","vnd_per_unit":"3"},
    {"province":"Synthetic C","from":"2026-01-01","to":"2026-01-31","vnd_per_unit":"4"},
    {"province":"Synthetic D","from":"2026-01-01","to":"2026-01-31","vnd_per_unit":"5"}
  ]},
  "provenance":{"source":"USER_CONFIRMED_IN_CODEX", "confirmed_date":"2026-01-31", "evidence_sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"}
}
```

These are not business prices, provinces, dates or evidence. A reviewer must construct the actual DPAPI content from the owner's exact approvals and verify all four prices, effective windows, normalized province names, ratios and both evidence hashes. `price_source_sha256` identifies the reviewed price source; `provenance.evidence_sha256` identifies the owner confirmation used for allocations and VAT. Ratios are decimals between 0 and 1 and apply only to the existing protected upstream forecast's explicitly selected route/window and the warehouse labor pool. They are owner accounting allocations, not proof of physical cargo composition. Overlapping price windows for a normalized province are rejected.

First import the approved base manual cost policy and its exact multi-tariff forecast. Then import the overlay using `python -m ops_team.telegram_cost_overlay --minimized <absolute-approved-dpapi>` with `{"schema":"supra-manual-revenue-allocation-v1","sha256":"<SHA256 of exact decrypted JSON bytes>"}` on stdin. It verifies owner/group, both source pins and catalog identities via the base policy, the exact base and forecast plaintext hashes, window, rates, unit, VAT and provenance before atomically saving. Cancellation or mismatch leaves the previous overlay intact. No secrets or real policy values belong in Git or a plaintext descriptor.

Revenue uses the approved `quantity_summary` from Booking J, summing eligible GHN/SO/DO lines in the selected E range. Distinct GHNs sharing SO/DO remain a warning rather than automatic exclusion. Each eligible line requires one valid province/date price; missing or ambiguous prices and quarantined quantities remain outside the eligible subtotal, so complete revenue is N/A. The report adds only proven component subtotals once, using DC incremental after package overlap. Missing component amounts are not set to zero. The displayed difference is revenue less known costs, not final profit. The audit keeps policy and source hashes, ratios, source-quality warnings and the exact previous response; pricing is a forecast, not settlement or delivery confirmation.

The GXT missing-plate count is derived from Raw DELIVER rows with missing S, using exact ALL-pinned-CS membership and a consistent owner-approved GXT route on the Raw date. It is a subset of source-wide missing S; no plate is inferred from AG or trip. Unresolved and unpriced amounts, VAT, warehouse rent and incidentals remain outside financial totals.

## Owner scope exclusions (v2)

`supra-manual-revenue-allocation-v2` has the exact v1 fields and adds a protected `scope_exclusions` object. Example, entirely synthetic:

```json
{
  "categories":["WAREHOUSE_RENT","ELECTRICITY_WATER","VEHICLE_WAIT","OTHER_OUT_OF_PACKAGE"],
  "window":{"start":"2026-01-01","end":"2026-01-03"},
  "source":"USER_CONFIRMED_IN_CODEX", "confirmed_date":"2026-01-04",
  "evidence_sha256":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
}
```

Only the four listed categories are supported, without duplicates; remote NCC contract kilometers are not an excludable category. The window must lie within the bound forecast window. Exclusions are shown only for a query fully inside their confirmed window and mean excluded from this estimate, not zero actual spending. Owner provenance and exact proof hash are retained in audit. Use the same CLI as above with descriptor schema `supra-manual-revenue-allocation-v2`; the descriptor and decrypted schema must match. Existing v1 protected overlays remain accepted. Neither schema changes component arithmetic or financial completeness.

The manual cost detail also distinguishes unresolved DC source GHNs that are already covered by an accepted delivery package from those still needing review for incremental DC cost. Coverage requires the actual accepted package source references and consistent route; a route label alone is insufficient. Original unresolved evidence, fee subtotals and financial total/GP remain unchanged.
