"""Read authorized source files and emit business validation only, no AI calls."""
from pathlib import Path
from decimal import Decimal
import hashlib
import argparse
from ops_team.supra.ingestion import read_workbook, read_document
from ops_team.supra.domain import Principal, encode
from ops_team.supra.service import SupraService
from ops_team.supra.capacity import fleet_review
from ops_team.supra.legacy import read_legacy_month


def verify(source_dir, output_dir):
    root, out = Path(source_dir), Path(output_dir)
    book = root/"Supra_Phu_Tho_PnL_CostToServe_Pilot14Ngay.xlsx"
    doc = root/"Phuong_An_Toi_Uu_Chi_Phi_Supra_Phu_Tho.docx"
    blobs = {file.name: file.read_bytes() for file in (book, doc)}
    before = {name: hashlib.sha256(content).hexdigest() for name, content in blobs.items()}
    dataset = read_workbook(blobs[book.name], book.name)
    document = read_document(blobs[doc.name], doc.name)
    service, principal = SupraService(dataset, document), Principal("local-validation")
    plans = {str(d): service.query_pnl(principal, days=d) for d in (26, 30)}
    assert sum(r["stores"] for r in dataset["routes"]) == 130
    for plan in plans.values():
        assert sum(r["cost"] for r in plan["routes"]) == plan["summary"]["cost"]
        assert sum(r["cost"] for r in plan["hubs"].values()) == plan["summary"]["cost"]
    # Goldens apply only to the exact supplied planning workbook version.
    if before[book.name] == "7708c7946f2ff6ea80979b3e04bf5d3f1b1bd22c0d4e96f859f204fa972c7db8":
        assert plans["26"]["summary"]["cost"] == Decimal("626848728.80")
        assert plans["26"]["summary"]["profit"] == Decimal("-142687752.80")
    assert before == {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in (book, doc)}
    report = {"mode": "PLAN_UNVERIFIED", "source_hashes": before, "source_files_unchanged": True,
        "plans": plans, "BASELINE_RECONCILIATION": service.reconciliation(principal),
        "capacity": fleet_review(dataset), "pilot": service.pilot_status(principal),
        "llm_calls": 0, "gtalk_sent": 0, "operational_actions": 0}
    legacy_path = root/"src/data/multiMonthSupraData.json"
    if legacy_path.exists() and (root/"supra_engine.py").exists():
        original = legacy_path.read_bytes()
        legacy = read_legacy_month(principal, root)
        assert legacy_path.read_bytes() == original
        report["historical_source_reuse"] = {**{k: v for k, v in legacy.items() if k != "lineage"}, "phu_tho_lineage_rows": len(legacy["lineage"]), "external_store_unchanged": True}
    out.mkdir(parents=True, exist_ok=True)
    (out/"SUPRA_VALIDATION.json").write_text(encode(report), encoding="utf-8")
    print("SUPRA SOURCE SMOKE OK | PLAN/UNVERIFIED | 12 routes | 130 temporary stores | 0 LLM/GTalk/actions")
    for days, plan in plans.items():
        summary = plan["summary"]
        print(f"{days} days | revenue={summary['revenue']} | cost={summary['cost']} | P&L={summary['profit']} VND")
    print("Business validation: "+str(out/"SUPRA_VALIDATION.json"))
    if "historical_source_reuse" in report:
        print("Existing Supra snapshot/validator reused read-only | PHU_THO only | MODELLED/UNVERIFIED | external store unchanged")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", default=str(Path.home()/"Desktop"/"Super Tracking chi phí Supra"))
    parser.add_argument("--output-dir", default="reports")
    args = parser.parse_args()
    verify(args.source_dir, args.output_dir)
