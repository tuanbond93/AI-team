# Protected source-bound owner rules

This optional manual layer is inactive unless independently reviewed and imported. It binds the exact current paired Booking and Raw digests, vehicle and handling catalog hashes, manual cost policy, multi-tariff forecast and revenue overlay plaintext hashes. It does not change source data, financial totals/GP, approved plan, model, schedule or provider calls. Absence preserves the existing behavior; an installed but invalid or stale layer fails closed. The import requires the currently paired owner/group and validates all bound objects before atomically saving protected data.

Exact JSON structure with entirely fictional identifiers, data and dates:

```json
{
  "schema":"supra-owner-rule-exceptions-v1",
  "base_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "forecast_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "overlay_sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
  "booking_digest":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
  "raw_digest":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
  "vehicle_catalog_sha256":"ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
  "handling_catalog_sha256":"1111111111111111111111111111111111111111111111111111111111111111",
  "window":{"start":"2026-01-01","end":"2026-01-31"},
  "direct_gxt_two_legs":true,
  "booked_class_basis":"BOOKED_TARIFF_CLASS",
  "handling_parts":[{"order_id":"SYN_ORDER","day":"2026-01-03","sources":["SYN_BOOK_ROW_1","SYN_BOOK_ROW_2"]}],
  "booked_trips":[{"day":"2026-01-04","vehicle":"99A12345","sources":["SYN_BOOK_ROW_3","SYN_BOOK_ROW_4"],
    "trips":[{"class_kg":"7000","units":1},{"class_kg":"5000","units":1}]}],
  "opening_ftl":[{"order_id":"SYN_OPEN","day":"2026-01-05","booking_sources":["SYN_BOOK_ROW_5"],
    "raw_sources":["SYN_RAW_ROW_1"],"class_kg":"7000","pricing":"ACTUAL_KM_UNPRICED"}],
  "provenance":{"source":"USER_CONFIRMED_IN_CODEX","confirmed_date":"2026-01-06",
    "evidence_sha256":"2222222222222222222222222222222222222222222222222222222222222222"}
}
```

All lists must be present; they may be empty. Replace every synthetic value from the verified paired snapshot and the owner's protected confirmation. Every handling-parts override is scoped to one exact GHN/day and the exact set of its nonblank source rows; invalid rows or a different set reject import. It sums only the specific confirmed distinct parts, not every DO or other GHN. Booked-trips overrides use one exact date/AG, all selected upstream-route rows for that key and exactly two distinct confirmed classes with one unit each. Each class must appear in those rows and be priced by the existing route forecast. No other mixed-class group is split, maximized or silently inferred. The opening exception binds one exact GHN/Booking day, all corresponding Booking rows, class and the exact matching Raw DELIVER row references; the booked vehicle must be unambiguous and the original owner-approved rule must be in the BC selector. The system does not use Raw for that opening's FTL price or assume a rate/distance.

DC handling is independently recalculated at GHN plus Booking E day, with one L total per event, rate by E, missing L not borrowed from another day and original legacy warnings retained for audit. DC package overlap is checked per event using accepted package references. For eligible GXT direct mismatches, two-leg treatment requires one unambiguous Booking date/AG and appropriate GXT route; no plate or AG share is inferred. Unknown or mixed groups remain held. An opening Raw event on a shared S/day does not change source statistics; the financial group stays held if attribution remains mixed. Other unrelated mixed BC/GXT groups remain held pending confirmation. Price for the opening's actual km and other unknown charges remain N/A.

The DC and GXT last-mile calculations use the same two-leg evidence resolver. It requires a unique Booking date and AG, a unique physical Raw S/day at or after the Booking date, usable source-group evidence, and compatible GXT selectors for matched orders in that S/day. A verified different S/AG makes DC handling eligible under the transfer terms; a verified same vehicle retains the original direct handling treatment. Missing, mixed or ambiguous linkage is held rather than assumed direct with zero DC. The AG upstream amount remains N/A unless a separately approved route price supports it; no S share is copied to AG.

Stage the JSON as a DPAPI file, reviewed by the owner/coordinating chat, with no business identifiers, values or secrets in Git. Import using `python -m ops_team.telegram_rule_layer --minimized <absolute-approved-dpapi>` and stdin `{"schema":"supra-owner-rule-exceptions-v1","sha256":"<SHA256 of exact decrypted JSON bytes>"}`. A mismatch, missing prerequisite, or cancellation leaves the prior layer unchanged. The coordinating chat must independently check the exact business exceptions, forecast class tariffs and source evidence, compare all ranges, and then activate; this CLI does not restart a worker or send Telegram.
