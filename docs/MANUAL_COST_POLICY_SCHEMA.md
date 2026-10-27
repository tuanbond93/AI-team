# Protected manual cost policy

`supra-manual-cost-policy-v1` is an owner-controlled, manually invoked report. It does not update financial total cost, profit, model context, schedules, plans or source data. The staging producer and reviewer must check the selected business routes, rates, effective window and the wage conversion against the owner's confirmations and the original source. Structural checks cannot establish business intent or prove that transcribed wage constants match the source. Do not put business policy values, route identifiers, source data, or owner identifiers in Git.

The protected UTF-8 JSON has exactly these top-level keys:

```json
{
  "schema":"supra-manual-cost-policy-v1",
  "forecast_binding": "THE COMPLETE APPROVED MULTI-TARIFF V2 OBJECT, not this string",
  "forecast_sha256":"SHA256_OF_EXACT_DECRYPTED_MULTI_TARIFF_BYTES",
  "selectors":{"point_bc":["SYN_BC"],"point_lc":["SYN_LC"],"ftl":["SYN_FTL"],"gxt":["SYN_GXT"]},
  "rates":{"point_bc_vnd":"10","point_lc_vnd":"20","ftl_vnd":"30","ftl_capacity_kg":"100",
           "gxt_vehicle_day_vnd":"40","warehouse_labor_day_vnd":"50"},
  "ftl_ceil_confirmed":true,
  "package_selectors_complete":true,
  "wage":{"source_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
          "extraction_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
          "unit":"VND_PER_GHN_BY_KG",
          "tiers":[
            {"upper_bound":"100","inclusive":true,"formula":{"kind":"FIXED","amount_vnd":"5"}},
            {"upper_bound":"200","inclusive":false,"formula":{"kind":"STEP","base_vnd":"6","offset_kg":"100","step_kg":"10","increment_vnd":"1","min_steps":0}},
            {"upper_bound":null,"inclusive":false,"formula":{"kind":"STEP","base_vnd":"8","offset_kg":"200","step_kg":"20","increment_vnd":"2","min_steps":null}}
          ]},
  "provenance":"USER_CONFIRMED_IN_CODEX"
}
```

This skeleton and all values/IDs are fictional. Substitute the actual multi-tariff object and its exact plaintext digest, checked selectors and rates; do not use the skeleton as business data. Selectors are disjoint approved vehicle catalog rule IDs, evaluated using store precedence and route evidence. BC pricing can select either `TRANSFER_GXT` or `TRANSFER_REP_LASTMILE`; the fee selector does not alter physical mode. LC is `LAI_CHAU_DIRECT`, FTL `NCC_DIRECT` and GXT `TRANSFER_GXT` or `GXT_DIRECT`. Selectors must cover the window. Unselected routes are reported separately rather than assumed free. Set `package_selectors_complete` true only after verifying the selectors include every route that carries a confirmed DC-inclusive package; otherwise unknown overlap stays N/A.

Wage tiers are ordered by strictly increasing upper bound; the last is unbounded. `inclusive` distinguishes `<=` from `<` exactly, including the boundary around 200. The formula returns one wage amount per GHN based on its kg; do not multiply that result by kg again. A step formula returns `base + max(min_steps, int((weight-offset)//step))*increment`, omitting `max` when `min_steps` is null. Zero weight returns zero. Apply to each eligible GHN's raw kg once within its physical vehicle/day. The staged tier data must come from static, verified examination of the exact owner-selected source and extraction hashes; this software does not execute/evaluate the source or attest that a transcription is correct.

Import the separately approved multi-tariff configuration first, then call `python -m ops_team.telegram_manual_cost_policy --minimized <absolute-approved-dpapi>` with `{"schema":"supra-manual-cost-policy-v1","sha256":"<SHA256 of exact decrypted JSON bytes>"}` on stdin. Import checks owner/group, both source pins, both catalog hashes, forecast bytes and identity, window, route modes, uniqueness and policy structure before an atomic protected save; failure or cancellation leaves prior policy untouched. Use temporary vaults for QA, not the production vault. Parent reviews exact selectors, confirmed cost terms, the verified wage transformation and scope before activation.

`/chiphi` and `/costs` use Booking E/H for distinct points; Booking E/AG plus T and L once per GHN for the configured FTL class; Raw load_date/S/DELIVER for eligible GXT sharing; and all calendar days for the GXT warehouse labor pool. FTL ceiling is an owner-approved forecast, not actual trips, and is only enabled by the explicit boolean. Unpriced/mixed classes, invalid or partial groups stay held. Only proven handling overlap is subtracted; unresolved overlap remains N/A. Shared upstream and warehouse amounts are not Supra allocation. `/details audit` replays the captured answer and source/policy hashes without another source read or model call.

For FTL, blank L on a DO is allowed when another row of the same GHN has one valid total; all blank, invalid nonblank or conflicting totals remain held. T is checked across the same E/AG, including other routes, so conflicting classes cannot select a cheaper tariff. A multi-AG upstream warning for `TRANSFER_GXT` does not by itself invalidate the separate physical S last-mile group; direct GXT mismatches still remain held. Details derive unpriced NCC districts from the pinned catalog and show missing plate and direct-mismatch counts; no real districts or prices are hardcoded.

## Multiple FTL tariffs and remote NCC base (v2)

`supra-manual-cost-policy-v2` retains all exact v1 fields and adds `ftl_tariffs`, `remote_km_status`, `selectors.point_remote`, and `rates.point_remote_vnd`. Selectors remain disjoint and must match the approved catalog and window. Remote selectors accept `LAI_CHAU_DIRECT` or `NCC_DIRECT`; the base is counted once per E/H. `remote_km_status` must be `UNAVAILABLE`: the km charge and full remote component remain N/A even when the base is known. Base is added once to the eligible known-cost subtotal, with package DC overlap checked separately.

Each FTL tariff has exact shape `{"class_kg":"100", "rate_vnd":"30", "method":"CEIL_WEIGHT"}` or `{"class_kg":"200", "rate_vnd":"40", "method":"ONE_IF_WITHIN_CAPACITY"}`. Values are fictional. Class values are unique; `ONE_IF_WITHIN_CAPACITY` holds excess weight, and `CEIL_WEIGHT` is allowed only when explicitly owner-confirmed for that class. The existing v1 capacity/rate/ceil fields must match the corresponding tariff. All evidence, source/pin, owner/group and forecast binding checks remain unchanged. v1 remains accepted without new fields. Import uses the same CLI and stdin descriptor as above, with `"schema":"supra-manual-cost-policy-v2"`; descriptor and decrypted schema must match. Since the revenue overlay binds the exact base policy hash, it must be separately reissued and reviewed against the new policy hash before import. No additional kilometer fee is assumed.
