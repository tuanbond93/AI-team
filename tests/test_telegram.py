"""All Telegram and model exchanges use fake tokens and in-memory transports."""
from copy import deepcopy
import json
import logging
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import httpx
import pytest
from streamlit.testing.v1 import AppTest
from ops_team.schema import AGENT_NAMES
from ops_team.providers import ModelConfig, FREE_MODELS, call_model
from ops_team.telegram_bridge import TelegramAPI, TelegramBridge, TelegramError, Incoming, parse_message, chunks_for_telegram
from ops_team.telegram_agent import TelegramAgent, warehouse_context, supra_context
from ops_team.telegram_ui import parse_user_ids
from ops_team.demo import demo_tables
from ops_team.ingestion import validate


TOKEN = "123456789:"+"x"*35


def message(text="/ask@test_bot test", **changes):
    return {"message_id": 11, "date": 101, "chat": {"id": -100123, "type": "supergroup"},
            "from": {"id": 55, "is_bot": False}, "text": text, **changes}


def update(text="/ask@test_bot test", update_id=1, **changes):
    return {"update_id": update_id, "message": message(text, **changes)}


def configs():
    return {n: ModelConfig("Groq", FREE_MODELS[0], "fake-model-key-for-test", free_pilot=True,
                           free_account_confirmed=True) for n in AGENT_NAMES}


def model_output(**changes):
    return {"summary": "Synthetic reply", "observations": [], "hypotheses": [], "proposals": [],
            "requests": [], "disagreements": [], "selected_agents": [], "audit_decisions": [], **changes}


class FakeAPI:
    _token = TOKEN
    def __init__(self):
        self.sent = []

    def call(self, method, **params):
        self.sent.append((method, params))
        return {"message_id": 90}


class FakeResponder:
    def __init__(self):
        self.requests = []
        self.cancel = threading.Event()

    def answer(self, request):
        self.requests.append(request)
        return "Synthetic reply"

    def safe_to_send(self, text, extra_secrets=()):
        return not any(s in text for s in extra_secrets)


def paired_bridge(extra_users=()):
    api, responder = FakeAPI(), FakeResponder()
    bridge = TelegramBridge(api, responder, extra_users, clock=lambda: 100, expected_chat=-100123, owner_id=55)
    bridge._username, bridge._bot_id = "test_bot", 99
    # Do not sleep between mocked sends; the event is still initially unset.
    bridge._stop.wait = lambda seconds: bridge._stop.is_set()
    bridge.process(update(bridge.pairing_command))
    api.sent.clear()
    return bridge, api, responder


@pytest.mark.parametrize("changes", [
    {"chat": {"id": 55, "type": "private"}}, {"from": {"id": 55, "is_bot": True}},
    {"sender_chat": {"id": -100123}}, {"forward_origin": {"type": "user"}},
    {"text": "/ask@different_bot test"}, {"text": "ordinary group conversation"},
    {"text": "/bad-command text"}, {"text": None}, {"from": {}},
])
def test_parser_ignores_unaddressed_or_unauthenticated_shapes(changes):
    assert parse_message(message(**changes), "test_bot", 99) is None


def test_reply_mentions_and_topics_are_explicit_and_utf16_correct():
    parsed = parse_message(message("follow up", reply_to_message={"from": {"id": 99, "is_bot": True}},
                                   message_thread_id=7), "test_bot", 99)
    assert parsed.command == "ask" and parsed.topic_id == 7
    text = "😀 @test_bot câu hỏi"
    parsed = parse_message(message(text, entities=[{"type": "mention", "offset": 3, "length": 9}]), "test_bot", 99)
    assert parsed.command == "ask" and "@test_bot" not in parsed.text
    assert parse_message(message("x@test_bot.com"), "test_bot", 99) is None


def test_pairing_scopes_sender_chat_and_topic_and_deduplicates():
    bridge, api, responder = paired_bridge()
    assert bridge.snapshot()["chat_id"] == -100123 and bridge.snapshot()["user_ids"] == [55]
    bridge.process(update(update_id=2, chat={"id": -100999, "type": "supergroup"}))
    bridge.process(update(update_id=3, **{"from": {"id": 77, "is_bot": False}}))
    assert not api.sent and not responder.requests
    accepted = update(update_id=4, message_thread_id=8)
    bridge.process(accepted)
    bridge.process(accepted)
    assert len(responder.requests) == len(api.sent) == 1
    method, params = api.sent[0]
    assert method == "sendMessage" and params["chat_id"] == -100123 and params["message_thread_id"] == 8
    assert params["reply_parameters"]["message_id"] == 11 and "parse_mode" not in params


def test_pairing_expiry_wrong_code_backlog_and_stop_never_call_model():
    bridge, api, responder = paired_bridge()
    bridge.process(update(update_id=2, date=99))
    bridge.process({"update_id": 3, "edited_message": message()})
    bridge.stop()
    bridge.process(update(update_id=4))
    assert not api.sent and not responder.requests
    fresh = TelegramBridge(FakeAPI(), FakeResponder(), clock=lambda: 100)
    fresh._username, fresh._bot_id = "test_bot", 99
    fresh.process(update("/pair@test_bot WRONG"))
    fresh._clock = lambda: 701
    fresh.process(update(fresh.pairing_command, update_id=2, date=701))
    assert fresh.snapshot()["chat_id"] is None


def test_extra_users_are_still_limited_to_the_paired_group():
    bridge, api, responder = paired_bridge([77])
    bridge.process(update(update_id=2, **{"from": {"id": 77, "is_bot": False}}))
    assert len(responder.requests) == 1


def test_telegram_secret_is_blocked_before_model_and_on_output():
    bridge, api, responder = paired_bridge()
    bridge.process(update("/ask@test_bot "+TOKEN, update_id=2))
    assert not responder.requests and TOKEN not in str(api.sent)
    responder.answer = lambda request: TOKEN
    bridge.process(update(update_id=3))
    assert TOKEN not in str(api.sent)


def test_long_emoji_messages_stay_within_telegram_units():
    text = "😀"*4000+"việt"*1000
    parts = list(chunks_for_telegram(text))
    assert "".join(parts) == text and all(len(p.encode("utf-16-le"))//2 <= 3500 for p in parts)


@pytest.mark.parametrize("status", [401, 403, 409, 429, 500])
def test_api_errors_never_return_token_or_server_body(status):
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, text=TOKEN)))
    api = TelegramAPI(TOKEN, client)
    with pytest.raises(TelegramError) as error:
        api.call("getMe")
    assert TOKEN not in str(error.value)
    api.close()


def test_api_transport_scope_and_no_automatic_send_retry():
    calls = []
    def handle(request):
        calls.append(request)
        assert request.url.host == "api.telegram.org" and request.method == "POST"
        raise httpx.ReadTimeout(TOKEN)
    api = TelegramAPI(TOKEN, httpx.Client(transport=httpx.MockTransport(handle)))
    with pytest.raises(TelegramError):
        api.call("sendMessage", chat_id=-100123, text="fake")
    assert len(calls) == 1
    with pytest.raises(TelegramError):
        api.call("deleteWebhook")
    assert len(calls) == 1
    api.close()


def test_existing_webhook_is_not_changed():
    called = []
    def handle(request):
        method = request.url.path.rsplit("/", 1)[-1]
        called.append(method)
        result = {"id": 99, "username": "test_bot", "is_bot": True} if method == "getMe" else {"url": "https://example.test/webhook"}
        return httpx.Response(200, json={"ok": True, "result": result})
    api = TelegramAPI(TOKEN, httpx.Client(transport=httpx.MockTransport(handle)))
    bridge = TelegramBridge(api, FakeResponder())
    with pytest.raises(TelegramError, match="webhook"):
        bridge.start()
    assert called == ["getMe", "getWebhookInfo"] and not bridge.snapshot()["alive"]
    api.close()


def test_worker_fake_telegram_to_real_adapter_and_reply_then_shutdown(caplog):
    sent, model_calls = [], []
    caplog.set_level(logging.INFO, logger="httpx")
    def model_handler(request):
        body = json.loads(request.content)
        payload = json.loads(body["messages"][0]["content"])
        model_calls.append(payload)
        assert payload["agent"] == "Cost" and payload["question"] == "Synthetic question"
        assert payload["metrics"] == {}
        return httpx.Response(200, json={"model": "fake-model", "choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps(model_output())}}]})
    model_client = httpx.Client(transport=httpx.MockTransport(model_handler))
    def model_transport(config, payload, **kwargs):
        return call_model(config, payload, client=model_client, **kwargs)
    agent = TelegramAgent(configs(), transport=model_transport)
    def telegram_handler(request):
        method = request.url.path.rsplit("/", 1)[-1]
        body = json.loads(request.content)
        if method == "getMe":
            result = {"id": 99, "is_bot": True, "username": "test_bot"}
        elif method == "getWebhookInfo":
            result = {"url": ""}
        elif method == "getUpdates":
            assert body["allowed_updates"] == ["message"]
            result = [update(bridge.pairing_command),
                      update("/cost@test_bot Should be ignored", update_id=2, **{"from": {"id": 88, "is_bot": False}}),
                      update("/cost@test_bot Synthetic question", update_id=3, message_thread_id=5)]
        else:
            sent.append(body)
            if len(sent) == 2:
                bridge.stop()
            result = {"message_id": 90}
        return httpx.Response(200, json={"ok": True, "result": result})
    api = TelegramAPI(TOKEN, httpx.Client(transport=httpx.MockTransport(telegram_handler)))
    bridge = TelegramBridge(api, agent, clock=lambda: 100, expected_chat=-100123, owner_id=55)
    bridge._stop.wait = lambda seconds: bridge._stop.is_set()
    bridge.start()
    bridge._thread.join(timeout=5)
    if bridge.snapshot()["alive"]:
        bridge.stop()
    assert not bridge.snapshot()["alive"] and not bridge.snapshot()["error"]
    assert len(sent) == 2 and len(model_calls) == 1 and sent[-1]["message_thread_id"] == 5
    assert "Synthetic reply" in sent[-1]["text"] and "chưa được kiểm tra chéo" in sent[-1]["text"]
    assert not api._token and not agent._configs and not agent._history
    assert TOKEN not in caplog.text and "[REDACTED]" in caplog.text
    model_client.close()


def test_agent_routes_history_by_user_and_topic_and_reset_is_local():
    payloads = []
    def transport(config, payload, **kwargs):
        payloads.append(deepcopy(payload))
        assert kwargs["budget"].max_calls == 60
        return model_output(), "fake-model"
    agent = TelegramAgent(configs(), transport=transport)
    first = Incoming(-100123, 55, 1, 7, "cost", "Chi phí?")
    answer = agent.answer(first)
    assert "chưa được kiểm tra chéo" in answer and payloads[-1]["agent"] == "Cost"
    assert payloads[-1]["metrics"] == {} and payloads[-1]["conversation"] == []
    agent.answer(Incoming(-100123, 55, 2, 7, "ask", "Tiếp theo?"))
    assert len(payloads[-1]["conversation"]) == 1
    agent.answer(Incoming(-100123, 77, 3, 7, "ask", "Người khác?"))
    assert payloads[-1]["conversation"] == []
    agent.answer(Incoming(-100123, 55, 4, 8, "ask", "Chủ đề khác?"))
    assert payloads[-1]["conversation"] == []
    agent.answer(Incoming(-100123, 55, 5, 7, "reset", ""))
    assert (-100123, 7, 55) not in agent._history
    assert (-100123, 8, 55) in agent._history


def test_agent_budget_scope_requirements_stop_and_private_material():
    calls = []
    def transport(config, payload, **kwargs):
        calls.append(payload)
        return model_output(), "fake-model"
    agent = TelegramAgent(configs(), transport=transport, max_questions=1)
    assert "chưa chọn" in agent.answer(Incoming(-1, 55, 1, None, "supra", "test"))
    assert "chọn nguồn" in agent.answer(Incoming(-1, 55, 2, None, "team", "test"))
    assert "chặn" in agent.answer(Incoming(-1, 55, 3, None, "ask", "fake-model-key-for-test"))
    assert not calls
    agent.answer(Incoming(-1, 55, 4, None, "ask", "test"))
    assert "hết" in agent.answer(Incoming(-1, 55, 5, None, "ask", "test")) and len(calls) == 1
    fresh = TelegramAgent(configs(), transport=transport)
    fresh.cancel.set()
    assert "dừng" in fresh.answer(Incoming(-1, 55, 6, None, "ask", "test"))
    assert len(calls) == 1
    fresh.clear()
    assert not fresh._history and not fresh._configs and not fresh._context


def test_no_source_requests_are_rendered_with_concrete_questions_without_empty_labels_or_tool_calls():
    calls = []
    def transport(config, payload, **kwargs):
        calls.append(payload)
        assert payload["metrics"] == {} and "trường còn thiếu" in payload["task"]
        return model_output(summary="Cần ba nhóm đầu vào để phân tích chi phí.", hypotheses=["", "  "], proposals=[""],
            requests=[{"to": "Accounting", "question": "Anh có sổ chi phí theo ngày/kho/loại phí và chứng từ không?", "evidence_ids": []},
                      {"to": "Operations", "question": "Có sản lượng cùng kỳ, cùng kho và đơn vị tính không?", "evidence_ids": []},
                      {"to": "Cost", "question": "Có ngân sách và quy tắc phân bổ cần đối soát không?", "evidence_ids": []}]), "fake-model"
    agent = TelegramAgent(configs(), transport=transport)
    answer = agent.answer(Incoming(-1, 55, 1, None, "cost", "Nêu ba loại dữ liệu cần cho chi phí"))
    assert len(calls) == 1 and "Anh xác nhận giúp" in answer and "chưa có số liệu doanh nghiệp" in answer
    assert "chứng từ" in answer and "sản lượng" in answer and "ngân sách" in answer
    assert "Giả thuyết:" not in answer and "Đề xuất:" not in answer


def test_all_empty_model_content_is_not_presented_as_completed_answer():
    agent = TelegramAgent(configs(), transport=lambda *a, **kw: (model_output(summary=" ", proposals=[""]), "fake-model"))
    result = agent.answer(Incoming(-1, 55, 1, None, "ask", "test"))
    assert "chưa trả nội dung" in result and "chưa hoàn tất" in result


def test_warehouse_snapshot_is_frozen_and_team_reuses_existing_orchestrator(monkeypatch):
    validation = validate(demo_tables())
    context = warehouse_context(validation, "2026-09", "2026-08", ["Duc Hoa"], None, "DEMO")
    before = context["metrics"]["cost"]["value"]
    validation.tables["costs"].loc[:, "amount_vnd"] = 0
    assert context["metrics"]["cost"]["value"] == before
    calls = []
    def team(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(exchanges=[SimpleNamespace(receiver="Coordinator", phase="final", answer=model_output(), model="fake")],
                               audit={"review_complete": False}, status="PARTIAL")
    monkeypatch.setattr("ops_team.telegram_agent.run_team", team)
    result = TelegramAgent(configs(), context).answer(Incoming(-1, 55, 1, None, "team", "SLA?"))
    assert len(calls) == 1 and calls[0][0][4] == "SLA?" and callable(calls[0][1]["transport"])
    assert "vẫn còn điểm chưa chốt" in result and "PARTIAL" in result


@pytest.mark.skipif(not (Path.home()/"Desktop"/"Super Tracking chi phí Supra"/"Supra_Phu_Tho_PnL_CostToServe_Pilot14Ngay.xlsx").exists(),
    reason="Cần nguồn Supra local của owner; repo sạch không chứa dữ liệu thật.")
def test_supra_question_uses_existing_account_and_exact_auditor_contract():
    from ops_team.supra.ingestion import read_workbook
    from ops_team.supra.service import SupraService
    source = Path.home()/"Desktop"/"Super Tracking chi phí Supra"/"Supra_Phu_Tho_PnL_CostToServe_Pilot14Ngay.xlsx"
    service = SupraService(read_workbook(source.read_bytes(), source.name))
    context = supra_context(service)
    sent = []
    def transport(config, payload, **kwargs):
        sent.append(payload)
        assert payload["question"] == "Synthetic question about PLAN"
        assert payload["scope"] == "Source PLAN, not actual or verified savings"
        assert all(m["mode"] == "PLAN" and m["verification"] == "UNVERIFIED" for m in payload["metrics"].values())
        result = model_output()
        if payload["agent"] == "Auditor":
            assert payload["review_tasks"][0]["task_id"] == "SUPRA-ACCOUNT-v1"
            result["audit_decisions"] = [{"task_id": "SUPRA-ACCOUNT-v1", "verdict": "unresolved",
                "reason": "Synthetic review", "evidence_ids": ["supra.cost"]}]
        return result, "fake-model"
    result = TelegramAgent(configs(), context, transport=transport).answer(
        Incoming(-1, 55, 1, None, "team", "Synthetic question about PLAN"))
    assert len(sent) == 2 and "vẫn còn điểm chưa chốt" in result and "chưa đối soát thực tế" in result


@pytest.mark.parametrize("text", ["abc", "-1", "0", "1.2", "1," , ",".join(str(i) for i in range(1, 22))])
def test_invalid_member_allowlists(text):
    with pytest.raises(ValueError):
        parse_user_ids(text)


def test_ui_is_offline_until_explicit_activation_and_live_consent(monkeypatch):
    called = []
    monkeypatch.setattr(TelegramAPI, "call", lambda *a, **kw: called.append(kw))
    root = Path(__file__).resolve().parents[1]
    at = AppTest.from_file(str(root/"app.py"), default_timeout=30).run()
    assert not at.exception and any(t.label == "Telegram" for t in at.tabs)
    assert at.button(key="telegram_start").disabled and not called
    at.radio(key="mode").set_value("Live").run()
    at.checkbox(key="free_pilot").check().run()
    at.text_input(key="group_key_free").set_value("fake-model-key-for-test").run()
    at.checkbox(key="free_account_confirmed").check().run()
    at.text_input(key="telegram_bot_token").set_value(TOKEN).run()
    assert at.button(key="telegram_start").disabled and not called
    at.checkbox(key="telegram_consent").check().run()
    assert not at.exception and not at.button(key="telegram_save").disabled and not called
    at.button(key="telegram_save").click().run()
    assert not at.exception and not at.button(key="telegram_start").disabled and not called
    assert TOKEN in at.session_state["private_export_fragments"]
    assert all(TOKEN not in x.value for x in at.markdown)
