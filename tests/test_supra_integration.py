from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import json
import io
import csv
import sqlite3
import httpx
import pytest
import openpyxl
from streamlit.testing.v1 import AppTest
from ops_team.supra.domain import Principal, CATEGORIES
from ops_team.supra.ingestion import read_workbook, read_actual_csv
from ops_team.supra.finance import forecast
from ops_team.supra.service import SupraService
from ops_team.supra.capacity import capacity_case, fleet_review
from ops_team.supra.store import EventStore
from ops_team.supra.workflow import ShadowWorkflow, MockGTalkAdapter, proposal, detect_incidents
from ops_team.supra.api import LocalAPI
from ops_team.supra.agent import analyze_account, CustomerOperationsAgent
from ops_team.providers import ModelConfig, ProviderError

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(r"C:\Users\Son-Tuan Nguyen\Desktop\Super Tracking chi phí Supra\Supra_Phu_Tho_PnL_CostToServe_Pilot14Ngay.xlsx")
READER = Principal("viewer")
AI = Principal("Supra Account AI", permissions=frozenset({"VIEW_PNL", "ANALYZE", "NOTIFY_MOCK"}))
MANAGER = Principal("finance-manager", permissions=frozenset({"VIEW_PNL", "APPROVE", "EXECUTE_SHADOW", "IMPORT_ACTUAL", "VERIFY_FINANCE"}))


@pytest.fixture(scope="module")
def dataset():
    return read_workbook(SOURCE.read_bytes(), SOURCE.name)


def sample_actual_csv():
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=["date", "route", "kg", "pcs", "drops", "on_time", "price_pcs", "document_ref", *CATEGORIES, "verification", "source"])
    writer.writeheader()
    writer.writerow(dict(date="2026-10-26", route="DH", kg=10, pcs=30, drops=1, on_time=1, price_pcs=186,
        document_ref="SYNTHETIC-INVOICE-001", **{k: 0 for k in CATEGORIES}, verification="VERIFIED", source="forged"))
    return buffer.getvalue().encode()


@pytest.mark.parametrize("change,expected", [({"kg": 16000}, False), ({"ecom_kg": 2000}, False), ({"outbound": 300}, False)])
def test_capacity_never_recommends_overload_or_missed_dc(change, expected):
    params = dict(kg=13713, vehicles=4, legal_kg=1900, trips=2, loading=40, outbound=40, service=60, returning=40,
        legal_verified=True, store_close=1200, cargo_m3=10, vehicle_m3=10)
    params.update(change)
    assert capacity_case(**params)["feasible"] is expected


def test_nominal_capacity_is_not_feasibility(dataset):
    vt = next(r for r in fleet_review(dataset) if r["id"] == "F7")
    assert vt["utilization"] == Decimal(13713)/Decimal(15200)
    assert vt["feasible"] is None and vt["minimum_feasible_vehicles"] is None
    assert capacity_case(10, 1, 1900, 2, legal_verified=True, loading=40, outbound=30, service=20, returning=30,
        store_close=1200, cargo_m3=1, vehicle_m3=1)["feasible"] is True
    assert capacity_case(10, 1, None, 2)["status"] == "NEEDS_DATA"


def test_source_roundtrip_version_history_and_chain(dataset, tmp_path):
    store = EventStore(tmp_path/"supra.db")
    store.save_snapshot(dataset)
    store.save_snapshot(read_workbook(SOURCE.read_bytes(), SOURCE.name))
    restored = store.load_snapshot(READER, dataset["version"])
    assert forecast(restored)["summary"]["cost"] == forecast(dataset)["summary"]["cost"]
    assert store.versions(READER) == [dataset["version"]]
    altered = deepcopy(dataset)
    altered["assumptions"]["price_pcs"] += 1
    with pytest.raises(ValueError, match="Version"):
        store.save_snapshot(altered)
    store.append("test-1", "id", "TEST", "tester", {"status": "OPEN"})
    assert not store.append("test-1", "id", "TEST", "tester", {"status": "CHANGED"})
    assert store.verify_chain()
    store.db.execute("UPDATE events SET actor='tampered'")
    store.db.commit()
    assert not store.verify_chain()
    store.close()


def test_shadow_workflow_permission_idempotency_and_rollback(dataset):
    store = EventStore()
    workflow = ShadowWorkflow(store)
    item = proposal(100, 90, title="Synthetic scenario", sources=["synthetic source"], constraints="legal/SLA pending",
        missing="actual evidence", rollback="restore prior shadow state", approver="manager")
    entity = workflow.register(AI, item)
    workflow.register(AI, item)
    assert len(store.events()) == 1
    with pytest.raises(PermissionError):
        workflow.decide(READER, entity, True, "no authority")
    with pytest.raises(PermissionError):
        workflow.execute(MANAGER, entity)
    self_approver = Principal(AI.name, permissions=MANAGER.permissions)
    with pytest.raises(ValueError):
        workflow.decide(self_approver, entity, True, "self approval denied")
    workflow.decide(MANAGER, entity, True, "test shadow only")
    adapter = MockGTalkAdapter(store)
    assert adapter.send(AI, entity, "synthetic message")["delivery"] == "DRY_RUN_NOT_SENT"
    assert workflow.state(READER, entity)["status"] == "APPROVED_SHADOW"
    assert adapter.send(AI, entity, "again")["idempotent"]
    assert workflow.execute(MANAGER, entity)["external_effect"] is False
    assert workflow.execute(MANAGER, entity)["idempotent"]
    workflow.rollback(MANAGER, entity, "test restore")
    assert workflow.state(READER, entity)["status"] == "ROLLED_BACK"
    assert store.verify_chain()


def test_import_actual_requires_finance_and_never_trusts_status(dataset):
    service, store = SupraService(dataset), EventStore()
    imported = read_actual_csv(sample_actual_csv(), "test.csv")
    assert imported["records"][0]["verification"] == "UNVERIFIED" and "forged" not in imported["records"][0]["source"]
    with pytest.raises(PermissionError):
        service.import_actual(READER, imported, store)
    unverified = service.import_actual(MANAGER, imported, store)
    assert unverified.query_pnl(READER, mode="ACTUAL")["summary"]["profit"] is None
    verified = unverified.verify_actual(MANAGER, store, "SYNTHETIC Finance attestation")
    assert verified.query_pnl(READER, mode="ACTUAL")["summary"]["profit"] == 5580
    assert verified.pilot_status(READER)["verified_route_days"] == 1
    assert not verified.pilot_status(READER)["complete_pilot"]
    assert any(i["kind"] == "ACTUAL_NOT_RECONCILED" for i in detect_incidents(verified.dataset,
        verified.query_pnl(READER), verified.query_pnl(READER, mode="ACTUAL")))
    assert service.dataset["actuals"] == []
    assert unverified.dataset["actuals"][0]["verification"] == "UNVERIFIED"


def test_duplicate_cost_document_cannot_be_verified(dataset):
    service, store = SupraService(dataset), EventStore()
    imported = read_actual_csv(sample_actual_csv(), "test.csv")
    imported["records"].append({**imported["records"][0], "route": "PN"})
    unverified = service.import_actual(MANAGER, imported, store)
    with pytest.raises(ValueError):
        unverified.verify_actual(MANAGER, store, "same cost invoice counted twice")


def test_workbook_actual_formula_status_never_certifies(dataset):
    workbook = openpyxl.load_workbook(SOURCE)
    s = workbook["05_NHAT_KY_14N"]
    for col, value in {"E": 10, "F": 30, "H": 1, "S": 1, "V": "synthetic evidence", **{c: 0 for c in "IJKLMN"}}.items():
        s[f"{col}5"] = value
    buffer = io.BytesIO()
    workbook.save(buffer)
    imported = read_workbook(buffer.getvalue(), "test_modified_source.xlsx")
    assert len(imported["actuals"]) == 1 and imported["actuals"][0]["date"] == "2026-10-26"
    assert imported["actuals"][0]["verification"] == "UNVERIFIED"
    assert SupraService(imported).query_pnl(READER, mode="ACTUAL")["summary"]["profit"] is None


def test_read_api_http_auth_scope_and_no_writes(dataset):
    service = SupraService(dataset)
    api = LocalAPI(service, "synthetic-read-token-long-enough", READER, port=0)
    try:
        with httpx.Client(trust_env=False) as client:
            base = f"http://127.0.0.1:{api.port}"
            assert client.get(base+"/v1/supra/pnl").status_code == 403
            headers = {"Authorization": "Bearer synthetic-read-token-long-enough"}
            result = client.get(base+"/v1/supra/pnl?mode=PLAN&days=26&route=DH", headers=headers)
            assert result.status_code == 200 and len(result.json()["routes"]) == 1
            assert client.get(base+"/v1/supra/pnl?customer=OTHER", headers=headers).status_code == 400
            assert client.get(base+"/v1/supra/pnl?mode=ACTUAL", headers=headers).json()["summary"]["profit"] is None
            assert client.post(base+"/v1/supra/pnl", headers=headers).status_code == 405
            assert result.headers["Cache-Control"] == "no-store"
    finally:
        api.close()


def test_incidents_idempotent_and_hypotheses_not_facts(dataset):
    service = SupraService(dataset)
    incidents = detect_incidents(dataset, service.query_pnl(READER), service.query_pnl(READER, mode="ACTUAL"))
    assert any(r["kind"] == "PLAN_ROUTE_LOSS" for r in incidents)
    assert all(r["sources"] and r["confidence"] == "ARITHMETIC_ONLY" and r["outcome"] is None for r in incidents)
    assert [r["id"] for r in incidents] == [r["id"] for r in detect_incidents(dataset, service.query_pnl(READER), service.query_pnl(READER, mode="ACTUAL"))]


def test_customer_agent_consent_secrets_and_exact_audit(dataset):
    config = ModelConfig("OpenAI", "synthetic-model", "synthetic-secret-key")
    service = SupraService(dataset)
    calls = []
    def transport(config, payload, timeout):
        calls.append(payload)
        out = {"summary": "Synthetic interpretation", "observations": [{"text": "Source is PLAN only", "evidence_ids": ["supra.cost"]}],
            "hypotheses": [], "proposals": [], "requests": [], "disagreements": [], "selected_agents": [], "audit_decisions": []}
        if payload["agent"] == "Auditor":
            out["audit_decisions"] = [{"task_id": "SUPRA-ACCOUNT-v1", "verdict": "accept", "reason": "synthetic review", "evidence_ids": ["supra.cost"]}]
        return out, "mock-actual"
    with pytest.raises(PermissionError):
        analyze_account(service, READER, config, config, transport=transport)
    assert not calls
    result = analyze_account(service, READER, config, config, activated=True, transport=transport)
    assert len(calls) == 2 and result["review_complete"]
    assert "session_instructions" in calls[0] and "synthetic-secret-key" not in json.dumps(calls)
    assert CustomerOperationsAgent.resolve("SUPRA", "PHU_THO") == "Supra Account AI"
    with pytest.raises(PermissionError):
        CustomerOperationsAgent.resolve("OTHER", "PHU_THO")


def test_supra_embedded_ui_source_to_report_and_finance_gate(tmp_path, monkeypatch):
    import ops_team.supra.ui as ui
    monkeypatch.setattr(ui, "ROOT", tmp_path)
    at = AppTest.from_file(str(ROOT/"app.py"), default_timeout=30).run()
    assert not at.exception and any(t.label == "Supra Account AI" for t in at.tabs)
    at.button(key="supra_read_local").click().run()
    assert not at.exception and "supra_preview" in at.session_state
    at.button(key="supra_confirm").click().run()
    assert not at.exception and "supra_service" in at.session_state
    assert any(m.label == "Lãi/lỗ PLAN" and "142.687.753" in m.value for m in at.metric)
    assert at.button(key="supra_verify_actual").disabled
    at.button(key="supra_record_incidents").click().run()
    assert not at.exception and at.session_state["supra_store"].verify_chain()
    at.button(key="supra_record_incidents").click().run()
    events = at.session_state["supra_store"].events()
    assert len([e["event_key"] for e in events]) == len({e["event_key"] for e in events})
    assert not at.exception and at.button(key="supra_ai_run").disabled


def test_detail_json_transactional_ingestion_verification_and_api(dataset):
    service, store = SupraService(dataset), EventStore()
    data = {"revenue": [dict(revenue_id="synthetic-bill", customer="SUPRA", region="PHU_THO", hub="VIET_TRI", route="VTRI",
        date="2026-10-26", vehicle_id="SYNTHETIC-VEH", store_id="SYNTHETIC-STORE", order_id="SYNTHETIC-ORDER", drop_id="SYNTHETIC-DROP",
        amount=1000, kg=10, pcs=30, drops=1, verification="VERIFIED", secret="must-not-persist")],
        "pools": [dict(cost_id="synthetic-cost", category="linehaul", amount=500, behavior="VARIABLE", period_start="2026-10-01", period_end="2026-10-31")],
        "allocations": [dict(allocation_id="synthetic-allocation", cost_id="synthetic-cost", revenue_id="synthetic-bill", amount=500)]}
    raw = json.dumps(data).encode()
    imported = service.import_business_json(MANAGER, store, raw, "synthetic.json", "detail_ledger")
    assert imported.query_detail(READER)["summary"] is None
    assert "secret" not in json.dumps(imported.dataset["detail_ledger"])
    with pytest.raises(PermissionError):
        imported.verify_detail(READER, store, "no permission")
    verified = imported.verify_detail(MANAGER, store, "synthetic finance acceptance")
    assert verified.query_detail(READER, "order_id")["summary"]["profit"] == 500
    restored = SupraService(store.load_snapshot(READER, verified.dataset["version"]))
    assert restored.query_detail(READER, "vehicle_id")["summary"]["cost"] == 500
    api = LocalAPI(verified, "synthetic-very-long-api-token", READER, port=0)
    try:
        result = httpx.get(f"http://127.0.0.1:{api.port}/v1/supra/detail?group_by=vehicle_id", headers={"Authorization": "Bearer synthetic-very-long-api-token"}, trust_env=False)
        assert result.status_code == 200 and result.json()["groups"][0]["key"] == "SYNTHETIC-VEH"
    finally:
        api.close()
    data["allocations"][0]["amount"] = 501
    with pytest.raises(ValueError):
        service.import_business_json(MANAGER, store, json.dumps(data).encode(), "bad.json", "detail_ledger")
    assert len(store.versions(READER)) == 2


def test_quarantined_row_blocks_finance_total(dataset):
    changed = deepcopy(dataset)
    changed["issues"] = [{"source": "synthetic invalid row", "error": "invalid amount"}]
    service = SupraService(changed)
    assert service.query_pnl(READER, mode="ACTUAL")["verification"] == "PARTIAL"
    with pytest.raises(ValueError, match="cách ly"):
        service.verify_actual(MANAGER, EventStore(), "synthetic acceptance cannot drop errors")


def test_outcome_never_turns_plan_saving_into_verified(dataset):
    store, workflow = EventStore(), None
    workflow = ShadowWorkflow(store)
    item = proposal(100, 90, title="Synthetic outcome test", sources=["synthetic source"], constraints="SLA pending", missing="evidence",
        rollback="restore test", approver="finance")
    entity = workflow.register(AI, item)
    workflow.decide(MANAGER, entity, True, "synthetic shadow approval")
    workflow.execute(MANAGER, entity)
    before, after = SupraService(dataset), SupraService({**dataset, "version": "synthetic-other"})
    with pytest.raises(ValueError, match="PLAN"):
        workflow.record_outcome(MANAGER, entity, before, after, "test missing actual")
    assert workflow.state(READER, entity)["verified_savings_vnd"] is None


def test_session_secret_never_persisted_in_business_store(dataset, tmp_path):
    secret = "SYNTHETIC-SESSION-TOKEN-NEVER-WRITE"
    path = tmp_path/"business.db"
    store = EventStore(path, [secret])
    with pytest.raises(ValueError, match="chặn lưu"):
        store.append("id", "entity", "NOTE", "user", {"note": secret})
    changed = deepcopy(dataset)
    changed["actuals"] = [{"document_ref": secret}]
    with pytest.raises(ValueError, match="chặn lưu"):
        store.save_snapshot(changed)
    assert not store.events() and not store.versions(READER)
    store.close()
    assert secret.encode() not in path.read_bytes()


def test_snapshot_and_audit_are_atomic_when_event_guard_fails(dataset):
    store = EventStore(private_fragments=["synthetic-private-session-material"])
    with pytest.raises(ValueError):
        store.save_version(dataset, "test", "SOURCE", "actor", {"note": "synthetic-private-session-material"})
    assert not store.versions(READER) and not store.events()


def test_actual_csv_physical_rows_and_multiline_provenance():
    rows = list(csv.reader(io.StringIO(sample_actual_csv().decode())))
    rows[1][rows[0].index("document_ref")] = "synthetic invoice\nsecond line"
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(rows[0])
    writer.writerow([])
    writer.writerow(rows[1])
    record = read_actual_csv(output.getvalue().encode(), "test.csv")["records"][0]
    assert record["source"].endswith("CSV:row3:4")
    assert read_actual_csv(sample_actual_csv(), "test.csv")["records"][0]["source"].endswith("CSV:row2")


def test_document_citations_survive_restart_without_persisting_document_text(dataset):
    from ops_team.supra.ingestion import read_document
    path = SOURCE.parent/"Phuong_An_Toi_Uu_Chi_Phi_Supra_Phu_Tho.docx"
    service = SupraService(dataset, read_document(path.read_bytes(), path.name))
    store = EventStore()
    store.save_snapshot(service.dataset)
    # Same files re-read at another import time do not conflict on version.
    reread = SupraService(read_workbook(SOURCE.read_bytes(), SOURCE.name), read_document(path.read_bytes(), path.name))
    store.save_snapshot(reread.dataset)
    restored = SupraService(store.load_snapshot(READER, service.dataset["version"]))
    assert all(r["sources"] for r in restored.reconciliation(READER))
    assert restored.dataset["version"] == service.dataset["version"]
    assert "records" not in restored.dataset and "Đã phê duyệt" not in json.dumps(restored.dataset, ensure_ascii=False, default=str)


def test_incident_dedup_across_source_versions_and_audited_fact_updates(dataset):
    store, service = EventStore(), SupraService(dataset)
    workflow = ShadowWorkflow(store)
    item = detect_incidents(dataset, service.query_pnl(READER), service.query_pnl(READER, mode="ACTUAL"))[0]
    workflow.register(AI, item, "INCIDENT")
    reread = {**item, "source_version": "new-source-same-financial-facts", "detected_at": "later"}
    workflow.register(AI, reread, "INCIDENT")
    assert len(store.events()) == 1
    changed = {**reread, "fact": "different measured fact"}
    workflow.register(AI, changed, "INCIDENT")
    assert len(store.events()) == 2 and workflow.state(READER, item["id"])["fact"] == changed["fact"]
    assert store.events()[-1]["kind"] == "INCIDENT_UPDATE" and store.verify_chain()
