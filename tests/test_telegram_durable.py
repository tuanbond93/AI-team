from dataclasses import asdict
from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import httpx
import pytest
from ops_team.telegram_state import BusinessState, GROUP_ID
from ops_team.telegram_secrets import save_settings, load_settings, save_protected, load_protected
from ops_team.telegram_assistant import AssistantResponder, DurableBudget, TZ
from ops_team.telegram_bridge import TelegramBridge, TelegramError, Incoming
from ops_team.telegram_sources import import_file
from ops_team.telegram_sheets import read_sheets, range_map, SCOPES
from ops_team.telegram_agent import TelegramAgent, warehouse_context
from ops_team.providers import ModelConfig, FREE_MODELS
from ops_team.schema import AGENT_NAMES, SPECS
from ops_team.demo import workbook_bytes, demo_tables
from ops_team.ingestion import validate

TOKEN = "123456789:"+"z"*35
OWNER = 123456
SHEET = "synthetic_sheet_identifier_12345"
ACCOUNT = {"type": "service_account", "client_email": "fake@fake-project.iam.gserviceaccount.com",
           "private_key": "-----BEGIN PRIVATE KEY-----\nFAKE TEST ONLY\n-----END PRIVATE KEY-----\n", "token_uri": "https://oauth2.googleapis.com/token"}


def settings():
    return {"bot_token": TOKEN, "owner_id": OWNER, "chat_id": GROUP_ID, "users": [], "models": {}, "allowed_folder": ""}


def request(command="status", text="", user=OWNER, mid=1):
    return Incoming(GROUP_ID, user, mid, None, command, text)


def response():
    return dict(summary="synthetic", observations=[], hypotheses=[], proposals=[], requests=[], disagreements=[], selected_agents=[], audit_decisions=[])


@pytest.fixture
def state(tmp_path):
    result = BusinessState(tmp_path/"state")
    yield result
    result.close()


def configure(state, source_id):
    for key, value in dict(customer="Synthetic customer", warehouses="Duc Hoa", period="2026-09", cutoff="23:59",
            report_time="13:00", timezone="Asia/Ho_Chi_Minh", goals="Synthetic target", contacts="Synthetic owner", source_id=source_id).items():
        state.remember(key, value, OWNER, 10, GROUP_ID)


def test_dpapi_vault_roundtrip_no_plaintext_and_no_prompt_persistence(tmp_path):
    config = settings()
    save_settings(config, tmp_path)
    encrypted = (tmp_path/"settings.dpapi").read_bytes()
    assert TOKEN.encode() not in encrypted and load_settings(tmp_path) == config
    config["models"] = {"Cost": {"instructions": "private session instructions"}}
    with pytest.raises(ValueError):
        save_settings(config, tmp_path)
    save_protected(tmp_path/"source.dpapi", b"SYNTHETIC FILE CONTENT")
    assert b"SYNTHETIC FILE CONTENT" not in (tmp_path/"source.dpapi").read_bytes()
    assert load_protected(tmp_path/"source.dpapi") == b"SYNTHETIC FILE CONTENT"


def test_memory_provenance_edit_forget_and_config_values_are_encrypted(state):
    assistant = AssistantResponder(state, settings())
    assert "xác nhận" in assistant.answer(request("set", "customer SYNTHETIC_PRIVATE_CUSTOMER", mid=42))
    row = state.facts()["customer"]
    assert row["value"] == "SYNTHETIC_PRIVATE_CUSTOMER" and row["actor"] == OWNER and row["message_id"] == 42
    assistant.answer(request("set", "customer SECOND_PRIVATE_VALUE", mid=43))
    assert state.facts()["customer"]["version"] == 2
    state.set_control("approved_plan", "PRIVATE_PLAN_VALUE")
    stored = repr(state.db.execute("SELECT value FROM memory").fetchall())+str([tuple(r) for r in state.db.execute("SELECT value FROM control")])
    assert "SECOND_PRIVATE_VALUE" not in stored and "PRIVATE_PLAN_VALUE" not in stored
    text = assistant.answer(request("memory"))
    assert "SECOND_PRIVATE_VALUE" in text and "tin #43" in text and "CHƯA ĐỐI SOÁT" in text
    assistant.answer(request("forget", "customer", mid=44))
    assert "customer" not in state.facts() and not state.control("schedule_enabled")
    assert "SECOND_PRIVATE_VALUE" not in str([tuple(r) for r in state.db.execute("SELECT * FROM events")])


def test_only_owner_changes_settings_and_onboarding_without_ai_is_truthful(state):
    assistant = AssistantResponder(state, settings())
    assert "Chỉ owner" in assistant.answer(request("set", "report_time 13:00", user=77))
    assert not state.facts()
    assert "CHƯA CÓ AI" in assistant.answer(request("status"))
    assert "customer" in assistant.answer(request("onboard"))
    assert "Xem lại" in assistant.answer(request("ask", "Synthetic customer", mid=20))
    assert not state.facts()
    assistant.answer(request("confirm", mid=21))
    assert state.facts()["customer"]["message_id"] == 20
    assert "CHƯA CÓ AI" in assistant.answer(request("cost", "What next?"))
    assert "chứa bí mật" in assistant.answer(request("set", "contacts "+TOKEN))


def test_local_setup_provenance_does_not_invent_telegram_message_zero(state):
    state.remember("report_time", "13:00", OWNER, 0, GROUP_ID)
    text = AssistantResponder(state, settings()).answer(request("memory"))
    assert "chủ máy cấu hình tại local" in text and "tin #0" not in text


def test_pinned_owner_pairing_cursor_and_send_receipt_survive_restart(state):
    class API:
        _token = TOKEN
        def __init__(self):
            self.sent = []
        def call(self, method, **kwargs):
            self.sent.append(kwargs)
            return {"message_id": 99}
    class Responder:
        def __init__(self):
            self.calls = 0
        def safe_to_send(self, text, extra_secrets=()):
            return TOKEN not in text
        def answer(self, request):
            self.calls += 1
            return "synthetic response"
    def update(identifier, text, user=OWNER, chat=GROUP_ID):
        return {"update_id": identifier, "message": {"message_id": identifier, "date": 101,
            "chat": {"id": chat, "type": "group"}, "from": {"id": user, "is_bot": False}, "text": text}}
    state.set_control("worker_enabled", True)
    api, responder = API(), Responder()
    bridge = TelegramBridge(api, responder, owner_id=OWNER, state=state, clock=lambda: 100)
    bridge._username, bridge._bot_id = "test_bot", 99
    bridge._stop.wait = lambda s: False
    bridge.process(update(1, bridge.pairing_command, user=77))
    bridge.process(update(2, bridge.pairing_command, chat=-999))
    assert state.control("paired") is None and not api.sent
    bridge.process(update(3, bridge.pairing_command))
    assert state.control("paired") == {"chat_id": GROUP_ID, "owner_id": OWNER}
    bridge.process(update(4, "/ask@test_bot synthetic"))
    assert responder.calls == 1 and state.control("cursor") == 5
    resumed = TelegramBridge(api, responder, owner_id=OWNER, state=state, clock=lambda: 102)
    resumed._username, resumed._bot_id = "test_bot", 99
    resumed._stop.wait = lambda s: False
    resumed.process(update(4, "/ask@test_bot synthetic"))
    assert responder.calls == 1
    other_owner = TelegramBridge(api, responder, owner_id=77, state=state, clock=lambda: 102)
    assert other_owner.snapshot()["chat_id"] is None
    assert state.delivery_state("reply:nonexistent") is None
    assert state.delivery_state("update:4:part:0") == "SENT"


def test_worker_recovers_after_more_than_three_transient_polls_without_duplicate_poller(state):
    class API:
        _token = TOKEN
        def __init__(self):
            self.polls, self.sent = 0, 0
        def call(self, method, **kwargs):
            if method == "getMe":
                return {"id": 99, "is_bot": True, "username": "test_bot"}
            if method == "getWebhookInfo":
                return {"url": ""}
            if method == "getUpdates":
                self.polls += 1
                if self.polls <= 4:
                    raise TelegramError("Synthetic offline", transient=True)
                return [{"update_id": 1, "message": {"date": 101, "message_id": 1,
                    "chat": {"id": GROUP_ID, "type": "group"}, "from": {"id": OWNER, "is_bot": False}, "text": "/status@test_bot"}}]
            if method == "sendMessage":
                self.sent += 1
                bridge.stop()
                return {"message_id": 99}
        def close(self):
            pass
    state.set_control("worker_enabled", True)
    state.set_control("paired", {"chat_id": GROUP_ID, "owner_id": OWNER})
    api = API()
    bridge = TelegramBridge(api, AssistantResponder(state, settings()), owner_id=OWNER, state=state, clock=lambda: 100)
    delays = []
    bridge._backoff = lambda delay: delays.append(delay) or False
    bridge.start()
    bridge._thread.join(timeout=5)
    if bridge.snapshot()["alive"]:
        bridge.stop()
        bridge._thread.join(timeout=2)
    assert not bridge.snapshot()["alive"] and not bridge.snapshot()["error"]
    assert api.polls == 5 and api.sent == 1 and len(delays) == 4 and max(delays) <= 60
    assert state.delivery_state("update:1:part:0") == "SENT"


def test_backoff_checks_durable_owner_stop_without_waiting_whole_delay(state):
    class API:
        _token = TOKEN
    bridge = TelegramBridge(API(), AssistantResponder(state, settings()), owner_id=OWNER, state=state)
    state.set_control("worker_enabled", False)
    started = time.monotonic()
    assert bridge._backoff(60) and time.monotonic()-started < 1


def test_session_reset_preserves_independent_vault_pair_and_worker(isolated_telegram_state):
    from streamlit.testing.v1 import AppTest
    save_settings(settings(), isolated_telegram_state)
    state = BusinessState(isolated_telegram_state)
    state.set_control("paired", {"chat_id": GROUP_ID, "owner_id": OWNER})
    state.set_control("worker_enabled", True)
    state.set_control("schedule_enabled", True)
    try:
        before = (isolated_telegram_state/"settings.dpapi").read_bytes()
        at = AppTest.from_file(str(Path(__file__).resolve().parents[1]/"app.py"), default_timeout=30).run()
        next(b for b in at.button if b.label == "Xóa dữ liệu & khóa phiên").click().run()
        assert not at.exception and at.session_state["tables"] == {}
        assert (isolated_telegram_state/"settings.dpapi").read_bytes() == before
        assert state.control("worker_enabled") is True and state.control("schedule_enabled") is True
        assert state.control("paired") == {"chat_id": GROUP_ID, "owner_id": OWNER}
    finally:
        state.close()


def test_agent_snapshot_changes_on_scope_and_cutoff_with_same_source_hash(state, monkeypatch):
    identifier, _ = import_file(state, request("import"), "DEMO.xlsx", workbook_bytes())
    configure(state, identifier)
    config = settings()
    config["models"] = {n: {k: v for k, v in asdict(ModelConfig("Groq", FREE_MODELS[0], "fake-key",
        free_pilot=True, free_account_confirmed=True)).items() if k != "instructions"} for n in AGENT_NAMES}
    assistant = AssistantResponder(state, config)
    first = assistant._get_agent()
    first._budget.acquire(500, time.monotonic()+2)
    state.remember("warehouses", "HCM", OWNER, 12, GROUP_ID)
    second = assistant._get_agent()
    assert first is not second and "HCM" in second._context["scope"]
    assert second._budget.attempts == 1 and second._budget.reserved_tokens == 500
    state.remember("cutoff", "22:59", OWNER, 13, GROUP_ID)
    third = assistant._get_agent()
    assert second is not third and third._budget.attempts == 1


def test_import_source_validation_encryption_and_no_partial_replacement(state):
    identifier, checked = import_file(state, request("import"), "DEMO.xlsx", workbook_bytes())
    assert sum(len(df) for df in checked.tables.values()) > 0
    record = state.source(identifier)
    blob = state.root/"sources"/(record["digest"]+".dpapi")
    assert blob.read_bytes()[:2] != b"PK"
    with pytest.raises(ValueError):
        import_file(state, request("import"), "bad.csv", b"arbitrary,columns\n1,2", "costs")
    assert len(state.source()) == 1
    configure(state, identifier)
    assistant = AssistantResponder(state, settings())
    report = assistant.report()
    assert "cost" not in report.lower() or "BÁO CÁO" in report
    assert "DỮ LIỆU CŨ" in report and "Ngày quan sát" in report and "CHƯA CÓ DIỄN GIẢI AI" in report
    assert "canonical chưa có customer_id" in report


def test_empty_scope_cannot_enable_and_memory_edit_invalidates_plan(state):
    identifier, _ = import_file(state, request("import"), "DEMO.xlsx", workbook_bytes())
    configure(state, identifier)
    assistant = AssistantResponder(state, settings())
    _, code, missing = assistant.plan()
    assert not missing
    assert "Đã bật" in assistant.answer(request("enable", code)) and state.control("schedule_enabled")
    assistant.answer(request("set", "warehouses UNKNOWN_WAREHOUSE"))
    _, changed, missing = assistant.plan()
    assert changed != code and missing and not state.control("schedule_enabled")
    assert "chưa đủ" in assistant.answer(request("enable", changed))
    assert "không có dòng" in assistant.report()


def test_daily_schedule_idempotency_survives_restart_and_ambiguous_send(state):
    identifier, _ = import_file(state, request("import"), "DEMO.xlsx", workbook_bytes())
    configure(state, identifier)
    assistant = AssistantResponder(state, settings())
    _, code, _ = assistant.plan()
    assistant.answer(request("enable", code))
    due = datetime.now(TZ).replace(hour=13, minute=1, second=0, microsecond=0)
    sent = []
    assistant.tick(lambda req, text, job: sent.append(job), now=due)
    restarted = AssistantResponder(state, settings())
    restarted.tick(lambda req, text, job: sent.append(job), now=due)
    assert len(sent) == 1
    tomorrow = due+timedelta(days=1)
    def fail(*args):
        raise TelegramError("Ambiguous delivery")
    with pytest.raises(TelegramError):
        restarted.tick(fail, now=tomorrow)
    restarted.tick(lambda *args: sent.append("duplicate"), now=tomorrow)
    assert len(sent) == 1
    assert any(r[0] == "UNKNOWN" for r in state.db.execute("SELECT state FROM deliveries"))
    assert not any("SYNTHETIC" in str(tuple(r)) for r in state.db.execute("SELECT * FROM deliveries"))


def test_durable_budget_reserves_before_network_and_carries_minute_window(state):
    first = DurableBudget(state)
    first.acquire(4000, time.monotonic()+2)
    second = DurableBudget(state)
    assert second.attempts == 1 and second.reserved_tokens == 4000 and second.window.events
    from ops_team.limits import ProviderError
    with pytest.raises(ProviderError, match="token/phút"):
        second.acquire(4000, time.monotonic()+1)
    assert state.control("provider_usage")["attempts"] == 1


def test_google_readonly_pins_tab_gid_ranges_and_no_auth_fallback(caplog):
    facts = {"sheets_id": SHEET, "sheets_tab": "DC Phú Thọ", "sheets_gid": "123",
             "sheets_ranges": json.dumps({"costs": "'DC Phú Thọ'!A1:K1000"})}
    calls = []
    raw = demo_tables()["costs"]
    rows = [list(raw.columns), *raw.astype(object).where(raw.notna(), "").values.tolist()]
    caplog.set_level(logging.INFO, logger="httpx")
    def handle(request):
        calls.append(request)
        assert request.method == "GET" and request.url.host == "sheets.googleapis.com"
        assert request.headers["Authorization"] == "Bearer FAKE_ACCESS_TOKEN"
        if ":batchGet" not in request.url.path:
            return httpx.Response(200, json={"sheets": [{"properties": {"title": "DC Phú Thọ", "sheetId": 123}}]})
        assert request.url.params.get_list("ranges") == ["'DC Phú Thọ'!A1:K1000"]
        return httpx.Response(200, json={"spreadsheetId": SHEET, "valueRanges": [{"range": "'DC Phú Thọ'!A1:K1000", "values": rows}]})
    checked, source = read_sheets(facts, ACCOUNT, client=httpx.Client(transport=httpx.MockTransport(handle)), token_provider=lambda _: "FAKE_ACCESS_TOKEN")
    assert len(calls) == 2 and len(checked.tables["costs"]) == len(raw)
    assert source["gid"] == "123" and source["tab"] == "DC Phú Thọ"
    assert "dòng 2" in checked.tables["costs"].iloc[0]._source
    assert SHEET not in caplog.text and "FAKE_ACCESS_TOKEN" not in caplog.text
    facts["sheets_gid"] = "999"
    with pytest.raises(ValueError, match="gid"):
        read_sheets(facts, ACCOUNT, client=httpx.Client(transport=httpx.MockTransport(handle)), token_provider=lambda _: "FAKE_ACCESS_TOKEN")
    assert len(calls) == 3
    facts["sheets_ranges"] = json.dumps({"costs": "Another!A1:K1000"})
    with pytest.raises(ValueError, match="tab đã khóa"):
        read_sheets(facts, ACCOUNT, client=httpx.Client(transport=httpx.MockTransport(handle)), token_provider=lambda _: "FAKE_ACCESS_TOKEN")
    assert len(calls) == 3


@pytest.mark.parametrize("value", [{"costs": "tab!A1:Z999999"}, {"costs": "tab!A:A"}, {"unknown": "tab!A1:B2"}])
def test_google_range_limits(value):
    with pytest.raises(ValueError):
        range_map(value)


def test_real_adapter_team_waits_within_overall_deadline_not_single_call_timeout(monkeypatch):
    import ops_team.providers as providers
    import ops_team.orchestrator as orchestrator
    import ops_team.telegram_agent as telegram
    tick, sent = [1000.0], []
    fake_time = SimpleNamespace(monotonic=lambda: tick[0])
    monkeypatch.setattr(providers, "time", fake_time)
    monkeypatch.setattr(orchestrator, "time", fake_time)
    monkeypatch.setattr(telegram, "time", fake_time)
    async def fetch(url, headers, body, timeout):
        tick[0] += 2
        payload = json.loads(body["messages"][0]["content"])
        sent.append(payload)
        answer = response()
        if payload["phase"] == "plan":
            answer["selected_agents"] = [n for n in AGENT_NAMES if n not in ("Coordinator", "Auditor")]
        elif payload["agent"] == "Auditor":
            answer["audit_decisions"] = [{"task_id": r["task_id"], "verdict": "accept", "reason": "synthetic review", "evidence_ids": ["cost"]} for r in payload["review_tasks"]]
        else:
            key = next(k for k, v in payload["metrics"].items() if v["value"] is not None)
            answer["observations"] = [{"text": "synthetic interpretation", "evidence_ids": [key]}]
        return 200, json.dumps({"model": "fake-model", "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer)}}]}).encode(), None
    monkeypatch.setattr(providers, "_fetch_async", fetch)
    configs = {n: ModelConfig("Groq", FREE_MODELS[0], "fake-key", free_pilot=True, free_account_confirmed=True) for n in AGENT_NAMES}
    context = warehouse_context(validate(demo_tables()), "2026-09", None, ["Duc Hoa"], None, "DEMO")
    agent = TelegramAgent(configs, context)
    agent._budget.clock = fake_time.monotonic
    agent._budget.sleep = lambda seconds: tick.__setitem__(0, tick[0]+seconds)
    result = agent.answer(request("team", "Synthetic team test"))
    assert len(sent) >= 9 and agent._budget.attempts == len(sent)
    assert tick[0] > 1060 and "Diễn giải đã qua kiểm tra chéo" in result, result
