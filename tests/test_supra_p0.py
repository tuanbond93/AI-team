from pathlib import Path
from decimal import Decimal
from copy import deepcopy
import pytest
from ops_team.supra.ingestion import read_workbook, read_document
from ops_team.supra.domain import allocate, number, Principal
from ops_team.supra.finance import forecast, actual_pnl
from ops_team.supra.service import SupraService

SOURCE = Path(r"C:\Users\Son-Tuan Nguyen\Desktop\Super Tracking chi phí Supra")


@pytest.fixture(scope="module")
def dataset():
    return read_workbook((SOURCE/"Supra_Phu_Tho_PnL_CostToServe_Pilot14Ngay.xlsx").read_bytes(), "Supra_Phu_Tho_PnL_CostToServe_Pilot14Ngay.xlsx")


def test_source_plan_not_actual_and_provenance(dataset):
    assert len(dataset["routes"]) == 12 and len(dataset["stores"]) == 130
    assert sum(r["kg"] for r in dataset["routes"]) == Decimal("33372")
    assert not dataset["actuals"] and all(not s["real_id"] for s in dataset["stores"])
    assert dataset["verification"] == "UNVERIFIED"
    assert "7708c7946f2f" in dataset["routes"][0]["source"]


def test_decimal_goldens_26_days_and_rollups(dataset):
    result = forecast(dataset, 26)
    s = result["summary"]
    assert s["revenue"] == Decimal("484160976.00")
    assert s["cost"] == Decimal("626848728.80")
    assert s["profit"] == Decimal("-142687752.80")
    assert abs(s["cost_per_pcs"]-Decimal("240.8163179942036")) < Decimal("0.000001")
    assert s["verified_savings"] is None and s["cost_per_drop"] is None
    assert sum(r["cost"] for r in result["routes"]) == sum(r["amount"] for r in result["allocations"]) == s["cost"]
    assert sum(r["profit"] for r in result["hubs"].values()) == s["profit"]
    assert sum(r["amount"] for r in result["allocations"] if r["pool_id"] == "5T_HYBRID") == 57000000


def test_30_days_not_multiplier_on_fixed_month(dataset):
    p26, p30 = (forecast(dataset, d)["summary"] for d in (26, 30))
    assert p30["revenue"] == p26["revenue"]*30/26
    assert p30["cost"] == (p26["cost"]-57000000)*30/26+57000000
    assert p30["cost"] != p26["cost"]*30/26


@pytest.mark.parametrize("value", [None, "", "nan", "Infinity", -1, True, "1,000"])
def test_bad_financial_values(value):
    with pytest.raises(ValueError):
        number(value)


def test_invalid_office_file_rejected_as_validation_error():
    with pytest.raises(ValueError, match="Office"):
        read_workbook(b"not an office zip", "invalid.xlsx")


def test_shared_cost_allocation_and_rounding():
    values = allocate("0.05", {"SUPRA": 1, "ECOM": 1, "OTHER": 1})
    assert sum(values.values()) == Decimal(".05")
    assert values == allocate(".05", {"OTHER": 1, "ECOM": 1, "SUPRA": 1})
    with pytest.raises(ValueError):
        allocate(100, {"SUPRA": 0})


def test_actual_no_missing_to_zero_no_mixed_plan(dataset):
    r = SupraService(dataset).query_pnl(Principal("viewer"), mode="ACTUAL")
    assert r["summary"]["profit"] is None and r["verification"] == "NO_ACTUAL"
    record = dict(date="2026-10-26", route="DH", kg=10, pcs=30, drops=1, on_time=1,
        costs={k: 0 for k in ("dc", "linehaul", "hub", "last_mile", "delivery_labor", "extra")},
        source="test synthetic", document_ref="test invoice", verification="VERIFIED", price_pcs=186)
    actual = actual_pnl([record], {"DH"})
    assert actual["summary"]["profit"] == 5580 and actual["summary"]["sla"] == 1
    record["costs"]["extra"] = None
    assert actual_pnl([record], {"DH"})["summary"]["profit"] is None
    record["costs"]["extra"] = 0
    assert not actual_pnl([record, record], {"DH"})["records"]


def test_tstt_replaces_last_mile_not_add_twice(dataset):
    changed = deepcopy(dataset)
    changed["assumptions"]["ncc_drops"] = Decimal(21)
    base, case = (forecast(d, 26) for d in (dataset, changed))
    row = next(r for r in case["routes"] if r["route"] == "TSTT")
    assert row["costs"]["last_mile"] == Decimal(3780000)*26
    assert row["profit"] == Decimal(-2325052)*26
    assert case["summary"]["cost"]-base["summary"]["cost"] == (Decimal(3780000)-Decimal("1285768"))*26


def test_baseline_findings_and_scope_authorization(dataset):
    doc = read_document((SOURCE/"Phuong_An_Toi_Uu_Chi_Phi_Supra_Phu_Tho.docx").read_bytes(), "source.docx")
    service = SupraService(dataset, doc)
    findings = service.reconciliation(Principal("viewer"))
    assert len(findings) == 7 and any(r["id"] == "TRUCK8_UNIT_RATE" and r["status"] == "CORRECTED_WITH_EVIDENCE" for r in findings)
    assert all(r["sources"] for r in findings)
    for principal in (Principal("other", customer="OTHER"), Principal("other", region="HCM"), Principal("guest", permissions=frozenset())):
        with pytest.raises(PermissionError):
            service.query_pnl(principal)
    subset = service.query_pnl(Principal("reader"), route="DH")
    assert len(subset["routes"]) == 1 and all(r["route"] == "DH" for r in subset["allocations"])
    stores = service.store_plan(Principal("reader"))
    assert sum(s["cost"] for s in stores) == forecast(dataset)["summary"]["cost"]
