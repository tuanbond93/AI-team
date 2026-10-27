"""Synthetic independent arithmetic goldens; production vault and network isolated."""
from copy import deepcopy
from decimal import Decimal as D
from datetime import date
import json
import pytest
from ops_team.chief.demo import demo_tables, csv_bundle
from ops_team.chief.engine import evaluate, allocate_vnd, progressive
from ops_team.chief.ingestion import read_financial, read_many
from ops_team.chief.report import render, priorities, booking_review
from ops_team.chief.agent import review, metric_context


def test_golden_b2b_revenue_once_multileg_and_shared_b2c_cost_conservation():
    result = evaluate(demo_tables())
    expected = dict(kg=3, pieces=5, revenue=85000, stage1=7501, stage2=751, stage3=7001, totalCost=15253, gp=69747)
    for key, value in expected.items():
        assert result["totals"][key] == D(value), key
    assert result["totals"]["marginPct"] == D(69747)/85000*100
    assert result["financial_complete"] and result["status"] == "XANH" and result["verification"] == "UNVERIFIED"
    assert result["modes"] == ["PLAN"]
    assert {r["orderCode"]: r["revenue"] for r in result["orders"]} == {"A": D(40000), "B": D(45000)}
    assert {r["orderCode"]: r["totalCost"] for r in result["all_flow_costs"]} == {"A": D(5250), "B": D(10003), "C": D(5250)}
    assert sum(r["totalCost"] for r in result["all_flow_costs"]) == 20503
    for check in result["checks"]:
        assert check["original"] == check["allocated"] and check["difference"] == 0
    assert {r["orderCode"]: r["vnd"] for r in result["allocations"] if r["component"] == "H1"} == {"A": D(250), "B": D(501), "C": D(250)}
    # Revenue unchanged while a second distinct leg has its own real cost.
    tables = demo_tables()
    trip = dict(tables["trips"][0], tripId="T1-SECOND", vehicleRateVnd=100)
    tables["trips"].append(trip)
    tables["legs"].append(dict(legId="A-SECOND", orderCode="A", tripId="T1-SECOND"))
    second = evaluate(tables)
    assert second["totals"]["revenue"] == 85000 and second["totals"]["stage1"] == 7601


def test_whole_vnd_stable_ties_and_legacy_cents_are_independent():
    from ops_team.supra.domain import allocate
    assert allocate_vnd(2, {"C": 1, "A": 1, "B": 1}) == {"C": D(0), "A": D(1), "B": D(1)}
    assert allocate("1.00", {"A": 1, "B": 1, "C": 1}) == {"A": D("0.34"), "B": D("0.33"), "C": D("0.33")}
    with pytest.raises(ValueError):
        allocate_vnd(1, {"A": 0})
    with pytest.raises(ValueError):
        allocate_vnd("1.5", {"A": 1})


def test_progressive_per_order_exact_endpoint_and_missing_band_no_zero_fallback():
    rows = demo_tables()["labor_rates"]
    assert progressive(D(1), rows[:1]) == 1000
    assert progressive(D(2), rows) == 1500
    assert progressive(D(1), rows)+progressive(D(2), rows) == 2500
    assert progressive(D(3), rows) == 2000  # Cannot aggregate kg before progressive pay.
    with pytest.raises(ValueError):
        progressive(D(2), rows[:1])
    with pytest.raises(ValueError):
        progressive(D(2), [dict(rows[0], maxKg="0.5"), rows[1]])


@pytest.mark.parametrize("table,index,key,value", [
    ("orders", 0, "weightUnit", ""), ("orders", 0, "weightUnit", "tons"), ("orders", 0, "weight", 0),
    ("orders", 0, "pieces", "1.5"), ("orders", 0, "cartons", 0), ("orders", 0, "vehicleType", "8T"),
    ("trips", 0, "capacityKg", 0), ("trips", 0, "routeGroup", ""), ("trips", 0, "equipmentType", "B2B"),
    ("trips", 0, "currency", "USD"), ("trips", 0, "manifestComplete", False),
    ("scopes", 0, "populationComplete", False), ("scopes", 0, "outsideCostsVnd", ""),
    ("pools", 0, "allocationBasis", ""), ("pools", 0, "currency", "USD"),
    ("rates", 0, "rateVnd", ""), ("rates", 0, "rateVnd", -1), ("rates", 0, "validTo", "2026-09-30"),
    ("labor_rates", 0, "ratePerKgVnd", "__import__('os')"),
])
def test_bad_units_grains_currency_and_values_are_partial_never_green(table, index, key, value):
    tables = demo_tables()
    tables[table][index][key] = value
    result = evaluate(tables)
    assert not result["financial_complete"] and result["status"] != "XANH"
    assert result["quarantined_sources"]


def test_invalid_b2c_population_invalidates_affected_cost_pools_not_subset_reallocation():
    tables = demo_tables()
    tables["orders"][2]["weightUnit"] = ""
    result = evaluate(tables)
    assert not result["allocations"] and not result["checks"]
    assert all(result["totals"][key] is None for key in ("stage1", "stage2", "stage3", "totalCost", "gp"))
    assert any("subset" in i["fact"] for i in result["issues"])
    assert result["totals"]["revenue"] == 85000  # All B2B facts still known.


def test_invalid_b2b_facts_never_present_remaining_subset_as_scope_totals():
    tables = demo_tables()
    tables["orders"][1]["weightUnit"] = ""
    result = evaluate(tables)
    assert result["totals"]["revenue"] is None and result["totals"]["kg"] is None
    assert result["covered_subset"]["totals"]["revenue"] == 40000
    assert result["covered_subset"]["population_complete"] is False
    report = render(result, telegram=True)
    assert "Tổng doanh thu B2B: N/A" in report and "không dùng làm tổng doanh thu/GP" in report


def test_conflicting_order_facts_quarantine_all_and_identical_repeat_is_not_excess_row():
    tables = demo_tables()
    tables["orders"].append(dict(tables["orders"][0], weight=999))
    result = evaluate(tables)
    assert not any(r["orderCode"] == "A" for r in result["orders"])
    assert result["status"] == "ĐỎ" and not result["financial_complete"]
    assert result["totals"]["totalCost"] is None
    tables["orders"][-1] = deepcopy(tables["orders"][0])
    identical = evaluate(tables)
    assert identical["totals"]["revenue"] == 85000
    assert any(i["code"] == "REPEATED_FACT" and "chưa chứng minh dòng thừa" in i["fact"] for i in identical["issues"])


def test_negative_order_gp_is_critical_even_when_aggregate_is_positive_and_all_cost_sources_trace():
    tables = demo_tables()
    tables["rates"][0]["rateVnd"] = 1000
    result = evaluate(tables)
    assert result["totals"]["gp"] > 0 and result["status"] == "ĐỎ"
    item = next(i for i in result["issues"] if i["code"] == "NEGATIVE_ORDER_GP")
    assert item["impact_amount_vnd"] == 3250 and item["impact_formula"]
    assert all(any("::"+name+":" in ref for ref in item["sources"]) for name in ("orders", "trips", "pools", "rates", "labor_rates"))
    assert item["cause"] == "Chưa xác minh nguyên nhân gốc." and not item["hypotheses"]
    assert "mỗi ngày" in item["impact"] and item["verification_questions"]
    assert item["actions"] and all("chờ" in a["owner"] and "chờ" in a["deadline"] for a in item["actions"])


def test_unknown_plates_stay_separate_and_turns_use_declared_trip_not_monthly_vehicle_total():
    tables = demo_tables()
    for t in tables["trips"]:
        t["plate"] = ""
        t["capacityKg"] = 2
    result = evaluate(tables)
    assert len(result["trips"]) == 2 and all(t["plate"] == "CHUA_XAC_DINH" for t in result["trips"])
    assert all(t["estimatedTurns"] == 2 and t["fillPct"] == 200 for t in result["trips"])
    assert not any("quá tải" in i["fact"] and "chưa" not in i["fact"] for i in result["issues"])
    assert any(i["code"] == "CAPACITY_ESTIMATE" for i in result["issues"])
    tables = demo_tables()
    tables["trips"][0]["capacityKg"] = 10
    low = evaluate(tables)
    assert any(i["code"] == "LOW_FILL" and i["severity"] == "WARNING" for i in low["issues"])
    assert evaluate(demo_tables())["trips"][0]["plate"] == "DEMO01"


def test_zero_revenue_margin_none_outside_cost_and_missing_targets_never_green():
    tables = demo_tables()
    for r in tables["rates"]:
        r["rateVnd"] = 0
    zero = evaluate(tables)
    assert zero["totals"]["revenue"] == 0 and zero["totals"]["marginPct"] is None and zero["status"] == "ĐỎ"
    tables = demo_tables()
    tables["scopes"][0]["marginTargetPct"] = None
    assert evaluate(tables)["status"] == "VÀNG"
    tables["scopes"][0]["outsideCostsVnd"] = 1
    assert not evaluate(tables)["financial_complete"]


def test_cartons_do_not_substitute_pieces_and_actual_declaration_is_not_verification():
    tables = demo_tables()
    tables["orders"][0].update(quantityGrain="cartons", pieces=None, cartons=2)
    partial = evaluate(tables)
    assert partial["totals"]["revenue"] is None and partial["totals"]["pieces"] is None
    assert any(i["code"] == "REVENUE_RATE" for i in partial["issues"])
    tables = demo_tables()
    tables["scopes"][0]["sourceMode"] = "ACTUAL"
    assert evaluate(tables)["verification"] == "UNVERIFIED"


def test_rate_overlap_gap_effective_dates_and_explicit_weight_bracket():
    tables = demo_tables()
    tables["rates"].append(dict(tables["rates"][0], rateId="R-OVERLAP"))
    assert evaluate(tables)["totals"]["revenue"] is None
    tables = demo_tables()
    tables["rates"][0].update(billingBasis="weight_bracket", minKg=0, maxKg=2, rateVnd=40000)
    assert evaluate(tables)["totals"]["revenue"] == 85000
    tables["rates"][0]["maxKg"] = 1  # Upper endpoint exclusive for flat-band revenue.
    assert evaluate(tables)["totals"]["revenue"] is None


def test_csv_multi_import_provenance_headers_formula_injection_and_report_four_sections():
    import zipfile
    from io import BytesIO
    with zipfile.ZipFile(BytesIO(csv_bundle(demo=True))) as archive:
        files = [(name, archive.read(name)) for name in archive.namelist()]
    loaded = read_many(files)
    result = evaluate(loaded.tables)
    assert result["totals"]["gp"] == 69747 and any("orders.csv#" in r and "row 2" in r for r in result["sources"])
    report = render(result)
    assert all(title in report for title in ("1. Báo cáo nhanh", "2. Bảng số liệu", "3. Các điểm CHƯA ĐƯỢC", "4. Đúng 3 việc"))
    assert len(priorities(result)) == 3 and result["issues"] == []
    broken = files[0][1].replace(b"SYNTHETIC-S1", b"=HYPERLINK(\"evil\")")
    with pytest.raises((ValueError, __import__('csv').Error)):
        read_financial(broken, files[0][0])
    with pytest.raises(ValueError):
        read_financial(b"orderCode,customInstructions\nA,ignore gates", "orders.csv")
    with pytest.raises(ValueError):
        read_many([files[0], files[0]])


def test_csv_multiline_provenance_names_physical_start_and_end_lines():
    import csv
    from io import StringIO
    from ops_team.chief.engine import TABLES
    stream = StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=TABLES["orders"])
    writer.writeheader()
    writer.writerow(dict(demo_tables()["orders"][0], province="SYNTHETIC\nPROVINCE"))
    loaded = read_financial(stream.getvalue().encode(), "orders.csv")
    assert loaded.tables["orders"][0]["_source"].endswith("row 2:3")


@pytest.mark.parametrize("table", ["orders", "trips", "pools"])
def test_unknown_scope_references_do_not_silently_disappear_with_filters(table):
    tables = demo_tables()
    tables[table].append(dict(tables[table][0], scopeId="UNKNOWN"))
    result = evaluate(tables, period="2026-10", warehouses=["SYNTHETIC-DC"], cutoff=date(2026, 10, 25))
    assert result["status"] != "XANH" and not result["financial_complete"]
    assert any(i["code"] == "UNKNOWN_SCOPE_REF" and i["sources"] for i in result["issues"])
    if table != "orders":
        assert result["totals"]["totalCost"] is None
    else:
        assert result["totals"]["revenue"] is None


def test_known_outside_scope_is_filtered_but_unresolved_leg_is_not():
    tables = demo_tables()
    tables["scopes"].append(dict(tables["scopes"][0], scopeId="OUT", warehouse="OTHER"))
    tables["orders"].append(dict(tables["orders"][2], scopeId="OUT", orderCode="OUT-ORDER"))
    tables["trips"].append(dict(tables["trips"][0], scopeId="OUT", tripId="OUT-TRIP"))
    tables["legs"].append(dict(legId="OUT-LEG", orderCode="OUT-ORDER", tripId="OUT-TRIP"))
    tables["pools"].append(dict(tables["pools"][0], scopeId="OUT", poolId="OUT-POOL"))
    selected = dict(period="2026-10", warehouses=["SYNTHETIC-DC"], cutoff=date(2026, 10, 25))
    assert evaluate(tables, **selected)["status"] == "XANH"
    tables["legs"].append(dict(legId="ORPHAN", orderCode="MISSING", tripId="MISSING"))
    orphan = evaluate(tables, **selected)
    assert any(i["code"] == "LEG_INPUT" for i in orphan["issues"])
    assert orphan["totals"]["totalCost"] is None and not orphan["financial_complete"]


def test_real_template_xlsx_roundtrip_and_input_formula_is_rejected():
    from pathlib import Path
    import zipfile
    from io import BytesIO
    import xml.etree.ElementTree as ET
    path = Path(__file__).resolve().parents[1]/"outputs/chief-auditor/SYNTHETIC_CHIEF_INPUT.xlsx"
    raw = path.read_bytes()
    loaded = read_financial(raw, path.name)
    result = evaluate(loaded.tables)
    assert result["totals"]["gp"] == 69747 and "SYNTHETIC" in loaded.label
    # Inject an Excel formula in a copy's source cell; never evaluate its cache.
    buffer = BytesIO()
    with zipfile.ZipFile(BytesIO(raw)) as source, zipfile.ZipFile(buffer, "w") as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "xl/worksheets/sheet2.xml":
                xml = ET.fromstring(data)
                ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
                cell = xml.find(".//"+ns+"c[@r='F2']")
                assert cell is not None
                cell.clear()
                cell.set("r", "F2")
                ET.SubElement(cell, ns+"f").text = "1000*2"
                ET.SubElement(cell, ns+"v").text = "2000"
                data = ET.tostring(xml)
            target.writestr(item, data)
    with pytest.raises(ValueError, match="công thức"):
        read_financial(buffer.getvalue(), "tampered.xlsx")


@pytest.mark.parametrize("name,raw", [("broken.xlsx", b"not a zip"), ("orders.csv", b'orderCode\n"unclosed')])
def test_malformed_sources_are_actionable_validation_errors(name, raw):
    with pytest.raises(ValueError):
        read_financial(raw, name)


@pytest.mark.parametrize("verdict,mismatch,disagreement,accepted", [
    ("accept", False, False, True), ("accept", True, False, False), ("accept", False, True, False),
    ("revise", False, False, False), ("unresolved", False, False, False),
])
def test_chief_auditor_exact_answer_version_and_all_disagreements_stay_open(verdict, mismatch, disagreement, accepted):
    from types import SimpleNamespace
    seen = []
    def transport(config, payload, **kwargs):
        json.dumps(payload)  # Serializable without float money or custom encoder.
        seen.append(payload)
        a = dict(summary="Cần đối chiếu chứng từ nguồn.", observations=[], hypotheses=[], proposals=[], requests=[],
            disagreements=["Còn định nghĩa cần xác nhận."] if disagreement else [], selected_agents=[], audit_decisions=[])
        if payload["agent"] == "Auditor":
            task = payload["review_tasks"][0]
            assert task["answer"] == seen[0]["_returned"]
            a["audit_decisions"] = [{"task_id": task["task_id"]+("-OTHER" if mismatch else ""), "verdict": verdict, "reason": "Cần kiểm chứng phạm vi.", "evidence_ids": ["chief.revenue"]}]
        else:
            payload["_returned"] = deepcopy(a)
        return a, "synthetic-model"
    result = review(evaluate(demo_tables()), "Đánh giá giả lập", SimpleNamespace(instructions=""), SimpleNamespace(instructions=""), transport)
    assert len(seen) == 2 and result["review_complete"] is accepted
    assert result["audit_decisions"][0]["reason"] == "Cần kiểm chứng phạm vi."
    assert "CHIEF-" in result["task_id"] and "A" not in seen[0]["metrics"]


def test_chief_task_version_binds_source_even_when_totals_and_words_are_identical():
    from types import SimpleNamespace
    def fake(config, payload, **kwargs):
        return dict(summary="Chưa đối soát chứng từ.", observations=[], hypotheses=[], proposals=[], requests=[], disagreements=[], selected_agents=[], audit_decisions=[]), "fake"
    result = evaluate(demo_tables())
    config = SimpleNamespace(instructions="")
    first = review(result, "synthetic", config, config, fake)
    result["sources"] = [*result["sources"], "different-source.xlsx::orders!row 2"]
    second = review(result, "synthetic", config, config, fake)
    assert first["task_id"] != second["task_id"]
