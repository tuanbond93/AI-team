import json
from io import BytesIO
import pandas as pd
import pytest
import httpx
from ops_team.demo import demo_tables, workbook_bytes, sample_workbook_bytes
from ops_team.ingestion import read_file, map_columns, validate
from ops_team.metrics import compute, reconcile, ratio, workforce_comparisons
from ops_team.agents import run_team, simulated
from ops_team.providers import ModelConfig, ProviderError, call_model
from ops_team.schema import AGENT_NAMES, SPECS
from ops_team.reporting import build_report


@pytest.fixture
def raw():
    return read_file(workbook_bytes(), "DU_LIEU_DEMO.xlsx")


def run(raw, **kwargs):
    return run_team(validate(raw), "2026-09", "2026-08", ["Duc Hoa", "HCM"],
                    "Chi phí, OT, hiệu suất xe và SLA có bất thường?", data_label="DỮ LIỆU DEMO", **kwargs)


def test_golden_answers(raw):
    v = validate(raw)
    assert v.issues == []
    now = compute(v, "2026-09", ["Duc Hoa", "HCM"])
    before = compute(v, "2026-08", ["Duc Hoa", "HCM"])
    expected = {"cost": 411e6, "budget": 380e6, "budget_delta": 31e6, "volume": 4400,
                "cost_per_unit": 411e6/4400, "sla": 4168/4400*100, "hours": 376,
                "productivity": 4400/376, "ot_rate": 56/376*100, "defect_rate": 68/4400*100,
                "load_weight": 60, "load_volume": 50, "empty_rate": 30, "wait_hours": 8,
                "cost_per_trip": 127e6/4, "backlog": 95}
    for key, answer in expected.items():
        assert now[key].value == pytest.approx(answer), key
        assert all("dòng" in s and "DU_LIEU_DEMO.xlsx" in s for s in now[key].sources)
    assert before["cost"].value == 360e6
    assert before["cost_per_unit"].value == 90000
    assert all(row["difference"] == 0 for row in reconcile(v, "2026-09", ["Duc Hoa", "HCM"]))


def test_demo_uses_exact_delivered_source_file():
    from pathlib import Path
    source = Path(__file__).resolve().parents[1] / "data" / "DU_LIEU_DEMO.xlsx"
    assert sample_workbook_bytes() == source.read_bytes()


def test_join_fanout_and_double_count_prevented(raw):
    v = validate(raw)
    # Naive raw join fans six warehouse cost lines out over two operating dates.
    bad = v.tables["costs"].merge(v.tables["operations"], on=["period", "warehouse"])
    assert bad[bad.period.eq("2026-09")].amount_vnd.sum() == 822e6
    good = compute(v, "2026-09", ["Duc Hoa", "HCM"])
    assert good["cost"].value == 411e6
    assert good["cost"].value != 411e6 + 214e6 + 127e6


@pytest.mark.parametrize("kind,column,value,code", [
    ("costs", "amount_vnd", None, "INVALID_NUMBER"),
    ("operations", "quantity_unit", "kg", "WRONG_UNIT"),
    ("costs", "service_date", "2026-07-28", "PERIOD_MISMATCH"),
    ("costs", "period", "2026-13", "INVALID_PERIOD"),
    ("operations", "volume", -1, "INVALID_NUMBER"),
    ("operations", "on_time", 99999, "INCONSISTENT"),
    ("operations", "volume", 1.5, "FRACTIONAL_COUNT"),
    ("fleet", "loaded_kg", float("inf"), "INVALID_NUMBER"),
    ("kpi", "target", 101, "INVALID_TARGET"),
])
def test_invalid_rows_quarantined(raw, kind, column, value, code):
    raw[kind].loc[0, column] = value
    v = validate(raw)
    assert any(i["code"] == code for i in v.issues)
    assert len(v.rejected[kind]) >= 1
    if kind == "costs":
        assert compute(v, "2026-09", ["Duc Hoa", "HCM"])["cost"].value is None


@pytest.mark.parametrize("kind", list(SPECS))
def test_duplicate_key_excludes_entire_group(raw, kind):
    raw[kind] = pd.concat([raw[kind], raw[kind].iloc[[0]]], ignore_index=True)
    v = validate(raw)
    assert len(v.rejected[kind]) >= 2
    assert any(i["code"] == "DUPLICATE_KEY" for i in v.issues)


def test_document_duplicate_different_record_id(raw):
    extra = raw["costs"].iloc[[0]].copy()
    extra["record_id"] = "different-id"
    raw["costs"] = pd.concat([raw["costs"], extra])
    assert len(validate(raw).rejected["costs"]) == 2


def test_missing_reference_preserves_actual_cost(raw):
    raw["costs"].loc[6, "document_ref"] = None
    v = validate(raw)
    assert any(i["code"] == "MISSING_VALUE" for i in v.issues)
    assert compute(v, "2026-09", ["Duc Hoa", "HCM"])["cost"].value == 411e6


def test_zero_denominator_unknown_not_zero(raw):
    raw["operations"].loc[:, ["volume", "on_time"]] = 0
    v = validate(raw)
    m = compute(v, "2026-09", ["Duc Hoa", "HCM"])
    assert m["volume"].value == 0
    assert m["sla"].value is None
    assert m["cost_per_unit"].value is None
    assert ratio(1, 0) is None


def test_missing_warehouse_or_budget_category_not_zero(raw):
    raw["budgets"] = raw["budgets"][~((raw["budgets"].period == "2026-09") & (raw["budgets"].category == "rent"))]
    m = compute(validate(raw), "2026-09", ["Duc Hoa", "HCM"])
    assert m["budget_delta"].value is None
    assert compute(validate(raw), "2026-09", ["unknown"])["cost"].value is None


def test_missing_table_or_column_is_reported(raw):
    del raw["fleet"]
    raw["workforce"] = raw["workforce"].drop(columns=["ot_hours"])
    v = validate(raw)
    m = compute(v, "2026-09", ["Duc Hoa", "HCM"])
    assert m["hours"].value is None
    assert m["empty_rate"].value is None
    assert {i["code"] for i in v.issues} >= {"MISSING_TABLE", "MISSING_COLUMN"}


def test_workforce_stage_comparison_and_snapshot(raw):
    groups = workforce_comparisons(validate(raw), "2026-09", "2026-08", ["Duc Hoa", "HCM"])
    assert len(groups) == 2
    dh = next(g for g in groups if g["warehouse"] == "Duc Hoa")
    assert dh["periods"]["2026-09"]["productivity"] == 12
    assert dh["stage"] == "sort" and dh["goods_type"] == "parcel"
    # Inconsistent snapshot dates must not be presented as one total backlog.
    raw["operations"].loc[7, "date"] = "2026-09-21"
    assert compute(validate(raw), "2026-09", ["Duc Hoa", "HCM"])["backlog"].value is None


def test_csv_mapping_provenance_and_read_only(tmp_path):
    df = demo_tables()["costs"].rename(columns={"amount_vnd": "Số tiền"})
    data = df.to_csv(index=False).encode("utf-8-sig")
    original = tmp_path / "input.csv"
    original.write_bytes(data)
    parsed = read_file(data, str(original))["CSV"]
    mapped = map_columns(parsed, "costs", {c: "Số tiền" if c == "amount_vnd" else c for c in SPECS["costs"].columns})
    assert "input.csv" in mapped.iloc[0]._source and "dòng 2" in mapped.iloc[0]._source
    assert original.read_bytes() == data
    with pytest.raises(ValueError):
        map_columns(parsed, "costs", {})
    forged = df.assign(_source="forged").to_csv(index=False).encode()
    assert "forged" not in read_file(forged, "x.csv")["CSV"]._source.tolist()


def test_demo_cross_requests_auditor_return_and_export(raw):
    result = run(raw)
    assert result.mode == "Demo" and result.calls == 0
    assert {e.receiver for e in result.exchanges} == set(AGENT_NAMES)
    assert any(e.sender == "Cost" and e.receiver == "Workforce" for e in result.exchanges)
    assert any(e.sender == "Cost" and e.receiver == "Fleet" for e in result.exchanges)
    assert result.audit["returned"] and result.audit["review_complete"]
    assert not result.audit["passed"]  # Operations disagreement remains open.
    assert any(e.sender == "Auditor" and e.receiver == "Cost" and e.round >= 1 for e in result.exchanges)
    assert len([e for e in result.exchanges if e.receiver == "Auditor"]) >= 2
    assert all(e.round <= 2 for e in result.exchanges)
    assert result.disagreements
    report = build_report(result)
    assert "DỮ LIỆU DEMO" in report and "MÔ PHỎNG" in report
    assert "E001" in report and "dòng" in report and "CHỜ DUYỆT" in report
    assert result.question not in report  # No session prompt persistence in exports.
    for number in range(1, 7):
        assert f"## {number}." in report


def configs():
    return {name: ModelConfig("OpenAI", f"test-{name}", "secret-do-not-log") for name in AGENT_NAMES}


def test_live_requires_activation_and_configuration(raw):
    with pytest.raises(ValueError):
        run(raw, mode="Live", configs=configs())
    with pytest.raises(ValueError):
        run(raw, mode="Live", activated=True)


def test_live_mock_actual_models_and_budget(raw):
    def mock(config, payload, timeout):
        assert set(payload["team_roster"]) == set(AGENT_NAMES)
        name = payload["agent"]
        out = simulated(name, payload["task"], payload["metrics"], 2 if "Bổ sung bằng chứng" in payload["task"] else 0)
        return out, config.model + "-actual"
    result = run(raw, mode="Live", activated=True, configs=configs(), max_calls=3, transport=mock)
    assert result.calls == 3
    assert all(e.model.endswith("-actual") for e in result.exchanges if e.answer)
    assert any(e.status == "Dừng giới hạn" for e in result.exchanges)
    assert not result.audit["passed"]
    report = build_report(result)
    assert "secret-do-not-log" not in report
    assert "LIVE" in report and "Chưa đủ kết quả" in report


def test_live_failures_never_fallback(raw):
    def fail(*args, **kwargs):
        raise ProviderError("API timeout; không dùng phản hồi giả.")
    result = run(raw, mode="Live", activated=True, configs=configs(), transport=fail)
    assert result.calls == 3  # Coordinator plan, Auditor, Coordinator final; no fabricated plan.
    assert not any(e.answer or "MÔ PHỎNG" in e.model for e in result.exchanges)
    assert not result.audit["passed"]


@pytest.mark.parametrize("status,content", [(401, {}), (200, {"choices": []}), (200, {"choices": [{"message": {"content": "bad JSON"}}]})])
def test_provider_http_and_json_errors_redacted(status, content):
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, json=content)))
    with pytest.raises(ProviderError) as error:
        call_model(ModelConfig("OpenAI", "mock", "private-key"), {"metrics": {}}, client=client)
    assert "private-key" not in str(error.value)


def test_provider_success_validates_schema_and_actual_model():
    output = dict(summary="test", observations=[], hypotheses=[], proposals=[], requests=[], disagreements=[], selected_agents=[])
    def handle(request):
        payload = json.loads(request.content)
        assert payload["store"] is False and payload["response_format"]["type"] == "json_schema"
        return httpx.Response(200, json={"model": "actual-version", "choices": [{"message": {"content": json.dumps(output)}}]})
    client = httpx.Client(transport=httpx.MockTransport(handle))
    _, actual = call_model(ModelConfig("OpenAI", "requested", "private-key"), {"metrics": {}}, client=client)
    assert actual == "actual-version"


def test_provider_unknown_evidence_and_timeout():
    output = dict(summary="test", observations=[{"text": "unproven", "evidence_ids": ["invented"]}],
        hypotheses=[], proposals=[], requests=[], disagreements=[], selected_agents=[])
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"model": "mock", "choices": [{"message": {"content": json.dumps(output)}}]})))
    with pytest.raises(ProviderError, match="bằng chứng"):
        call_model(ModelConfig("OpenAI", "mock", "key"), {"metrics": {}}, client=client)
    def fail(request):
        raise httpx.ReadTimeout("do not echo secret", request=request)
    client = httpx.Client(transport=httpx.MockTransport(fail))
    with pytest.raises(ProviderError, match="timeout"):
        call_model(ModelConfig("OpenAI", "mock", "key"), {"metrics": {}}, client=client)


def test_two_round_ceiling(raw):
    result = run(raw, max_rounds=0)
    assert not result.audit["passed"]
    assert all(e.round == 0 for e in result.exchanges)


def test_coordinator_selects_specialists_from_question(raw):
    result = run_team(validate(raw), "2026-09", "2026-08", ["HCM"], "SLA kỳ này ra sao?")
    assert not any(e.receiver in ("Cost", "Fleet", "Workforce") for e in result.exchanges)


def test_live_auditor_can_request_reasoning_rework(raw):
    def mock(config, payload, timeout):
        name = payload["agent"]
        out = simulated(name, payload["task"], payload["metrics"], 2)
        if name == "Auditor":
            out["requests"] = [{"to": "Cost", "question": "Tách giả thuyết và bằng chứng của nhận định chi phí.", "evidence_ids": ["cost"]}]
        return out, "mock-actual"
    result = run(raw, mode="Live", activated=True, configs=configs(), transport=mock)
    assert any(e.sender == "Auditor" and e.receiver == "Cost" for e in result.exchanges)
    assert result.audit["returned"]


def test_provider_blocks_echoed_credentials_and_session_instructions():
    config = ModelConfig("OpenAI", "test-model", "private-key", "private session instructions must not appear in reports")
    for leaked in (config.api_key, config.instructions):
        output = dict(summary=leaked, observations=[], hypotheses=[], proposals=[], requests=[], disagreements=[], selected_agents=[])
        client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200,
            json={"model": "test-model-actual", "choices": [{"message": {"content": json.dumps(output)}}]})))
        with pytest.raises(ProviderError) as error:
            call_model(config, {"metrics": {}}, client=client)
        assert leaked not in str(error.value)
