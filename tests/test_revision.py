import json
import zipfile
from io import BytesIO
import httpx
import pandas as pd
import pytest
from ops_team.demo import workbook_bytes, demo_tables
from ops_team.ingestion import read_file, validate, map_columns
from ops_team.schema import SPECS, AGENT_NAMES
from ops_team.agents import run_team, simulated
from ops_team.analysis import compare_periods, what_if
from ops_team.metrics import compute
from ops_team.providers import ModelConfig, call_model, ProviderError
from ops_team.reporting import build_report, report_bundle


@pytest.fixture
def validation():
    return validate(read_file(workbook_bytes(), "DEMO.xlsx"))


def live_run(v, transport, **kwargs):
    configs = {n: ModelConfig("OpenAI", "fake-"+n, "secret-key-never-export", "session-only-instructions-never-export") for n in AGENT_NAMES}
    return run_team(v, "2026-09", "2026-08", ["Duc Hoa", "HCM"], "Chi phí OT xe SLA?", mode="Live",
                    activated=True, configs=configs, transport=transport, **kwargs)


def fake_transport(verdicts=None, fail_revision=False, omit_decisions=False, capture=None):
    reviews = 0
    def transport(config, payload, timeout):
        nonlocal reviews
        if capture is not None:
            capture.append(payload)
        name = payload["agent"]
        out = simulated(name, payload["task"], payload["metrics"], 2)
        out["disagreements"] = []
        out["requests"] = []
        if payload["phase"] == "plan":
            out["selected_agents"] = ["Cost"]
        if fail_revision and payload["phase"] == "revision":
            raise ProviderError("HTTP 503; bản sửa chưa nhận được")
        if name == "Cost":
            out["hypotheses"] = ["unchanged-unapproved-hypothesis"]
        if name == "Auditor":
            verdict = (verdicts or ["accept"])[min(reviews, len(verdicts or ["accept"])-1)]
            reviews += 1
            if not omit_decisions:
                out["audit_decisions"] = [{"task_id": t["task_id"], "verdict": verdict, "reason": "Cần sửa lập luận" if verdict != "accept" else "Đã xem xét đúng bản",
                    "evidence_ids": ["cost"]} for t in payload["review_tasks"]]
        return out, "fake-actual"
    return transport


def test_repeated_auditor_objection_never_auto_closes(validation):
    captured = []
    result = live_run(validation, fake_transport(["revise", "revise", "revise"], capture=captured))
    assert not result.audit["passed"] and not result.audit["review_complete"]
    assert len([e for e in result.exchanges if e.receiver == "Auditor"]) == 3
    cost = [e for e in result.exchanges if e.receiver == "Cost"]
    assert [e.version for e in cost] == [1, 2, 3]
    assert all(e.review_status == "Auditor trả lại" for e in cost)
    assert result.audit["unresolved_versions"] == [cost[-1].task_id]
    final = next(p for p in captured if p["phase"] == "final")
    assert not final["previous_exchanges"]  # Unsupported versions cannot feed synthesis.
    report = build_report(result)
    assert "unchanged-unapproved-hypothesis" not in report


def test_revision_only_accepted_after_fresh_audit(validation):
    result = live_run(validation, fake_transport(["revise", "accept"]))
    assert result.audit["review_complete"]
    versions = [e for e in result.exchanges if e.receiver == "Cost"]
    assert versions[0].review_status == "Auditor trả lại"
    assert versions[0].task_id not in result.accepted_task_ids
    assert versions[1].task_id in result.accepted_task_ids
    assert versions[1].replaces == versions[0].task_id
    assert len(result.audit["history"]) == 2


@pytest.mark.parametrize("kwargs", [{"fail_revision": True}, {"omit_decisions": True}])
def test_failed_or_unreviewed_revision_stays_open(validation, kwargs):
    result = live_run(validation, fake_transport(["revise"], **kwargs))
    assert not result.audit["review_complete"] and not result.audit["passed"]
    assert result.audit["unresolved_versions"]


def test_reserved_budget_keeps_final_auditor_and_coordinator(validation):
    result = live_run(validation, fake_transport(["revise"]), max_calls=4)
    assert result.calls <= 4
    assert any(e.receiver == "Auditor" and e.answer for e in result.exchanges)
    assert result.exchanges[-1].receiver == "Coordinator" and result.exchanges[-1].answer
    assert not result.audit["review_complete"]


def test_source_pass_is_not_business_acceptance(validation):
    result = live_run(validation, fake_transport(["unresolved"]))
    assert result.audit["sources_passed"]
    assert not result.audit["passed"] and not result.accepted_task_ids


def test_csv_physical_lines_blank_and_multiline():
    parsed = read_file(b'a,b\n1,2\n\n3,4\n5,"multi\nline"\n', "blank-lines.csv")["CSV"]
    assert parsed._source.iloc[1].endswith("dòng 4")
    assert parsed._source.iloc[2].endswith("dòng 5–6")
    assert parsed.b.iloc[2] == "multi\nline"


def test_nonfirst_header_and_excel_blank_rows():
    data = b'title\n\na,b\n1,2\n\n3,4\n'
    parsed = read_file(data, "headers.csv", header_row=3, csv_separator=",")["CSV"]
    assert parsed._source.iloc[0].endswith("dòng 4")
    assert parsed._source.iloc[1].endswith("dòng 6")
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame({"a": [1, None, 3], "b": [2, None, 4]}).to_excel(writer, index=False, startrow=2)
    parsed = read_file(output.getvalue(), "blank.xlsx", header_row=3)["Sheet1"]
    assert parsed._source.iloc[2].endswith("dòng 6")


@pytest.mark.parametrize("mode,text,expected", [("vi", "1.234.567,5", 1234567.5), ("en", "1,234,567.5", 1234567.5), ("machine", "1234567.5", 1234567.5)])
def test_explicit_numeric_locale(mode, text, expected):
    tables = demo_tables()
    tables["costs"]["amount_vnd"] = tables["costs"].amount_vnd.astype(object)
    tables["costs"].loc[0, "amount_vnd"] = text
    parsed = validate(tables, mode)
    assert parsed.tables["costs"].iloc[0].amount_vnd == expected
    assert not parsed.rejected["costs"]


def test_wrong_locale_never_guessed():
    tables = demo_tables()
    tables["costs"]["amount_vnd"] = "1,234.50"
    v = validate(tables, "vi")
    assert len(v.rejected["costs"]) == 12
    assert all(i.get("column") == "amount_vnd" for i in v.issues if i["code"] == "INVALID_NUMBER")


def test_explicit_constant_units():
    raw = read_file(workbook_bytes(), "demo.xlsx")["fleet"]
    spec = SPECS["fleet"]
    raw = raw.drop(columns=list(spec.units))
    mapped = map_columns(raw, "fleet", {c: c for c in spec.columns if c not in spec.units}, dict(spec.units))
    assert not validate({"fleet": mapped}).rejected["fleet"]
    with pytest.raises(ValueError):
        map_columns(raw, "fleet", {}, {"weight_unit": "ton"})


def test_overcapacity_kept_as_business_warning():
    tables = demo_tables()
    tables["fleet"].loc[4, "loaded_kg"] = 6000
    v = validate(tables)
    assert not v.rejected["fleet"]
    assert any(i["code"] == "OVER_CAPACITY" for i in v.issues)
    assert compute(v, "2026-09", ["Duc Hoa"])["load_weight"].value == 85


def test_single_period_without_fabricated_baseline(validation):
    result = run_team(validation, "2026-09", None, ["Duc Hoa"], "SLA?")
    assert result.metrics["cost"].value == 245e6
    assert result.previous_metrics["cost"].value is None
    assert not result.comparison["bridge"]["ready"]
    assert "không có kỳ so sánh" in build_report(result)


def test_cost_volume_bridge_and_equal_category_scope(validation):
    comparison = compare_periods(validation, "2026-09", "2026-08", ["Duc Hoa", "HCM"])
    b = comparison["bridge"]
    assert b["volume_effect"] == pytest.approx(360e6/4400-360e6/4000)
    assert b["cost_effect"] == pytest.approx(51e6/4400)
    assert b["total_change"] == pytest.approx(b["cost_effect"]+b["volume_effect"])
    selected = compare_periods(validation, "2026-09", "2026-08", ["Duc Hoa", "HCM"], ["labor"])
    assert selected["changes"][0]["current"] == 214e6
    tables = demo_tables()
    tables["costs"] = tables["costs"][~((tables["costs"].period == "2026-08") & (tables["costs"].category == "rent"))]
    mismatch = compare_periods(validate(tables), "2026-09", "2026-08", ["Duc Hoa", "HCM"])
    assert not mismatch["bridge"]["ready"]
    assert next(c for c in mismatch["changes"] if c["id"] == "cost")["delta"] is None


def test_whatif_assumptions_no_auto_sla_or_combined_savings(validation):
    case = what_if(validation, "2026-09", ["Duc Hoa", "HCM"], "labor", 5, 1e6, 0, None, "Giả định thử nghiệm do người dùng nhập")
    assert case["net_savings_vnd"] == 9.7e6
    assert case["projected_cost_per_unit"] == pytest.approx((411e6-9.7e6)/4400)
    assert case["projected_sla"] is None
    with pytest.raises(ValueError):
        what_if(validation, "2026-09", ["Duc Hoa"], "labor", 5, sla_delta_pp=20)
    assert not what_if(validation, "2026-09", ["Duc Hoa"], "fleet", 5, assumption="")["ready"]


def test_export_bundle_excludes_session_material_and_records_review(validation):
    result = live_run(validation, fake_transport(["accept"]))
    result.scenarios = [what_if(validation, "2026-09", ["Duc Hoa", "HCM"], "labor", 5, assumption="Điều kiện mô phỏng")]
    result.reviews = {"scenario_labor": {"status": "Duyệt phạm vi thử nghiệm", "note": "=malicious()", "reviewed_at": "2026-10-24"}}
    with zipfile.ZipFile(BytesIO(report_bundle(result))) as archive:
        content = "\n".join(archive.read(name).decode("utf-8-sig") for name in archive.namelist())
        assert "secret-key-never-export" not in content and "session-only-instructions-never-export" not in content
        assert result.question not in content
        assert "Duyệt phạm vi thử nghiệm" in content
        assert "'=malicious()" in archive.read("Y_KIEN_QUAN_LY.csv").decode("utf-8-sig")


@pytest.mark.parametrize("provider,token_param", [("OpenAI", "max_completion_tokens"), ("Gemini", "max_tokens"), ("Ollama (local)", "max_tokens")])
def test_adapters_parameters_schema_and_usage(provider, token_param):
    output = dict(summary="ok", observations=[], hypotheses=[], proposals=[], requests=[], disagreements=[], selected_agents=[], audit_decisions=[])
    def handle(request):
        body = json.loads(request.content)
        assert body[token_param] == 3000
        assert body["response_format"]["json_schema"]["schema"]["additionalProperties"] is False
        assert ("store" in body) == (provider == "OpenAI")
        return httpx.Response(200, json={"model": "actual", "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30, "injected": "secret"},
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(output)}}]})
    config = ModelConfig(provider, "requested", "credential")
    answer, actual = call_model(config, {"metrics": {}}, client=httpx.Client(transport=httpx.MockTransport(handle)))
    assert actual == "actual" and answer["_usage"] == {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}


def test_wall_timeout_cancels_slow_response(monkeypatch):
    import asyncio
    import time
    from ops_team import providers
    cancelled = []
    class SlowClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None
        def stream(self, *args, **kwargs):
            return self
        status_code = 200
        async def aiter_bytes(self):
            try:
                await asyncio.sleep(.3)
                yield b"{}"
            finally:
                cancelled.append(True)
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kwargs: SlowClient())
    started = time.monotonic()
    with pytest.raises(ProviderError, match="timeout"):
        call_model(ModelConfig("OpenAI", "mock", "secret-key"), {"metrics": {}}, timeout=.03)
    assert time.monotonic()-started < .25 and cancelled


def test_cross_role_secret_echo_and_payload_key_blocked(validation):
    def leak(config, payload, timeout):
        out = simulated(payload["agent"], payload["task"], payload["metrics"], 2)
        out["summary"] = "secret-key-never-export"
        return out, "actual"
    result = live_run(validation, leak)
    assert not any(e.answer for e in result.exchanges)
    assert "secret-key-never-export" not in build_report(result)


@pytest.mark.parametrize("value,unit,expected", [(-3.2727, "đpt", "-3,27 đpt"), (1.5, "giờ", "1,50 giờ"), (93409.09, "VND/kiện", "93.409,09 VND/kiện")])
def test_report_keeps_material_decimals(value, unit, expected):
    from ops_team.reporting import fmt
    assert fmt(value, unit) == expected


def test_zero_denominator_cannot_be_business_closed():
    tables = demo_tables()
    tables["operations"].loc[:, ["volume", "on_time"]] = 0
    result = live_run(validate(tables), fake_transport(["accept"]))
    assert not result.audit["passed"]
    assert "cost_per_unit" in result.audit["metric_gaps"]


def test_scenarios_reach_specialists_and_auditor(validation):
    captured = []
    scenario = what_if(validation, "2026-09", ["Duc Hoa", "HCM"], "labor", 5, assumption="Giả định riêng trong phiên")
    result = live_run(validation, fake_transport(["accept"], capture=captured), scenarios=[scenario])
    assert all(p["scenarios"][0]["savings_pct"] == 5 for p in captured)
    assert result.scenarios[0]["net_savings_vnd"] == 10.7e6
