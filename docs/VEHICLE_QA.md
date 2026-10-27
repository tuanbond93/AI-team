# Vehicle paired source — source freeze QA

2026-10-26: full suite **516 passed in77.34s**, shared Windows venv, isolated temporary vaults, HTTP mocks/fake clocks and `-p no:cacheprovider`. Baseline483 tests retained;33 synthetic vehicle tests added. Booking intake assertions were intentionally moved from the short main message to the complete `/details` report; canonical financial presentation and its checks remain covered.

Final trace hardening was followed by **74 passed in15.92s** across vehicle, Booking Telegram and Chief recovery tests. Vehicle trace records share the100-conversation bound, and a failed new vehicle request cannot surface an older answer's detail. A separate label assertion also passed after Vietnamese role/quality wording was finalized.

Coverage includes all five owner modes, representative exclusion, direct GXT mismatch, multiple AGs, direct vehicle mode with handling eligibility, all-CS rather than monthly membership, DELIVER filtering independent of B2B/B2C, Decimal grams conversion, duplicate keys with equal/conflicting weights, missing fields, zero denominator, source-coverage gaps and unchanged J/L grain. Paired schema/pin/catalog/hash/capture guards, atomic rejection/cancellation, protected restart, legacy N/A, primary-only refresh retaining Raw's original age, plan identity, both-source freshness before scheduled claim/send, source/catalog/cutoff cache invalidation and owner-only details are tested.

Fleet uses the real provider adapter with mocked HTTP: four consecutive turns retain at most three history excerpts,1500 output tokens and the existing7500-token minute reservation. Payloads contain only aggregate metrics plus complete source/capture identities and limitations. Tests assert that raw vehicle/order/store/routing identifiers do not enter model payloads, and the renderer adds fixed capture/coverage notices independently of the model answer. Code commands and Booking intake remain0 HTTP. Existing daily-budget,429 recovery and Auditor tests pass unchanged.

The authorized paired business staging was also imported/reloaded in a separate temporary vault. Source/projection counts matched the descriptor, deterministic vehicle groups matched the known control counts, J/L/handling figures stayed unchanged at the same cutoff, and both real source capture timestamps were preserved. Main Booking and vehicle views stayed bounded; identifier-leak assertions passed. This local QA did not send real provider/Telegram HTTP or alter production state, source files, catalogs, plan, schedule, worker, UI or automation. Independent deployment review and activation belong to the coordinating chat.

Recheck with the shared venv activated:

```powershell
python -m pytest -q -p no:cacheprovider --basetemp=.test-tmp-vehicle-review
```

Daily producer/CLI contract: [VEHICLE_PAIR_SCHEMA.md](VEHICLE_PAIR_SCHEMA.md). Protected staging and actual business rows are excluded from Git.

## Manager-facing reply revision

Presentation-only follow-up: **77 passed in10.36s** across vehicle evidence, Telegram Booking and Chief recovery tests on the final wording. Three added cases exercise the short main reply versus complete details, same-day eligible Decimal subtotals with held/other-day rows excluded, selected temporary vehicle and unchanged ratio, Vietnamese number/time display, saved-capture notice and missing-source/day behavior. Existing evidence/privacy/guard/cache tests remain covered. Internal evidence is asserted unchanged by rendering. `booking_intake`, `fleet_context`, catalogs, source pins, plan, scheduling gates, caches and model payload policy are unchanged in this follow-up.

The committed [synthetic samples](SYNTHETIC_VEHICLE_REPLIES.md) show default/date/alias/held/no-observation/no-pair/detail replies. Typical main examples are717–733 characters. No real data, provider call, Telegram send or production reload was used for these samples. Independent review and worker reload remain with the coordinating chat.

## Action checklist and explicit audit view

2026-10-27: **80 passed in11.91s** across vehicle, Telegram Booking and Chief recovery tests on the final copy requiring all25 original columns. Vehicle `/details` now gives a copyable data request and supported Codex handoff; `/details audit` preserves technical evidence and the exact main response. New tests cover the three views, source-derived missing dates, full-tab headers/gram unit, no invented vehicle import command, owner/topic/reset isolation, unchanged nonvehicle details, correct pinned row links, rejection of wrong-pin/hash/reference metadata, explicit same-export checking and omission of old row links. Default synthetic action replies are1289 characters for an old capture and1487 characters for a fresh capture with two row examples.

This change touches only vehicle rendering and its responder trace/routing, tests and documentation. Engine math, source pins/catalogs/policies, model context, plan, caches, permissions, scheduling and runtime remain unchanged. Samples are synthetic; their sheet identifiers are replaced with clearly fictional values. No real provider/Telegram call, source refresh or worker reload was performed by this implementation task.

## Existing-date range analysis

2026-10-27: **90 passed in38.38s** across vehicle, Telegram Booking and Chief recovery tests on the final range implementation. New synthetic cases cover inclusive two-date boundaries, default coverage, single dates and temporary aliases; reject reversed/outside-period/cutoff/malformed inputs; verify weighted sums over all eligible S/day groups including other-only groups; separately verify the Supra-present S/day cohort; hold duplicate/unknown groups; and report missing-plate/representative rows only for the selected dates. Rows without a usable date remain unassigned, and missing-plate counts are never attributed to a selected vehicle. Raw, Booking and vehicle-catalog hashes all bind the range quality calculation.

The synthetic example yields20% across all eligible source groups versus50% within groups with recorded Supra; neither is a vehicle utilization rate. Tests also exercise the Telegram parser, owner-only action/audit access, unchanged nonvehicle behavior, source/hash provenance, response replay, privacy, daily scheduled freshness gates and provider caps. Scope quality is calculated from already captured Raw rows in RAM and stored only as aggregate counters in the existing per-user/topic trace; the engine's data and numeric evidence remain unchanged. Source update guidance is optional after the selected-range analysis and is not a prerequisite for it.

AST comparison against the prior commit confirms the evidence engine, Booking calculations, Fleet model context, source load, plan, scheduling gates and caches remain unchanged. No production source was imported, no provider/Telegram call was made, and worker/runtime activation remains a separate task for the coordinating chat. [Synthetic range samples](SYNTHETIC_VEHICLE_REPLIES.md) contain fictional values and a fictitious sheet identifier.

## Scoped handling and manual shift forecast

The focused vehicle, handling, Telegram Booking and Chief recovery suite passed **167 tests**. Synthetic cases cover date-boundary and overlapping handling issues, atomic protected configuration rejection, owner/group and source/catalog binding, distinct vehicle/day counting, missing or mixed capacity, other-route warnings, unscoped dates, invalidation, manual-only rendering and exact audit replay. HTTP was mocked; no real provider or Telegram request was sent. The forecast remains a shared-pool estimate and does not change financial totals, model context, schedules or the approved plan. Business staging and runtime activation remain with the coordinating chat.
