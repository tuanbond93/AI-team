"""Smoke benchmark using synthetic CSVs generated and parsed only in RAM."""
from pathlib import Path
from time import perf_counter
import json
import pandas as pd
from ops_team.demo import demo_tables
from ops_team.ingestion import read_file, validate
from ops_team.agents import run_team
from ops_team.reporting import report_bundle


def benchmark(copies=2500):
    raw = demo_tables()
    for kind, key in (("operations", "route"), ("workforce", "shift"), ("fleet", "trip_id")):
        original = raw[kind]
        big = pd.concat([original]*copies, ignore_index=True)
        suffix = (pd.Series(range(len(big))) // len(original)).astype(str)
        big[key] = big[key].astype(str)+"-stress-"+suffix
        raw[kind] = big
    raw["costs"]["amount_vnd"] *= copies
    raw["budgets"]["budget_vnd"] *= copies
    started = perf_counter()
    parsed = {kind: read_file(df.to_csv(index=False).encode("utf-8-sig"), f"STRESS_{kind}.csv")["CSV"] for kind, df in raw.items()}
    read_seconds = perf_counter()-started
    started = perf_counter()
    validation = validate(parsed)
    assert not validation.issues
    validate_seconds = perf_counter()-started
    started = perf_counter()
    run = run_team(validation, "2026-09", "2026-08", ["Duc Hoa", "HCM"], "Chi phí OT xe SLA?", data_label="DỮ LIỆU DEMO — STRESS")
    assert run.audit["review_complete"]
    assert abs(run.metrics["cost_per_unit"].value-411e6/4400) < 1e-8
    analysis_seconds = perf_counter()-started
    started = perf_counter()
    archive = report_bundle(run)
    return {"data": "SYNTHETIC / RAM ONLY", "rows": sum(len(df) for df in parsed.values()), "copies": copies,
        "read_csv_seconds": round(read_seconds, 3), "validation_seconds": round(validate_seconds, 3),
        "analysis_and_audit_seconds": round(analysis_seconds, 3), "export_zip_seconds": round(perf_counter()-started, 3),
        "zip_bytes": len(archive), "api_calls": run.calls, "tasks": len(run.exchanges),
        "audit_review_complete": run.audit["review_complete"], "cost_per_unit_matches_golden": True}


if __name__ == "__main__":
    result = benchmark()
    Path("artifacts").mkdir(exist_ok=True)
    Path("artifacts/performance.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True))
