from pathlib import Path
from decimal import Decimal as D
import json
import pytest
from ops_team.chief.demo import demo_tables
from ops_team.chief.engine import evaluate
from ops_team.chief.agent import metric_context
from ops_team.telegram_agent import TelegramAgent
from ops_team.telegram_assistant import AssistantResponder
from ops_team.telegram_bridge import Incoming
from ops_team.telegram_state import BusinessState, GROUP_ID
from ops_team.schema import AGENT_NAMES
from ops_team.providers import ModelConfig, FREE_MODELS


def profiles():
    return {n: ModelConfig("Groq", FREE_MODELS[0], "fake-chief-key", free_pilot=True, free_account_confirmed=True) for n in AGENT_NAMES}


def response():
    return dict(summary="Cần kiểm tra chứng từ trước khi chốt nhận định.", observations=[], hypotheses=[], proposals=[],
        requests=[], disagreements=[], selected_agents=[], audit_decisions=[])


def test_ui_demo_load_report_four_sections_and_live_review_requires_explicit_consent():
    from streamlit.testing.v1 import AppTest
    root = Path(__file__).resolve().parents[1]
    at = AppTest.from_file(str(root/"app.py"), default_timeout=30).run()
    assert not at.exception
    next(b for b in at.button if b.key == "chief_demo").click().run()
    assert not at.exception
    report = next(m.value for m in at.markdown if "### 1. Báo cáo nhanh" in m.value)
    assert "### 4. Đúng 3 việc" in report and "69.747" in report
    assert "### 2. Bảng số liệu đã tính\n\n| Chỉ tiêu |" in report
    assert next(b for b in at.button if b.key == "chief_ai").disabled
    assert at.session_state["chief_data"].label.startswith("SYNTHETIC")


def test_telegram_ask_evaluation_routes_chief_and_auditor_without_exposing_order_codes():
    seen = []
    def fake(config, payload, **kwargs):
        seen.append(payload)
        answer = response()
        if payload["agent"] == "Auditor":
            answer["audit_decisions"] = [{"task_id": payload["review_tasks"][0]["task_id"], "verdict": "unresolved", "reason": "Chưa có chứng từ đã đối soát.", "evidence_ids": ["chief.gp"]}]
        return answer, "fake-model"
    result = evaluate(demo_tables())
    ctx = dict(label="SYNTHETIC PLAN UNVERIFIED", scope=result["scope"], metrics=metric_context(result), chief_result=result)
    agent = TelegramAgent(profiles(), ctx, transport=fake)
    request = Incoming(GROUP_ID, 123, 1, None, "ask", "Đánh giá P&L và các điểm chưa được")
    report = agent.answer(request)
    assert [p["agent"] for p in seen] == ["Coordinator", "Auditor"]
    assert all(title in report for title in ("1. Báo cáo nhanh", "2. Bảng số liệu", "3. Các điểm CHƯA ĐƯỢC", "4. Đúng 3 việc"))
    assert "69.747" in report and "nhận định chưa được chấp nhận" in report
    assert report.startswith("DỮ LIỆU GIẢ / DEMO")
    assert "CHIEF-" not in report and "CHIEF-" in agent.details(request)
    assert "review_tasks" not in seen[0] and len(seen[1]["review_tasks"]) == 1
    agent.answer(Incoming(GROUP_ID, 123, 2, None, "operations", "Hỏi chuyên viên hỗ trợ"))
    assert seen[-1]["agent"] == "Operations"  # Eight-role routing remains available.


def test_financial_import_is_encrypted_and_only_explicit_source_selection_changes_scope(tmp_path):
    from ops_team.telegram_sources import import_file, load_source
    state = BusinessState(tmp_path)
    try:
        path = Path(__file__).resolve().parents[1]/"outputs/chief-auditor/SYNTHETIC_CHIEF_INPUT.xlsx"
        req = Incoming(GROUP_ID, 123, 1, None, "import", "chief")
        identifier, data = import_file(state, req, path.name, path.read_bytes(), "chief")
        assert "source_id" not in state.facts() and not state.control("schedule_enabled", False)
        loaded, source = load_source(state, identifier, {})
        assert evaluate(loaded.tables)["totals"]["gp"] == 69747
        assert all(b"SYNTHETIC-S1" not in p.read_bytes() for p in tmp_path.rglob("*.dpapi"))
        facts = dict(source_id=identifier, customer="SYNTHETIC", warehouses="SYNTHETIC-DC", period="2026-10", cutoff="23:59", report_time="13:00", timezone="Asia/Ho_Chi_Minh", goals="margin", contacts="synthetic owner")
        for key, value in facts.items():
            state.remember(key, value, 123, 1, GROUP_ID)
        assistant = AssistantResponder(state, {"owner_id": 123, "models": {}, "bot_token": "not-used"})
        assert not assistant.plan()[2]
        report = assistant.report()
        assert "69.747" in report and "UNVERIFIED" in report and "SYNTHETIC" in report
        assert not state.control("schedule_enabled", False)
        assert "chủ sở hữu" in assistant.answer(Incoming(GROUP_ID, 456, 2, None, "details", ""))
    finally:
        state.close()


def test_chief_real_provider_adapter_respects_existing_free_caps_with_fake_http(monkeypatch):
    import ops_team.providers as provider
    from ops_team.limits import estimate_tokens
    from types import SimpleNamespace
    import ops_team.telegram_agent as telegram
    clock = [1000.0]
    fake_time = SimpleNamespace(monotonic=lambda: clock[0])
    monkeypatch.setattr(provider, "time", fake_time)
    monkeypatch.setattr(telegram, "time", fake_time)
    bodies = []
    async def fetch(url, headers, body, timeout):
        assert estimate_tokens(body) <= 7500 and body["max_completion_tokens"] == 1500
        payload = json.loads(body["messages"][0]["content"])
        bodies.append(payload)
        output = response()
        if payload["agent"] == "Auditor":
            output["audit_decisions"] = [{"task_id": payload["review_tasks"][0]["task_id"], "verdict": "accept", "reason": "Chỉ chấp nhận diễn giải số code, chưa đối soát.", "evidence_ids": ["chief.revenue"]}]
        return 200, json.dumps({"model": "fake-model", "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(output)}}]}).encode(), None
    monkeypatch.setattr(provider, "_fetch_async", fetch)
    result = evaluate(demo_tables())
    agent = TelegramAgent(profiles(), dict(label="SYNTHETIC PLAN", scope=result["scope"], metrics=metric_context(result), chief_result=result))
    agent._budget.clock = fake_time.monotonic
    agent._budget.sleep = lambda seconds: clock.__setitem__(0, clock[0]+seconds)
    text = agent.answer(Incoming(GROUP_ID, 123, 1, None, "chief", "Thẩm định tổng hợp"))
    assert len(bodies) == 2 and agent._budget.attempts == 2 and "69.747" in text


def test_chief_ui_reuses_minute_window_across_reviews_instead_of_resetting_quota(monkeypatch):
    from ops_team.chief import ui
    from types import SimpleNamespace
    session = SimpleNamespace()
    class Session(dict):
        __getattr__ = dict.__getitem__
        __setattr__ = dict.__setitem__
    monkeypatch.setattr(ui.st, "session_state", Session())
    first = ui._review_agent(profiles(), {})
    second = ui._review_agent(profiles(), {})
    tick = [1000.0]
    for agent in (first, second):
        agent._budget.clock = lambda: tick[0]
        agent._budget.sleep = lambda seconds: tick.__setitem__(0, tick[0]+seconds)
        assert agent._budget.max_calls == 24 and agent._budget.max_tokens == 60000
    first._budget.acquire(7400, 1600)
    second._budget.acquire(200, 1600)
    assert tick[0] >= 1060 and second._budget.attempts == 1


@pytest.mark.parametrize("text,expected", [("PNL", True), ("Lãi lỗ tháng này", True), ("P&L", True), ("Chào bạn", False)])
def test_chief_routes_financial_evaluation_phrases_only(text, expected):
    from ops_team.chief.agent import is_evaluation
    assert is_evaluation(text) is expected


def test_booking_chief_intake_uses_code_without_spending_provider_budget(monkeypatch):
    import ops_team.providers as provider
    import ops_team.telegram_agent as telegram
    from ops_team.limits import estimate_tokens
    from types import SimpleNamespace
    tick, calls = [1000.0], []
    clock = SimpleNamespace(monotonic=lambda: tick[0])
    monkeypatch.setattr(provider, "time", clock)
    monkeypatch.setattr(telegram, "time", clock)
    async def fetch(url, headers, body, timeout):
        assert estimate_tokens(body) <= 7500 and body["max_completion_tokens"] == 1500
        payload = json.loads(body["messages"][0]["content"])
        calls.append(payload)
        out = response()
        out["summary"] = "Có thể tiết kiệm 5% chi phí. Cần bổ sung cước và chi phí gốc."
        if payload["agent"] == "Auditor":
            out["audit_decisions"] = [{"task_id": payload["review_tasks"][0]["task_id"], "verdict": "unresolved", "reason": "Chưa có chứng từ 1000 VND để đối soát.", "evidence_ids": ["chief.revenue"]}]
        return 200, json.dumps({"model": "fake-model", "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(out)}}]}).encode(), None
    monkeypatch.setattr(provider, "_fetch_async", fetch)
    ctx = dict(label="BOOKING OWNER SOURCE", scope="synthetic DC / 2026-10", booking=True,
        metrics={"booking.rows": dict(value=3, unit="dòng", period="2026-10", sources=["synthetic.csv::row 2"]),
                 "booking.unique_orders": dict(value=2, unit="mã GHN", sources=["synthetic.csv::row 2"])},
        source_provenance=dict(retrieved_at="2026-10-25T12:50:00+07:00", latest_dispatch_date="2026-10-10"))
    agent = TelegramAgent(profiles(), ctx)
    agent._budget.clock = clock.monotonic
    agent._budget.sleep = lambda seconds: tick.__setitem__(0, tick[0]+seconds)
    text = agent.answer(Incoming(GROUP_ID, 123, 1, None, "ask", "Đánh giá lãi lỗ"))
    assert not calls and agent._budget.attempts == 0
    assert "Tổng doanh thu B2B: N/A" in text and "PARTIAL" in text and "VÀNG" in text
    assert "5%" not in text and "1000 VND" not in text and "cước" in text
    assert "chưa chạy Chief AI/Auditor (0 HTTP)" in text
    assert agent.last_presentation["audit"] is None and agent.last_presentation["answer"] is None
