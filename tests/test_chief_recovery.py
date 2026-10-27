"""Real provider adapter, fake HTTP/Telegram; no production state or network."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import httpx
import pytest
from ops_team.chief.agent import metric_context
from ops_team.chief.demo import demo_tables
from ops_team.chief.engine import evaluate
from ops_team.limits import ProviderError, RequestBudget, TelegramBudget
from ops_team.providers import ModelConfig, FREE_MODELS, call_model
from ops_team.schema import AGENT_NAMES
from ops_team.telegram_agent import TelegramAgent
from ops_team.telegram_assistant import AssistantResponder, DurableBudget, TZ
from ops_team.telegram_bridge import TelegramBridge, Incoming
from ops_team.telegram_state import BusinessState, GROUP_ID

QUESTION = "Thẩm định nguồn hiện tại và cho tôi biết cần bổ sung dữ liệu gì để tính P&L."
TOKEN = "123456789:"+"z"*35
BODY_SENTINEL = "PRIVATE_RESPONSE_BODY_DO_NOT_STORE"


class Clock:
    def __init__(self):
        self.tick, self.wall = 1000.0, time.time()
    def monotonic(self):
        return self.tick
    def time(self):
        return self.wall+self.tick-1000
    def sleep(self, seconds):
        self.tick += seconds


@pytest.fixture
def clock(monkeypatch):
    import ops_team.providers as providers
    import ops_team.telegram_agent as telegram
    import ops_team.telegram_assistant as assistant
    value = Clock()
    for module in (providers, telegram, assistant):
        monkeypatch.setattr(module, "time", value)
    return value


def profiles():
    return {n: ModelConfig("Groq", FREE_MODELS[0], "FAKE_CHIEF_RECOVERY_KEY", free_pilot=True, free_account_confirmed=True) for n in AGENT_NAMES}


def output(payload, *, source_issue=False):
    value = dict(summary="Cần đối chiếu chứng từ trước khi duyệt chi phí.", observations=[], hypotheses=[], proposals=[],
        requests=[dict(to="Accounting", question="Anh có chứng từ chi phí theo từng chặng để đối chiếu không?", evidence_ids=[])],
        disagreements=[], selected_agents=[], audit_decisions=[])
    if source_issue:
        value["observations"] = [dict(text="Nhận định chi phí chưa có bằng chứng kèm theo.", evidence_ids=[])]
    if payload["agent"] == "Auditor":
        value["audit_decisions"] = [dict(task_id=payload["review_tasks"][0]["task_id"], verdict="accept",
            reason="Chỉ chấp nhận diễn giải, chưa đối soát chứng từ.", evidence_ids=["chief.revenue"])]
    return value


def success(payload, *, source_issue=False):
    return 200, json.dumps(dict(model="fake-recovery-model", choices=[dict(finish_reason="stop", message=dict(content=json.dumps(output(payload, source_issue=source_issue))))])).encode(), None


def financial_agent(clock):
    result = evaluate(demo_tables())
    agent = TelegramAgent(profiles(), dict(label="SYNTHETIC PLAN", scope=result["scope"], metrics=metric_context(result), chief_result=result))
    agent._budget.clock, agent._budget.sleep = clock.monotonic, clock.sleep
    return agent


def financial_responder(state, clock, monkeypatch):
    from ops_team.telegram_sources import import_file
    path = Path(__file__).resolve().parents[1]/"outputs/chief-auditor/SYNTHETIC_CHIEF_INPUT.xlsx"
    identifier, _ = import_file(state, Incoming(GROUP_ID, 123, 1, None, "import", "chief"), path.name, path.read_bytes(), "chief")
    facts = dict(source_id=identifier, customer="SYNTHETIC", warehouses="SYNTHETIC-DC", period="2026-10", cutoff="23:59",
        report_time="13:00", timezone="Asia/Ho_Chi_Minh", goals="margin", contacts="synthetic owner")
    for key, value in facts.items():
        state.remember(key, value, 123, 1, GROUP_ID)
    state.set_control("worker_enabled", True)
    state.set_control("schedule_enabled", True)
    state.set_control("approved_plan", "synthetic-approved")
    responder = AssistantResponder(state, dict(owner_id=123, bot_token=TOKEN, models={n: asdict(c) for n, c in profiles().items()}))
    original = responder._get_agent
    def get_agent():
        agent = original()
        agent._budget.clock, agent._budget.sleep = clock.monotonic, clock.sleep
        return agent
    monkeypatch.setattr(responder, "_get_agent", get_agent)
    return responder


def booking_responder(state):
    from ops_team.telegram_snapshot import ingest_snapshot
    sheet = "SYNTHETIC_BOOKING_FOR_INTAKE_123"
    area = "'DC PHÚ THỌ'!A1:E100"
    facts = dict(source_id="snapshot", customer="SYNTHETIC", warehouses="DC PHÚ THỌ", period="2026-10", cutoff="23:59",
        report_time="13:00", timezone="Asia/Ho_Chi_Minh", goals="data quality", contacts="synthetic owner",
        sheets_id=sheet, sheets_tab="DC PHÚ THỌ", sheets_gid="0", sheets_ranges=json.dumps(dict(supra_booking=area)))
    for key, value in facts.items():
        state.remember(key, value, 123, 1, GROUP_ID)
    ingest_snapshot(dict(version=1, source="GOOGLE_SHEETS_CONNECTOR", spreadsheet_id=sheet, tab="DC PHÚ THỌ", gid=0, range=area,
        retrieved_at=datetime.now(timezone.utc).isoformat(), headers=["Ngày LTC", "Mã đơn GHN", "Trạng thái", "Ngày xuất"],
        rows=[["10/10/2026", "SYNTHETIC001", "delivered", "10/10/2026"], ["10/10/2026", "SYNTHETIC001", "delivering", "10/10/2026"]]), state, 123)
    state.set_control("worker_enabled", True)
    state.set_control("schedule_enabled", True)
    state.set_control("approved_plan", "synthetic-approved")
    return AssistantResponder(state, dict(owner_id=123, bot_token=TOKEN, models={n: asdict(c) for n, c in profiles().items()}))


class FakeTelegram:
    _token = TOKEN
    def __init__(self):
        self.sent = []
    def call(self, method, **params):
        assert method == "sendMessage"
        self.sent.append(params)
        return dict(message_id=len(self.sent))


def bridge(state, responder, clock):
    state.set_control("paired", dict(chat_id=GROUP_ID, owner_id=123))
    api = FakeTelegram()
    value = TelegramBridge(api, responder, clock=clock.time, expected_chat=GROUP_ID, owner_id=123, state=state)
    value._username, value._bot_id = "aisupramb03_bot", 99
    value._stop.wait = lambda seconds: value._stop.is_set()
    return value, api


def update(clock, mid):
    return dict(update_id=mid, message=dict(message_id=mid, date=int(clock.time()), chat=dict(id=GROUP_ID, type="supergroup"),
        **{"from": dict(id=123, is_bot=False)}, text="/chief@aisupramb03_bot "+QUESTION))


def sources(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*.dpapi")}


def assert_code_numbers(text):
    assert "85.000" in text and "15.253" in text and "69.747" in text
    assert all(title in text for title in ("1. Báo cáo nhanh", "2. Bảng số liệu", "3. Các điểm CHƯA ĐƯỢC", "4. Đúng 3 việc"))
    assert "UNVERIFIED" in text or "chưa đối soát" in text


def test_real_user_intake_delivered_by_bridge_without_any_provider_and_no_quota_changes(tmp_path, clock, monkeypatch):
    import ops_team.providers as providers
    from ops_team.telegram_bridge import TelegramAPI
    async def forbidden(*args, **kwargs):
        pytest.fail("Booking intake must use zero provider HTTP")
    monkeypatch.setattr(providers, "_fetch_async", forbidden)
    monkeypatch.setattr(TelegramAPI, "call", lambda *a, **k: pytest.fail("No real Telegram API"))
    state = BusinessState(tmp_path)
    try:
        responder = booking_responder(state)
        usage = dict(day=datetime.now(TZ).date().isoformat(), attempts=7, tokens=12000, window=[])
        state.set_control("provider_usage", usage)
        state.set_control("provider_cooldown", dict(status=429, until=clock.time()+3600, failures=1, confirmed=True))
        worker, api = bridge(state, responder, clock)
        original_facts, original_sources = deepcopy(state.facts()), sources(tmp_path)
        original_settings = deepcopy(responder.settings)
        for mid in (10, 11, 12):
            start = len(api.sent)
            worker.process(update(clock, mid))
            text = "\n".join(p["text"] for p in api.sent[start:])
            assert text.startswith("BOOKING — PARTIAL / UNVERIFIED") and len(text)<2200
            assert "Tổng doanh thu B2B: N/A" in text and "BƯỚC TIẾP:" in text and "0 HTTP" in text
            assert "chưa chạy Chief AI/Auditor" in text and "API trả HTTP 429" not in text
            assert state.delivery_state(f"update:{mid}") == "SENT"
        detail = responder.answer(Incoming(GROUP_ID, 123, 20, None, "details", ""))
        for phrase in ("g/kg", "ngày hiệu lực", "lương", "ba chặng", "B2B/B2C", "Bước tiếp theo:"):
            assert phrase in detail
        assert "CHIEF-CODE-" in detail and "HTTP attempts cho yêu cầu này: 0" in detail
        assert "fake-recovery-model" not in detail
        assert state.control("provider_usage") == usage
        assert state.facts() == original_facts and sources(tmp_path) == original_sources and responder.settings == original_settings
        assert state.control("schedule_enabled") and state.control("approved_plan") == "synthetic-approved"
    finally:
        state.close()


@pytest.mark.parametrize("failed_phase", ["Coordinator", "Auditor"])
def test_429_preserves_report_partial_reply_and_recovers_next_turn_without_daily_reset(tmp_path, clock, monkeypatch, failed_phase):
    import ops_team.providers as providers
    calls = []
    failed = [False]
    async def fetch(url, headers, body, timeout):
        payload = json.loads(body["messages"][0]["content"])
        calls.append(payload)
        if payload["agent"] == failed_phase and not failed[0]:
            failed[0] = True
            return 429, BODY_SENTINEL.encode(), "120"
        return success(payload, source_issue=not failed[0] and failed_phase == "Auditor")
    monkeypatch.setattr(providers, "_fetch_async", fetch)
    state = BusinessState(tmp_path)
    try:
        responder = financial_responder(state, clock, monkeypatch)
        worker, api = bridge(state, responder, clock)
        original_sources, original_facts = sources(tmp_path), deepcopy(state.facts())
        worker.process(update(clock, 10))
        text = "\n".join(p["text"] for p in api.sent)
        assert_code_numbers(text)
        assert "UNRESOLVED" in text and "chưa có quyết định Auditor" in text
        agent, record = responder._agent, deepcopy(responder._agent.last_presentation)
        initial_attempts = 1 if failed_phase == "Coordinator" else 2
        assert len(calls) == initial_attempts == agent._budget.attempts == record["http_attempts"]
        assert record["audit"]["audit_answer"] is None and record["audit"]["audit_decisions"] == []
        assert not record["audit"]["review_complete"]
        detail = responder.answer(Incoming(GROUP_ID, 123, 11, None, "details", ""))
        if failed_phase == "Auditor":
            assert record["answer"] is not None and record["audit"]["actual_models"] == ["fake-recovery-model"]
            assert "chứng từ chi phí theo từng chặng" in text and record["audit"]["source_issues"]
            assert record["audit"]["task_id"] in detail and "Chief đã trả lời" in detail
        else:
            assert record["answer"] is None and record["audit"]["actual_models"] == []
        usage = deepcopy(state.control("provider_usage"))
        assert usage["attempts"] == initial_attempts
        clock.sleep(30)
        responder.answer(Incoming(GROUP_ID, 123, 12, None, "chief", QUESTION))
        assert len(calls) == initial_attempts and state.control("provider_usage") == usage
        assert agent.last_presentation["http_attempts"] == 0 and agent.last_presentation["audit"]["actual_models"] == []
        clock.sleep(91)
        recovered = responder.answer(Incoming(GROUP_ID, 123, 13, None, "chief", QUESTION))
        assert_code_numbers(recovered)
        assert responder._agent is agent and agent._budget.attempts == initial_attempts+2
        assert state.control("provider_usage")["tokens"] > usage["tokens"] and state.control("provider_usage")["attempts"] == initial_attempts+2
        assert agent.last_presentation["audit"]["review_complete"] and not agent._budget.halted
        assert state.control("provider_cooldown") is None
        assert sources(tmp_path) == original_sources and state.facts() == original_facts and state.control("schedule_enabled")
        assert BODY_SENTINEL not in json.dumps(record, default=str) and BODY_SENTINEL not in json.dumps(state.control("provider_cooldown"))
    finally:
        state.close()


@pytest.mark.parametrize("phase", ["Coordinator", "Auditor"])
def test_timeout_keeps_code_and_any_completed_chief_without_sticky_quota(phase, clock, monkeypatch):
    import ops_team.providers as providers
    calls, failed = [], [False]
    async def fetch(url, headers, body, timeout):
        payload = json.loads(body["messages"][0]["content"])
        calls.append(payload)
        if payload["agent"] == phase and not failed[0]:
            failed[0] = True
            raise httpx.ReadTimeout(BODY_SENTINEL)
        return success(payload)
    monkeypatch.setattr(providers, "_fetch_async", fetch)
    agent = financial_agent(clock)
    request = Incoming(GROUP_ID, 123, 1, None, "chief", QUESTION)
    first = agent.answer(request)
    assert_code_numbers(first)
    assert agent.last_presentation["audit"]["failure"]["stage"] == ("Chief" if phase == "Coordinator" else "Auditor")
    assert not agent.last_presentation["audit"]["review_complete"] and not agent._budget.halted
    assert (agent.last_presentation["answer"] is not None) is (phase == "Auditor")
    before = agent._budget.attempts
    assert_code_numbers(agent.answer(request))
    assert agent._budget.attempts == before+2 and agent.last_presentation["audit"]["review_complete"]


@pytest.mark.parametrize("status", [401, 403])
def test_auth_denial_does_not_auto_recover_as_rate_cooldown(status, clock, monkeypatch):
    import ops_team.providers as providers
    calls = []
    async def fetch(*args):
        calls.append(1)
        return status, BODY_SENTINEL.encode(), "1"
    monkeypatch.setattr(providers, "_fetch_async", fetch)
    agent = financial_agent(clock)
    request = Incoming(GROUP_ID, 123, 1, None, "chief", QUESTION)
    assert_code_numbers(agent.answer(request))
    tokens = agent._budget.reserved_tokens
    clock.sleep(86401)
    second = agent.answer(request)
    assert_code_numbers(second)
    assert len(calls) == 1 and agent._budget.reserved_tokens == tokens and agent._budget.halt_status == status
    assert agent._budget.retry_until is None and agent.last_presentation["http_attempts"] == 0
    assert agent.last_presentation["audit"]["failure"]["status_code"] == status
    assert "quota provider đã phục hồi" not in second and BODY_SENTINEL not in second


@pytest.mark.parametrize("header", [None, "garbage", "NaN", "Infinity", "-1", "99999"])
def test_unknown_retry_after_uses_bounded_user_turn_backoff_without_probe(header, clock, monkeypatch):
    import ops_team.providers as providers
    calls = []
    async def fetch(*args):
        calls.append(1)
        return 429, BODY_SENTINEL.encode(), header
    monkeypatch.setattr(providers, "_fetch_async", fetch)
    agent = financial_agent(clock)
    request = Incoming(GROUP_ID, 123, 1, None, "chief", QUESTION)
    first = agent.answer(request)
    assert_code_numbers(first)
    assert len(calls) == 1 and agent._budget.retry_until == 1060
    assert not agent._budget.retry_confirmed and "chưa xác nhận quota provider đã phục hồi" in first
    clock.sleep(59)
    agent.answer(request)
    assert len(calls) == 1
    clock.sleep(2)
    agent.answer(request)
    assert len(calls) == 2 and agent._budget.retry_until == 1181
    clock.sleep(61)
    agent.answer(request)
    assert len(calls) == 2
    clock.sleep(60)
    agent.answer(request)
    assert len(calls) == 3 and agent._budget.attempts == 3


def test_default_budget_remains_halted_for_same_run_and_terminal_retry_is_bounded(clock, monkeypatch):
    import ops_team.providers as providers
    calls = []
    async def fetch(*args):
        calls.append(1)
        return 429, BODY_SENTINEL.encode(), "0"
    monkeypatch.setattr(providers, "_fetch_async", fetch)
    budget = RequestBudget(tokens_per_minute=50000, clock=clock.monotonic, sleep=clock.sleep)
    with pytest.raises(ProviderError, match="429"):
        call_model(profiles()["Coordinator"], dict(metrics={}), budget=budget, deadline=1600)
    assert len(calls) == budget.attempts == 2 and budget.halted
    clock.sleep(3600)
    with pytest.raises(ProviderError, match="Đã dừng"):
        call_model(profiles()["Coordinator"], dict(metrics={}), budget=budget, deadline=clock.monotonic()+600)
    assert len(calls) == 2


def test_durable_cooldown_restores_and_expires_without_clearing_daily_reservations(tmp_path, clock):
    state = BusinessState(tmp_path)
    try:
        first = DurableBudget(state)
        first.clock, first.sleep = clock.monotonic, clock.sleep
        first.acquire(100, 1600)
        first.provider_failed(429, 3600)
        saved = deepcopy(state.control("provider_usage"))
        second = DurableBudget(state)
        second.clock, second.sleep = clock.monotonic, clock.sleep
        second.begin_turn()
        with pytest.raises(ProviderError, match="429"):
            second.acquire(100, clock.monotonic()+600)
        assert state.control("provider_usage") == saved
        clock.sleep(3601)
        second.begin_turn()
        second.acquire(100, clock.monotonic()+600)
        assert second.attempts == 2 and second.reserved_tokens == 200
        assert state.control("provider_usage")["tokens"] == 200 and state.control("provider_cooldown") is None
        with pytest.raises(ValueError):
            state.set_control("provider_cooldown", dict(status=429, body=BODY_SENTINEL))
    finally:
        state.close()


@pytest.mark.parametrize("missing_key", [False, True])
def test_no_model_or_missing_key_keeps_financial_code_report_and_fresh_details(tmp_path, clock, monkeypatch, missing_key):
    import ops_team.providers as providers
    async def forbidden(*args):
        pytest.fail("No credential means no HTTP")
    monkeypatch.setattr(providers, "_fetch_async", forbidden)
    state = BusinessState(tmp_path)
    try:
        responder = financial_responder(state, clock, monkeypatch)
        if missing_key:
            for c in responder.settings["models"].values():
                c["api_key"] = ""
        else:
            responder.settings["models"] = {}
        text = responder.answer(Incoming(GROUP_ID, 123, 1, None, "chief", QUESTION))
        assert_code_numbers(text)
        assert "0 HTTP" in text
        detail = responder.answer(Incoming(GROUP_ID, 123, 2, None, "details", ""))
        assert "HTTP attempts cho yêu cầu này: 0" in detail and "CHIEF-CODE-" in detail
        assert "fake-recovery-model" not in detail
        assert_code_numbers(responder.answer(Incoming(GROUP_ID, 123, 3, None, "run", "")))
    finally:
        state.close()


def test_repeated_booking_intake_does_not_consume_model_question_limit(clock, monkeypatch):
    import ops_team.providers as providers
    from ops_team.chief.report import booking_review
    async def forbidden(*args):
        pytest.fail("Repeated intake must not call a provider")
    monkeypatch.setattr(providers, "_fetch_async", forbidden)
    context = dict(label="BOOKING / UNVERIFIED", scope="SYNTHETIC DC / 2026-10", booking=True,
        source_provenance=dict(retrieved_at="2026-10-26T00:00:00+07:00", latest_dispatch_date="2026-10-10"),
        metrics={"booking.rows": dict(value=2, unit="dòng", period="2026-10", sources=["synthetic.csv::row 2"])})
    agent = TelegramAgent(profiles(), context, max_questions=1)
    agent._budget.clock = clock.monotonic
    agent._budget.provider_failed(429, 3600)
    for mid in range(25):
        text = agent.answer(Incoming(GROUP_ID, 123, mid+1, None, "chief", QUESTION))
        assert "Chưa thể tính P&L" in text and "0 HTTP" in text and "Bước tiếp theo" in text
    assert agent._questions == agent._budget.attempts == agent._budget.reserved_tokens == 0
    assert agent.last_presentation["answer"] is None and agent.last_presentation["audit"] is None


def test_current_source_version_replaces_older_partial_reply_during_cooldown(clock, monkeypatch):
    import ops_team.providers as providers
    calls = []
    async def fetch(url, headers, body, timeout):
        payload = json.loads(body["messages"][0]["content"])
        calls.append(payload)
        if payload["agent"] == "Auditor":
            return 429, BODY_SENTINEL.encode(), "120"
        value = output(payload)
        value["requests"][0]["question"] = "Chứng từ nào còn thiếu ở OLD_VERSION_SENTINEL?"
        return 200, json.dumps(dict(model="fake-recovery-model", choices=[dict(finish_reason="stop", message=dict(content=json.dumps(value)))])).encode(), None
    monkeypatch.setattr(providers, "_fetch_async", fetch)
    agent = financial_agent(clock)
    request = Incoming(GROUP_ID, 123, 1, None, "chief", QUESTION)
    assert "OLD_VERSION_SENTINEL" in agent.answer(request)
    old_version = agent.last_presentation["code_task_id"]
    tables = demo_tables()
    tables["rates"][0]["rateVnd"] = 21000
    result = evaluate(tables)
    agent._context.update(label="SYNTHETIC v2", chief_result=result, metrics=metric_context(result), scope=result["scope"])
    text = agent.answer(request)
    assert "87.000" in text and "71.747" in text and "OLD_VERSION_SENTINEL" not in text
    assert len(calls) == 2 and agent.last_presentation["http_attempts"] == 0
    assert agent.last_presentation["code_task_id"] != old_version
    detail = agent.details(request)
    assert agent.last_presentation["code_task_id"] in detail and "OLD_VERSION_SENTINEL" not in detail
    assert "fake-recovery-model" not in detail


@pytest.mark.parametrize("with_models", [False, True])
def test_reset_removes_current_topic_trace_and_source_change_never_reuses_old_report(tmp_path, clock, monkeypatch, with_models):
    import ops_team.providers as providers
    async def forbidden(*args):
        pytest.fail("Intake/reset/source selection must not spend provider calls")
    monkeypatch.setattr(providers, "_fetch_async", forbidden)
    state = BusinessState(tmp_path)
    try:
        responder = booking_responder(state)
        if not with_models:
            responder.settings["models"] = {}
        usage = dict(day=datetime.now(TZ).date().isoformat(), attempts=5, tokens=9000, window=[])
        state.set_control("provider_usage", usage)
        hashes = sources(tmp_path)
        def request(command, text="", topic=9):
            return Incoming(GROUP_ID, 123, 1, topic, command, text)
        responder.answer(request("chief", QUESTION))
        original = responder._code_details[(GROUP_ID, 9, 123)]["code_task_id"]
        responder.answer(request("chief", QUESTION, topic=10))
        assert "CHIEF-CODE-" in responder.answer(request("details"))
        responder.answer(request("run"))
        assert responder._last_report_record is not None and responder._report_details is not None
        assert "Đã xóa" in responder.answer(request("reset"))
        assert "CHIEF-CODE-" not in responder.answer(request("details"))
        assert responder._report_details is None and responder._last_report_record is None
        assert "CHIEF-CODE-" in responder.answer(request("details", topic=10))
        assert state.control("provider_usage") == usage and sources(tmp_path) == hashes
        assert state.control("schedule_enabled") and state.control("approved_plan") == "synthetic-approved"
        # No models are required for reset; changing selected period clears every old trace.
        responder.answer(request("set", "period 2026-11"))
        assert "CHIEF-CODE-" not in responder.answer(request("details", topic=10))
        responder.answer(request("chief", QUESTION))
        current = responder._code_details[(GROUP_ID, 9, 123)]
        assert current["code_task_id"] != original and "2026-11" in current["context"]["scope"]
        assert state.control("provider_usage") == usage and sources(tmp_path) == hashes
    finally:
        state.close()
