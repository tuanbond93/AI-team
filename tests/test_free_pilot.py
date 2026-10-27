from dataclasses import replace
from io import BytesIO
from pathlib import Path
import json
import time
import zipfile
from types import SimpleNamespace
import httpx
import pytest
from streamlit.testing.v1 import AppTest
from ops_team.providers import ModelConfig, FREE_MODELS, ProviderError, call_model, probe_model
from ops_team.limits import RequestBudget, UsageWindow
from ops_team.orchestrator import run_team
from ops_team.schema import AGENT_NAMES
from ops_team.demo import demo_tables
from ops_team.ingestion import validate
from ops_team.muse import muse_bundle, demo_muse_bundle


def config(**kwargs):
    return replace(ModelConfig("Groq", FREE_MODELS[0], "runtime-only-test-key", free_pilot=True,
        free_account_confirmed=True), **kwargs)


def output():
    return dict(summary="probe ok", observations=[], hypotheses=[], proposals=[], requests=[],
        disagreements=[], selected_agents=[], audit_decisions=[])


def response(data=None, **kwargs):
    return httpx.Response(200, json={"model": FREE_MODELS[0], "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
        "choices": [{"finish_reason": kwargs.get("finish", "stop"),
                     "message": {"content": json.dumps(output() if data is None else data), **kwargs.get("message", {})}}]})


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("change", [{"provider": "OpenAI"}, {"provider": "Gemini"}, {"model": "paid-unknown"},
    {"structured_output": "json_object"}, {"free_account_confirmed": False}])
def test_free_mode_rejects_before_network(change):
    calls = []
    with pytest.raises(ProviderError):
        call_model(config(**change), {"metrics": {}}, client=client(lambda r: calls.append(r)), budget=RequestBudget())
    assert not calls


def test_run_gates_all_roles_before_any_transport():
    configs = {n: config() for n in AGENT_NAMES}
    configs["Fleet"] = replace(configs["Fleet"], provider="OpenAI", free_pilot=False)
    calls = []
    with pytest.raises(ValueError, match="FREE PILOT"):
        run_team(validate(demo_tables()), "2026-09", None, ["Duc Hoa"], "SLA?", "Live", configs, True,
            free_pilot=True, transport=lambda *a, **kw: calls.append(a))
    assert not calls


def test_groq_request_and_synthetic_probe_exclude_session_material():
    bodies = []
    def handle(request):
        assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
        body = json.loads(request.content)
        bodies.append(body)
        assert body["response_format"]["json_schema"]["strict"] is True
        assert body["max_completion_tokens"] == 768 and "max_tokens" not in body and "store" not in body
        assert "private-instructions-user-data" not in request.content.decode()
        assert request.headers["Authorization"] == "Bearer runtime-only-test-key"
        payload = json.loads(body["messages"][0]["content"])
        assert payload == {"data_label": "DEMO SYNTHETIC PROBE", "sample": 1,
            "task": 'Connection check only. Return exactly the JSON object {"status":"ok"}.', "output_format": "JSON"}
        schema = body["response_format"]["json_schema"]
        assert schema["name"] == "connection_probe"
        assert schema["schema"] == {"type": "object", "properties": {"status": {"type": "string", "enum": ["ok"]}},
            "required": ["status"], "additionalProperties": False}
        return response({"status": "ok"})
    result = probe_model(config(instructions="private-instructions-user-data"), client=client(handle))
    assert result["ok"] and result["attempts"] == 1 and result["usage"]["total_tokens"] == 30
    assert result["model"] == FREE_MODELS[0] and len(bodies) == 1


@pytest.mark.parametrize("data", [{"status": "failed"}, {"status": "ok", "summary": "extra"},
    {"status": True}, [], None])
def test_probe_rejects_invalid_or_extra_fields(data):
    result = probe_model(config(), client=client(lambda r: response(message={"content": json.dumps(data)})))
    assert not result["ok"] and result["attempts"] == 1


def test_business_evidence_validation_cannot_be_bypassed_by_probe_payload():
    data = output()
    data["observations"] = [{"text": "synthetic", "evidence_ids": ["SYNTHETIC:probe:row2"]}]
    with pytest.raises(ProviderError, match="mã bằng chứng không tồn tại"):
        call_model(config(), {"phase": "probe", "metrics": {"probe": {"value": 1}}},
            client=client(lambda r: response(data)), budget=RequestBudget())


def test_probe_also_supports_explicit_json_object_mode():
    def handle(request):
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        assert json.loads(body["messages"][0]["content"])["response_contract"] == {"status": "ok"}
        return response({"status": "ok"})
    assert probe_model(config(free_pilot=False, structured_output="json_object"), client=client(handle))["ok"]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 503])
def test_http_errors_are_actionable_and_never_echo_response(status):
    result = probe_model(config(), client=client(lambda r: httpx.Response(status, text="runtime-only-test-key PRIVATE BODY")))
    assert not result["ok"] and f"HTTP {status}" in result["error"]
    assert "runtime-only-test-key" not in str(result) and "PRIVATE BODY" not in str(result)
    assert result["attempts"] == 1


def test_retry_after_bounded_and_counts_call_token_reservations():
    budget = RequestBudget(max_calls=2, max_tokens=20000, tokens_per_minute=20000)
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(429, headers={"retry-after": "0"}) if len(calls) == 1 else response()
    answer, _ = call_model(config(), {"metrics": {}}, client=client(handle), budget=budget)
    assert answer["summary"] == "probe ok" and len(calls) == budget.attempts == 2
    assert budget.reserved_tokens > 6000
    with pytest.raises(ProviderError, match="ngân sách"):
        call_model(config(), {"metrics": {}}, client=client(handle), budget=budget)
    assert len(calls) == 2


@pytest.mark.parametrize("header", ["99999", "nonsense", "NaN", "-1"])
def test_quota_retry_does_not_loop_or_sleep_unbounded(header):
    sleeps, calls = [], []
    budget = RequestBudget(sleep=sleeps.append)
    def handle(request):
        calls.append(request)
        return httpx.Response(429, headers={"retry-after": header})
    with pytest.raises(ProviderError, match="429"):
        call_model(config(), {"metrics": {}}, client=client(handle), budget=budget)
    assert len(calls) == 1 and not sleeps and budget.halted
    with pytest.raises(ProviderError, match="Đã dừng"):
        call_model(config(), {"metrics": {}}, client=client(handle), budget=budget)
    assert len(calls) == 1


def test_budget_per_run_tokens_and_shared_window_stop_before_network():
    calls = []
    with pytest.raises(ProviderError, match="ngân sách"):
        call_model(config(), {"metrics": {}}, client=client(lambda r: calls.append(r)), budget=RequestBudget(max_tokens=1000))
    assert not calls
    window = UsageWindow([(time.monotonic(), 7500)])
    result = probe_model(config(), client=client(lambda r: calls.append(r)), window=window)
    assert not result["ok"] and result["attempts"] == 0 and not calls
    assert "token/phút" in result["error"]


def test_minute_window_paces_and_expires_with_fake_clock():
    now = [100.0]
    sleeps = []
    def wait(seconds):
        sleeps.append(seconds)
        now[0] += seconds
    window = UsageWindow([(100.0, 6000)])
    budget = RequestBudget(window=window, clock=lambda: now[0], sleep=wait)
    budget.acquire(3000, 200)
    assert len(sleeps) == 1 and 60 <= sleeps[0] < 61 and budget.attempts == 1
    assert len(window.events) == 1


@pytest.mark.parametrize("kind", ["malformed", "schema", "refusal", "length", "timeout"])
def test_groq_bad_output_and_deadline_fail_closed(kind):
    def handle(request):
        if kind == "timeout":
            raise httpx.ReadTimeout("runtime-only-test-key private raw error")
        if kind == "malformed":
            return httpx.Response(200, text="not json")
        if kind == "schema":
            return response({"summary": "missing fields"})
        return response(finish="length" if kind == "length" else "stop", message={"refusal": "refused"} if kind == "refusal" else {})
    result = probe_model(config(), client=client(handle))
    assert not result["ok"] and "runtime-only-test-key" not in str(result) and result["attempts"] == 1


def test_large_payload_blocks_without_silent_evidence_cut():
    calls = []
    with pytest.raises(ProviderError, match="Không cắt ngầm"):
        call_model(config(), {"metrics": {}, "review_tasks": ["all evidence"*5000]}, client=client(lambda r: calls.append(r)), budget=RequestBudget())
    assert not calls


def test_muse_export_demo_only_business_evidence_and_sensitive_guard():
    package = demo_muse_bundle()
    with zipfile.ZipFile(BytesIO(package)) as archive:
        text = "\n".join(archive.read(n).decode("utf-8-sig") for n in archive.namelist())
        assert len(package) < 50000 and "DEMO" in text and "CHƯA được Auditor" in text
        assert "CHI_SO.csv" in archive.namelist() and "BANG_CHUNG.csv" in archive.namelist()
        assert all(k not in text for k in ("api_key", "session_instructions", "response_contract", "previous_exchanges"))
    run = run_team(validate(demo_tables()), "2026-09", None, ["Duc Hoa"], "full private question must never export")
    from ops_team.analysis import what_if
    run.scenarios = [what_if(validate(demo_tables()), "2026-09", ["Duc Hoa"], "labor", 5, assumption="runtime-only-test-key")]
    with pytest.raises(ValueError, match="chặn xuất"):
        muse_bundle(run, ["runtime-only-test-key"])


def test_free_pilot_ui_common_key_override_gates_and_muse_demo():
    root = Path(__file__).resolve().parents[1]
    at = AppTest.from_file(str(root/"app.py"), default_timeout=30).run()
    assert not at.exception and at.session_state["muse_demo_bundle"].startswith(b"PK")
    next(b for b in at.button if b.label == "Nạp dữ liệu demo").click().run()
    at.radio(key="mode").set_value("Live").run()
    at.checkbox(key="free_pilot").check().run()
    assert not at.exception and at.selectbox(key="group_model_free").value == FREE_MODELS[0]
    at.text_input(key="group_key_free").set_value("runtime-only-test-key").run()
    assert next(b for b in at.button if b.key == "probe_free").disabled
    at.checkbox(key="free_account_confirmed").check().run()
    assert not next(b for b in at.button if b.key == "probe_free").disabled
    at.checkbox(key="override_Fleet").check().run()
    assert at.selectbox(key="free_provider_Fleet").options == ["Groq"]
    at.selectbox(key="free_model_Fleet").set_value(FREE_MODELS[1]).run()
    next(c for c in at.checkbox if c.label.startswith("Tôi chủ động bật Live")).check().run()
    assert not at.exception and not next(b for b in at.button if b.label == "Chạy đội agent").disabled
    assert not any("runtime-only-test-key" in m.value for m in at.markdown)


def test_free_orchestration_exact_audit_versions_and_actual_http_attempts(monkeypatch):
    from ops_team import providers
    import ops_team.orchestrator as engine
    sent = []
    estimates = []
    budget_time = [1000.0]
    tick = lambda: budget_time[0]
    async def fetch(url, headers, body, timeout):
        assert 0 < timeout <= 30
        budget_time[0] += 2  # Synthetic HTTP latency counts toward the same deadline.
        payload = json.loads(body["messages"][0]["content"])
        sent.append(payload)
        result = output()
        if payload["phase"] == "plan":
            result["selected_agents"] = ["Accounting", "Cost", "Operations", "Workforce", "Fleet", "KPI"]
        elif payload["agent"] == "Auditor":
            result["audit_decisions"] = [{"task_id": item["task_id"], "verdict": "accept",
                "reason": "offline test only", "evidence_ids": ["cost"]} for item in payload["review_tasks"]]
        else:
            key = next(k for k, v in payload["metrics"].items() if v["value"] is not None)
            result["observations"] = [{"text": "offline synthetic interpretation", "evidence_ids": [key]}]
        data = {"model": FREE_MODELS[0], "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}]}
        return 200, json.dumps(data).encode(), None
    # Advance only the budget clock instead of sleeping real minutes. Keep the
    # production minute cap and per-run token cap active throughout the test.
    class FastBudget(RequestBudget):
        def acquire(self, tokens, deadline):
            estimates.append(tokens)
            return super().acquire(tokens, deadline)
    def budget(**kwargs):
        return FastBudget(**kwargs, clock=tick, sleep=lambda seconds: budget_time.__setitem__(0, budget_time[0]+seconds))
    monkeypatch.setattr(engine, "RequestBudget", budget)
    monkeypatch.setattr(engine, "time", SimpleNamespace(monotonic=tick))
    monkeypatch.setattr(providers, "time", SimpleNamespace(monotonic=tick))
    monkeypatch.setattr(providers, "_fetch_async", fetch)
    configs = {n: config() for n in AGENT_NAMES}
    result = run_team(validate(demo_tables()), "2026-09", "2026-08", ["Duc Hoa", "HCM"],
        "Chi phí, OT, hiệu suất xe và SLA kỳ này bất thường ở đâu? Ưu tiên kiểm tra gì trước khi duyệt?",
        "Live", configs, True, free_pilot=True, time_limit=600, clock=tick)
    assert result.calls == len(sent) == 9 and result.audit["review_complete"], str(estimates)+str([(x.receiver, x.error) for x in result.exchanges if x.error])
    assert result.audit["free_pilot_budget"]["attempts_including_retry"] == 9
    assert max(estimates) <= 7500 and sum(estimates) <= 60000
    assert 18 < result.duration <= 600 and len(result.accepted_task_ids) == 6
    auditor = next(p for p in sent if p["agent"] == "Auditor")
    assert len(auditor["review_tasks"]) == 6 and not auditor["previous_exchanges"]
    assert all(p["context_scope"] for p in sent)
    def check_samples(value):
        if isinstance(value, dict):
            if "sources" in value:
                assert value["source_count"] >= len(value["sources"])
                assert value["source_sample_only"] == (value["source_count"] > len(value["sources"]))
            for item in value.values():
                check_samples(item)
        elif isinstance(value, list):
            for item in value:
                check_samples(item)
    for payload in sent:
        check_samples(payload)
    assert len(next(p for p in sent if p["agent"] == "Fleet")["metrics"]) < len(result.metrics)*2


def test_free_orchestrator_quota_stops_network_and_leaves_audit_open(monkeypatch):
    from ops_team import providers
    calls = []
    async def fetch(*args):
        calls.append(1)
        return 429, b"", "99999"
    monkeypatch.setattr(providers, "_fetch_async", fetch)
    result = run_team(validate(demo_tables()), "2026-09", None, ["Duc Hoa"], "SLA?", "Live",
        {n: config() for n in AGENT_NAMES}, True, free_pilot=True)
    assert len(calls) == result.calls == 1 and not result.audit["review_complete"], [(x.receiver, x.error) for x in result.exchanges if x.error]
    assert not result.audit["execution_complete"] and "Chưa đủ" in result.status
