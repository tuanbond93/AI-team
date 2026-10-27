"""Reproduce the demo and export only its ordinary findings/evidence."""
from pathlib import Path
from ops_team.demo import sample_workbook_bytes, write_samples
from ops_team.ingestion import read_file, validate
from ops_team.agents import run_team
from ops_team.reporting import build_report
from ops_team.muse import demo_muse_bundle


if __name__ == "__main__":
    write_samples()
    source = read_file(sample_workbook_bytes(), "DU_LIEU_DEMO.xlsx")
    validation = validate(source)
    result = run_team(validation, "2026-09", "2026-08", ["Duc Hoa", "HCM"],
        "Chi phí, OT, hiệu suất xe và SLA kỳ này có bất thường?", data_label="DỮ LIỆU DEMO")
    assert not validation.issues
    assert result.audit["review_complete"] and result.audit["sources_passed"]
    assert result.metrics["cost"].value == 411_000_000
    dest = Path("reports")
    dest.mkdir(exist_ok=True)
    (dest / "BAO_CAO_DEMO.md").write_text(build_report(result), encoding="utf-8")
    (dest / "MUSE_DEMO.zip").write_bytes(demo_muse_bundle())
    print(f"DEMO OK | tasks={len(result.exchanges)} | audit_returns={len(result.audit['returned'])} | API calls={result.calls}")
    print("Report: reports/BAO_CAO_DEMO.md")
    print("Muse business/evidence package: reports/MUSE_DEMO.zip (synthetic, no prompt/key/config)")
