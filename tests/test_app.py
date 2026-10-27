from pathlib import Path
from streamlit.testing.v1 import AppTest
from ops_team.demo import workbook_bytes, demo_tables

ROOT = Path(__file__).resolve().parents[1]


def button(at, label):
    return next(b for b in at.button if b.label == label)


def test_demo_ui_to_report():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not at.exception
    button(at, "Nạp dữ liệu demo").click().run()
    assert not at.exception
    assert len(at.metric) == 6
    assert at.metric[0].value == "411,00 triệu ₫"
    button(at, "Chạy đội agent").click().run()
    assert not at.exception
    assert at.session_state["run"].audit["review_complete"]
    assert any("## 6." in m.value for m in at.markdown)
    # Stale results must disappear after changing scope/period/question/mode.
    at.text_area(key="question").set_value("SLA ra sao?").run()
    assert "run" not in at.session_state


def test_live_gate_and_session_clear():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    button(at, "Nạp dữ liệu demo").click().run()
    at.radio(key="mode").set_value("Live").run()
    assert not at.exception
    assert button(at, "Chạy đội agent").disabled
    assert len([t for t in at.text_input if t.key.startswith("group_key_")]) == 3
    button(at, "Xóa dữ liệu & khóa phiên").click().run()
    assert not at.exception
    assert at.session_state["tables"] == {}
    assert not [t for t in at.text_input if t.key.startswith("group_key_")]
    assert all(not t.value for t in at.text_input if t.key == "telegram_bot_token")


def test_excel_upload_mapping_analysis_report():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    at.file_uploader[0].set_value(("DU_LIEU_DEMO.xlsx", workbook_bytes(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")).run()
    assert not at.exception
    button(at, "Kiểm tra bản nhập").click().run()
    button(at, "Xác nhận nhập dữ liệu").click().run()
    assert not at.exception
    assert at.metric[0].value == "411,00 triệu ₫"
    assert at.session_state["data_label"] == "DỮ LIỆU DEMO"
    button(at, "Chạy đội agent").click().run()
    assert not at.exception
    assert at.session_state["run"].audit["review_complete"]
    assert any("## 6." in m.value for m in at.markdown)


def test_csv_upload_column_mapping_and_no_silent_demo():
    tables = demo_tables()
    files = []
    for kind, df in tables.items():
        if kind == "costs":
            df = df.rename(columns={"amount_vnd": "Số tiền"})
        files.append((f"DEMO_{kind}.csv", df.to_csv(index=False).encode("utf-8-sig"), "text/csv"))
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    at.file_uploader[0].set_value(files).run()
    assert not at.exception
    # Auto-detected group, explicitly map a non-standard Vietnamese column.
    missing_amount = next(w for w in at.selectbox if w.label == "amount_vnd")
    missing_amount.set_value("Số tiền").run()
    button(at, "Kiểm tra bản nhập").click().run()
    button(at, "Xác nhận nhập dữ liệu").click().run()
    assert not at.exception
    assert at.metric[0].value == "411,00 triệu ₫"
    assert len(at.session_state["tables"]) == 6


def test_single_period_ui_and_analysis():
    tables = {k: df[df.period.eq("2026-09")].copy() for k, df in demo_tables().items()}
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    at.file_uploader[0].set_value(("DEMO_one_period.xlsx", workbook_bytes(tables), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")).run()
    button(at, "Kiểm tra bản nhập").click().run()
    button(at, "Xác nhận nhập dữ liệu").click().run()
    assert not at.exception and len(at.metric) == 6
    assert at.selectbox(key="previous").value is None
    button(at, "Chạy đội agent").click().run()
    assert not at.exception and at.session_state["run"].previous is None


def test_whatif_review_and_changed_assumptions_reset_decision():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    button(at, "Nạp dữ liệu demo").click().run()
    at.number_input(key="scenario_rate_labor").set_value(5).run()
    at.text_input(key="scenario_note_labor").set_value("Dự kiến giảm OT sau kiểm tra lịch ca").run()
    next(c for c in at.checkbox if c.label.startswith("Tôi xác nhận giả định what-if")).check().run()
    button(at, "Đưa what-if vào phiên phân tích").click().run()
    button(at, "Chạy đội agent").click().run()
    next(s for s in at.selectbox if s.label == "Cân ca / OT").set_value("Duyệt phạm vi thử nghiệm")
    next(t for t in at.text_input if t.label == "Ghi chú / điều kiện duyệt").set_value("Theo dõi SLA mỗi ngày")
    button(at, "Ghi nhận xem xét trong phiên").click().run()
    assert not at.exception
    assert at.session_state["run"].reviews["scenario_labor"]["status"] == "Duyệt phạm vi thử nghiệm"
    at.number_input(key="scenario_rate_labor").set_value(8).run()
    button(at, "Đưa what-if vào phiên phân tích").click().run()
    assert not at.exception
    assert "run" not in at.session_state
    button(at, "Chạy đội agent").click().run()
    assert at.session_state["run"].reviews == {}
    assert next(s for s in at.selectbox if s.label == "Cân ca / OT").value == "Chờ xem xét"


def test_live_profiles_override_and_fake_transport(monkeypatch):
    import ops_team.orchestrator as engine
    from ops_team.agents import simulated
    def transport(config, payload, timeout):
        out = simulated(payload["agent"], payload["task"], payload["metrics"], 2)
        out["disagreements"], out["requests"] = [], []
        if payload["phase"] == "plan":
            out["selected_agents"] = ["Accounting", "Cost", "Operations", "Workforce", "Fleet", "KPI"]
        if payload["agent"] == "Auditor":
            out["audit_decisions"] = [{"task_id": r["task_id"], "verdict": "accept", "reason": "Đã xét bản hiện tại", "evidence_ids": ["cost"]} for r in payload["review_tasks"]]
        return out, "actual-"+payload["agent"]
    monkeypatch.setattr(engine, "call_model", transport)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    button(at, "Nạp dữ liệu demo").click().run()
    at.radio(key="mode").set_value("Live").run()
    for profile in ("coord", "specialist", "audit"):
        at.text_input(key="group_model_"+profile).set_value("fake-"+profile)
        at.text_input(key="group_key_"+profile).set_value("runtime-test-key")
    at.run()
    at.checkbox(key="override_Fleet").check().run()
    at.selectbox(key="provider_Fleet").set_value("Ollama (local)").run()
    at.text_input(key="model_Fleet").set_value("fake-local").run()
    next(c for c in at.checkbox if c.label.startswith("Tôi chủ động bật Live")).check().run()
    assert not at.exception and not button(at, "Chạy đội agent").disabled
    button(at, "Chạy đội agent").click().run()
    assert not at.exception
    result = at.session_state["run"]
    assert result.mode == "Live" and result.calls == 9
    assert {e.receiver for e in result.exchanges} == set(("Coordinator", "Accounting", "Cost", "Operations", "Workforce", "Fleet", "KPI", "Auditor"))
    assert next(e for e in result.exchanges if e.receiver == "Fleet").provider == "Ollama (local)"
    assert all("runtime-test-key" not in m.value for m in at.markdown)


def test_import_vietnamese_numbers_and_confirmed_unit_constant():
    tables = demo_tables()
    tables["costs"] = tables["costs"].drop(columns=["currency"])
    tables["costs"]["amount_vnd"] = tables["costs"].amount_vnd.map(lambda value: f"{value:,.0f}".replace(",", "."))
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    at.file_uploader[0].set_value(("DEMO_vi.xlsx", workbook_bytes(tables), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")).run()
    at.selectbox(key="import_number_format").set_value("vi").run()
    next(s for s in at.selectbox if s.label == "currency" and "_costs_currency" in s.key).set_value("Hằng số đã xác nhận: VND").run()
    assert button(at, "Kiểm tra bản nhập").disabled
    next(c for c in at.checkbox if c.label.startswith("Tôi xác nhận các hằng số")).check().run()
    button(at, "Kiểm tra bản nhập").click().run()
    button(at, "Xác nhận nhập dữ liệu").click().run()
    assert not at.exception
    assert at.metric[0].value == "411,00 triệu ₫" and at.session_state["numeric_format"] == "vi"
