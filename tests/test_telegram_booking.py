from datetime import datetime, timezone
import json
import csv
import threading
import time
import httpx
import pandas as pd
import pytest
from ops_team.supra.booking import parse_booking, booking_context
from ops_team.telegram_assistant import AssistantResponder, TZ
from ops_team.telegram_state import BusinessState, GROUP_ID
from ops_team.telegram_snapshot import ingest_snapshot, load_snapshot
from ops_team.telegram_sheets import read_sheets
from ops_team.telegram_agent import TelegramAgent
from ops_team.providers import ModelConfig, FREE_MODELS
from ops_team.schema import AGENT_NAMES

FILE = "synthetic_sheet_id_for_booking_123"
AREA = "'DC PHÚ THỌ'!A1:AK10000"
HEADERS = ["Ngày LTC", "Mã đơn GHN", "Trạng thái", "Ngày xuất", "Qty (Units)", "PCS/DROP/Tỉnh", "Tỉnh", "Tên Tài Xế", "SĐT", "BIỂN SỐ XE"]


def payload():
    return {"version": 1, "source": "GOOGLE_SHEETS_CONNECTOR", "spreadsheet_id": FILE,
        "tab": "DC PHÚ THỌ", "gid": 0, "range": AREA, "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "headers": HEADERS, "rows": [
            ["10/10/2026", "GHN001", "delivered", "10/10/2026", 100, 999, "Hà Nội", "PRIVATE_DRIVER_A", "PRIVATE_PHONE_A", "PRIVATE_PLATE_A"],
            ["10/10/2026", "GHN001", "delivered", "10/10/2026", 100, 999, "Phú Thọ", "PRIVATE_DRIVER_B", "PRIVATE_PHONE_B", "PRIVATE_PLATE_B"],
            ["11/10/2026", "", "PRIVATE_STATUS_VALUE", "11/10/2026", 500, 999, "Yên Bái", "PRIVATE_DRIVER_C", "PRIVATE_PHONE_C", "PRIVATE_PLATE_C"],
            ["11/10/2026", "GHN003", "delivered", "", 900, 999, "Lào Cai", "PRIVATE_DRIVER_D", "PRIVATE_PHONE_D", "PRIVATE_PLATE_D"],
        ]}


def pins(state):
    facts = {"sheets_id": FILE, "sheets_tab": "DC PHÚ THỌ", "sheets_gid": "0",
        "sheets_ranges": json.dumps({"supra_booking": AREA}), "source_id": "snapshot",
        "customer": "OWNER PROVIDED DC scope", "warehouses": "DC PHÚ THỌ", "period": "2026-10", "cutoff": "23:59",
        "report_time": "13:00", "timezone": "Asia/Ho_Chi_Minh", "goals": "data quality", "contacts": "synthetic owner"}
    for key, value in facts.items():
        state.remember(key, value, 123, 0, GROUP_ID)
    return facts


def settings():
    return {"owner_id": 123, "models": {}, "bot_token": "fake-not-used", "allowed_folder": ""}


def test_booking_projection_discards_personal_and_ambiguous_columns_and_does_not_filter_destinations():
    data = payload()
    frame = pd.DataFrame(data["rows"], columns=HEADERS)
    frame["_source"] = [f"synthetic / dòng {r}" for r in range(2, 6)]
    parsed = parse_booking(frame)
    safe = parsed.rows.to_json()
    assert all(secret not in safe for secret in ("PRIVATE_DRIVER", "PRIVATE_PHONE", "PRIVATE_PLATE", "PRIVATE_STATUS_VALUE"))
    context = booking_context(parsed, "2026-10", ["DC Phú Thọ"], datetime(2026, 10, 20, 12, tzinfo=TZ), {"tab": "DC PHÚ THỌ", "digest": "a"*64})
    assert context["rows"] == 3 and context["metrics"]["booking.unique_orders"]["value"] == 1
    assert context["metrics"]["booking.duplicate_rows"]["value"] == 1
    assert context["metrics"]["booking.missing_ids"]["value"] == 1 and context["missing_dates"] == 1
    assert context["metrics"]["booking.status.delivered"]["value"] == 2
    assert context["metrics"]["booking.unique_orders"]["unit"] == "mã GHN"
    assert context["metrics"]["booking.conflicting_status_ids"]["unit"] == "mã GHN"
    assert context["metrics"]["booking.consistent_delivered_ids"]["unit"] == "mã GHN"
    assert "không lọc tỉnh đích" in context["scope"]
    assert not any(key in context["metrics"] for key in ("cost", "volume", "sla", "ot_rate", "load_weight"))
    with pytest.raises(ValueError, match="đúng tên tab"):
        booking_context(parsed, "2026-10", ["Phú Thọ"], datetime.now(TZ), {"tab": "DC PHÚ THỌ", "digest": "a"*64})


def test_snapshot_stdin_ingest_projects_before_disk_and_reports_only_aggregates(tmp_path):
    state = BusinessState(tmp_path)
    try:
        facts = pins(state)
        result = ingest_snapshot(payload(), state, 123)
        assert result["safe_rows"] == 4 and result["state"] == "INGESTED_NOT_SCHEDULED"
        checked, source = load_snapshot(state, facts)
        assert len(checked.rows) == 4 and source["gid"] == "0"
        for path in tmp_path.rglob("*"):
            if path.is_file():
                blob = path.read_bytes()
                assert b"PRIVATE_DRIVER" not in blob and FILE.encode() not in blob
        assistant = AssistantResponder(state, settings())
        report = assistant.report()
        assert "Dòng booking có ngày trong kỳ: 3 dòng" in report and "Mã GHN khác nhau đọc được: 1 mã GHN" in report
        assert "cách ngày chốt hơn một ngày" in report and "chưa dùng AI" in report
        assert "Tổng doanh thu B2B: N/A" in report and "chưa đối soát" in report
        assert "PRIVATE_" not in report and "GHN001" not in report
        assert len(report)<2200 and "BƯỚC TIẾP:" in report
        assert "Chưa đủ căn cứ tính chi phí, OT, số kiện, khối lượng, tải xe, chuyến/điểm giao hay SLA" in assistant._report_details
        assert "không lọc tỉnh đích" in assistant._report_details
        plan, code, missing = assistant.plan()
        assert not missing and "booking" in plan and "13:00" in plan
        assert not state.control("schedule_enabled", False)
    finally:
        state.close()


@pytest.mark.parametrize("key,value", [("gid", 999), ("tab", "ANOTHER DC"), ("range", "'DC PHÚ THỌ'!A1:Z100"),
                                      ("spreadsheet_id", "another_synthetic_file_123456")])
def test_snapshot_cannot_change_owner_source_pin(tmp_path, key, value):
    state = BusinessState(tmp_path)
    try:
        pins(state)
        item = payload()
        item[key] = value
        with pytest.raises(ValueError, match="không khớp"):
            ingest_snapshot(item, state, 123)
        assert not (tmp_path/"snapshot-latest.dpapi").exists()
    finally:
        state.close()


def test_booking_model_and_auditor_receive_only_aggregate_evidence():
    raw = payload()
    frame = pd.DataFrame(raw["rows"], columns=HEADERS)
    frame["_source"] = [f"synthetic dòng {r}" for r in range(2, 6)]
    context = booking_context(parse_booking(frame), "2026-10", ["DC PHÚ THỌ"], datetime.now(TZ), {"tab": "DC PHÚ THỌ", "digest": "b"*64})
    sent = []
    def transport(config, message, **kwargs):
        encoded = json.dumps(message)
        assert "PRIVATE_" not in encoded and "GHN001" not in encoded
        assert all(key.startswith("booking.") for key in message["metrics"])
        sent.append(message)
        answer = dict(summary="Synthetic quality review", observations=[], hypotheses=[], proposals=[], requests=[], disagreements=[], selected_agents=[], audit_decisions=[])
        if message["agent"] == "Auditor":
            answer["audit_decisions"] = [{"task_id": message["review_tasks"][0]["task_id"], "verdict": "unresolved", "reason": "Synthetic missing business rules", "evidence_ids": ["booking.rows"]}]
        return answer, "fake-model"
    configs = {n: ModelConfig("Groq", FREE_MODELS[0], "fake-key", free_pilot=True, free_account_confirmed=True) for n in AGENT_NAMES}
    agent = TelegramAgent(configs, context, transport=transport)
    from ops_team.telegram_bridge import Incoming
    result = agent.answer(Incoming(GROUP_ID, 123, 1, None, "team", "Synthetic booking quality question"))
    assert len(sent) == 2 and "nhận định chưa được chấp nhận" in result
    assert "Synthetic missing business rules" in result and "Operations + Auditor" not in result


def test_sheets_auth_denied_never_reads_values_or_falls_back():
    from ops_team.telegram_sheets import SCOPES
    account = {"type": "service_account", "client_email": "fake@project.iam.gserviceaccount.com", "private_key": "-----BEGIN PRIVATE KEY-----FAKE", "token_uri": "https://oauth2.googleapis.com/token"}
    facts = {"sheets_id": FILE, "sheets_tab": "DC PHÚ THỌ", "sheets_gid": "0", "sheets_ranges": json.dumps({"supra_booking": AREA})}
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(403, text="PRIVATE RESPONSE")
    with pytest.raises(ValueError, match="quyền Viewer"):
        read_sheets(facts, account, client=httpx.Client(transport=httpx.MockTransport(handle)), token_provider=lambda _: "fake-access")
    assert len(calls) == 1 and ":batchGet" not in calls[0].url.path
    assert SCOPES == ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def test_status_conflicts_do_not_pick_last_status_or_count_duplicate_as_new_orders():
    item = payload()
    item["rows"][1][2] = "delivering"
    frame = pd.DataFrame(item["rows"], columns=HEADERS)
    frame["_source"] = [f"synthetic dòng {r}" for r in range(2, 6)]
    context = booking_context(parse_booking(frame), "2026-10", ["DC PHÚ THỌ"], datetime.now(TZ), {"tab": "DC PHÚ THỌ", "digest": "a"*64})
    metrics = context["metrics"]
    assert metrics["booking.unique_orders"]["value"] == 1 and metrics["booking.duplicate_rows"]["value"] == 1
    assert metrics["booking.conflicting_status_ids"]["value"] == 1
    assert metrics["booking.consistent_delivered_ids"]["value"] == 0
    assert metrics["booking.status.delivered"]["value"] == 1


def test_browser_csv_file_guard_37_columns_count_and_no_pii_persistence(tmp_path):
    from ops_team.supra.booking import BOOKING_HEADERS
    from ops_team.telegram_snapshot import ingest_csv_file
    state = BusinessState(tmp_path/"vault")
    try:
        facts = pins(state)
        item = payload()
        descriptor = {k: item[k] for k in ("version", "spreadsheet_id", "tab", "gid", "range", "retrieved_at")}
        descriptor.update(source="GOOGLE_BROWSER_VIEWER_EXPORT", source_url=f"https://docs.google.com/spreadsheets/d/{FILE}/edit#gid=0", expected_data_rows=2)
        path = tmp_path/"synthetic.csv"
        def write(headers):
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(headers)
                if headers == BOOKING_HEADERS:
                    for status in ("delivered", "delivering"):
                        row = {"Ngày LTC": "10/10/2026", "Ngày xuất": "10/10/2026", "Mã đơn GHN": "GHN_FAKE_001", "Trạng thái": status,
                               "Tên Tài Xế": "PRIVATE_DRIVER_SENTINEL", "SĐT": "PRIVATE_PHONE_SENTINEL"}
                        writer.writerow([row.get(h, "") for h in headers])
        write([f"col{i}" for i in range(57)])
        with pytest.raises(ValueError, match="37 cột"):
            ingest_csv_file(path, descriptor, state, 123)
        assert not (state.root/"snapshot-latest.dpapi").exists()
        write(BOOKING_HEADERS)
        result = ingest_csv_file(path, descriptor, state, 123)
        assert result["safe_rows"] == 2 and result["source_counters"]["conflicting_status_ids"] == 1
        safe, source = load_snapshot(state, facts)
        assert source["name"] == "GOOGLE_BROWSER_VIEWER_SNAPSHOT"
        assert "PRIVATE_" not in safe.rows.to_json()
        for artifact in state.root.rglob("*.dpapi"):
            assert b"PRIVATE_" not in artifact.read_bytes()
        descriptor["expected_data_rows"] = 99
        with pytest.raises(ValueError, match="Số dòng"):
            ingest_csv_file(path, descriptor, state, 123)
    finally:
        state.close()


def test_booking_plan_can_progress_without_goals_contacts_and_labels_derived_assumptions(tmp_path):
    state = BusinessState(tmp_path)
    try:
        pins(state)
        for key in ("goals", "contacts", "customer", "period", "cutoff", "warehouses"):
            state.forget(key, 123, 1)
        ingest_snapshot(payload(), state, 123)
        assistant = AssistantResponder(state, settings())
        text, code, missing = assistant.plan()
        assert not missing and "GIẢ ĐỊNH CHỜ OWNER" in text and "period=" in text and "cutoff=" in text
        assert "Checklist bổ sung" in text and "Đầu mối" in text
        assert "period" not in state.facts() and "cutoff" not in state.facts()
    finally:
        state.close()


def test_browser_pipeline_import_then_common_scheduled_delivery_no_worker_autosend(tmp_path, monkeypatch):
    from ops_team.telegram_snapshot import send_scheduled
    from ops_team.telegram_bridge import TelegramAPI
    state = BusinessState(tmp_path)
    try:
        facts = pins(state)
        state.remember("source_id", "browser_snapshot", 123, 0, GROUP_ID)
        now = datetime.now(TZ)
        state.remember("report_time", now.strftime("%H:%M"), 123, 0, GROUP_ID)
        item = payload()
        item["source"] = "GOOGLE_BROWSER_VIEWER_EXPORT"
        ingest_snapshot(item, state, 123)
        config = settings()
        config["chat_id"] = GROUP_ID
        config["bot_token"] = "123456789:"+"y"*35
        assert send_scheduled(state, config)["state"] == "NOT_PAIRED"
        state.set_control("paired", {"chat_id": GROUP_ID, "owner_id": 123})
        state.set_control("worker_enabled", True)
        assistant = AssistantResponder(state, config)
        _, code, missing = assistant.plan()
        assert not missing
        assistant.answer(__import__('ops_team.telegram_bridge', fromlist=['Incoming']).Incoming(GROUP_ID, 123, 1, None, "enable", code))
        sent = []
        assert assistant.tick(lambda *args: sent.append("worker"), now=now)["state"] == "AWAITING_BROWSER_EXPORT_PIPELINE"
        def call(self, method, **params):
            assert method == "sendMessage"  # No getUpdates/second polling process.
            sent.append(params)
            return {"message_id": 99}
        monkeypatch.setattr(TelegramAPI, "call", call)
        first = send_scheduled(state, config, now=now)
        assert first["state"] == "SENT" and sent
        count = len(sent)
        item["retrieved_at"] = datetime.now(timezone.utc).isoformat()
        ingest_snapshot(item, state, 123)
        assert state.control("approved_plan") == code and state.control("schedule_enabled")
        assert send_scheduled(state, config, now=now)["state"] == "DUPLICATE_JOB" and len(sent) == count
        assert all("reply_parameters" not in params for params in sent)
    finally:
        state.close()


def test_booking_real_provider_adapter_fits_free_minute_cap_without_truncating_review(monkeypatch):
    from types import SimpleNamespace
    import ops_team.providers as providers
    import ops_team.telegram_agent as telegram
    from ops_team.limits import estimate_tokens
    statuses = ["delivered", "delivering", "transporting", "ready_to_pick", "picking", "picked", "storing", "return", "returned", "cancel", "cancelled", "delivery_fail", "waiting", "unknown"]
    frame = pd.DataFrame({"Ngày LTC": ["10/10/2026"]*len(statuses), "Ngày xuất": ["10/10/2026"]*len(statuses),
        "Mã đơn GHN": ["SYNTHETIC"+str(i) for i in range(len(statuses))], "Trạng thái": statuses,
        "_source": ["GoogleConnector "+"s"*44+f" / 'DC PHÚ THỌ'!A1:AK100000 / dòng {i+2}" for i in range(len(statuses))]})
    context = booking_context(parse_booking(frame), "2026-10", ["DC PHÚ THỌ"], datetime.now(TZ), {"tab": "DC PHÚ THỌ", "digest": "c"*64})
    tick, bodies = [1000.0], []
    fake_time = SimpleNamespace(monotonic=lambda: tick[0])
    monkeypatch.setattr(providers, "time", fake_time)
    monkeypatch.setattr(telegram, "time", fake_time)
    async def fetch(url, headers, body, timeout):
        assert estimate_tokens(body) <= 7500
        assert body["max_completion_tokens"] == 1500
        data = json.loads(body["messages"][0]["content"])
        bodies.append(data)
        for key, metric in data["metrics"].items():
            for field in ("value", "label", "formula", "unit", "source_count", "source_sample_only"):
                assert metric[field] == context["metrics"][key][field]
        assert data["source_row_catalog"] and data["metric_context"]["period"] == "2026-10"
        summary = "Booking chỉ phản ánh ngày xuất và trạng thái khai báo. Cần đối soát mã trùng, ngày thiếu và quy tắc chuyến/drop trước khi suy luận năng suất hoặc chi phí. "*3
        result = dict(summary=summary,
            observations=[{"text": "Dữ liệu tổng hợp phục vụ kiểm tra chất lượng nguồn, chưa xác nhận hoàn tất hoặc giao đúng cam kết.", "evidence_ids": ["booking.rows"]}],
            hypotheses=["Chưa xác minh nguyên nhân trạng thái khác nhau ở cùng mã."], proposals=["Đối chiếu lịch sử sự kiện với đầu mối vận hành trước khi kết luận."],
            requests=[{"to": "Operations", "question": "Anh xác nhận giúp khóa chuyến/drop và cách phân biệt dòng lặp với cập nhật trạng thái của cùng mã GHN?", "evidence_ids": ["booking.rows"]},
                      {"to": "Accounting", "question": "Đầu mối nào đối chiếu chứng từ chi phí và kỳ hạch toán trước khi tính P&L?", "evidence_ids": []}],
            disagreements=[], selected_agents=[], audit_decisions=[])
        if data["agent"] == "Auditor":
            assert data["review_tasks"][0]["answer"]["summary"] == summary
            assert len(data["review_tasks"][0]["answer"]["requests"]) == 2
            result["audit_decisions"] = [{"task_id": data["review_tasks"][0]["task_id"], "verdict": "unresolved", "reason": "Synthetic", "evidence_ids": ["booking.rows"]}]
        return 200, json.dumps({"model": "fake-model", "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}]}).encode(), None
    monkeypatch.setattr(providers, "_fetch_async", fetch)
    configs = {n: ModelConfig("Groq", FREE_MODELS[0], "fake-key", free_pilot=True, free_account_confirmed=True) for n in AGENT_NAMES}
    agent = TelegramAgent(configs, context)
    agent._budget.clock = fake_time.monotonic
    agent._budget.sleep = lambda seconds: tick.__setitem__(0, tick[0]+seconds)
    from ops_team.telegram_bridge import Incoming
    for index in range(3):
        chat = agent.answer(Incoming(GROUP_ID, 123, index+1, None, "operations",
            "Từ các số booking này, tôi nên hỏi thêm những trường dữ liệu và quy tắc vận hành nào trước khi phân tích?"))
        assert "Anh xác nhận giúp" in chat
        assert "bản chụp, không phải dữ liệu trực tuyến" in chat
    assert len(bodies[2]["conversation"]) == 2 and all(t["is_excerpt"] for t in bodies[2]["conversation"])
    result = agent.answer(Incoming(GROUP_ID, 123, 1, None, "team", "Synthetic review"))
    assert len(bodies) == 5 and agent._budget.attempts == 5 and tick[0] > 1060, result
    assert "nhận định chưa được chấp nhận" in result


def test_current_month_plan_is_stable_across_rollover_but_explicit_period_is_fixed(tmp_path, monkeypatch):
    import ops_team.telegram_assistant as module
    clock = [datetime(2026, 10, 25, 14, tzinfo=TZ)]
    class ClockDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]
    monkeypatch.setattr(module, "datetime", ClockDateTime)
    state = BusinessState(tmp_path)
    try:
        pins(state)
        state.forget("period", 123, 1)
        ingest_snapshot(payload(), state, 123)
        assistant = AssistantResponder(state, settings())
        october_text, october_code, _ = assistant.plan()
        assert "CURRENT_MONTH" in october_text and assistant._effective_facts()[0]["period"] == "2026-10"
        clock[0] = datetime(2026, 11, 25, 14, tzinfo=TZ)
        november_text, november_code, missing = assistant.plan()
        assert november_code == october_code and assistant._effective_facts()[0]["period"] == "2026-11"
        assert "đang 2026-11" in november_text and missing
        assert "không có dòng" in assistant.report()
        state.remember("period", "2026-10", 123, 2, GROUP_ID)
        fixed = assistant.plan()[1]
        assert fixed != november_code and assistant._effective_facts()[0]["period"] == "2026-10"
        clock[0] = datetime(2026, 12, 25, 14, tzinfo=TZ)
        assert assistant.plan()[1] == fixed and assistant._effective_facts()[0]["period"] == "2026-10"
    finally:
        state.close()


def _booking_audit_fixture(verdict="unresolved", mismatched=False, disagreement=False):
    from ops_team.supra.agent import review_booking
    raw = payload()
    frame = pd.DataFrame(raw["rows"], columns=HEADERS)
    frame["_source"] = [f"synthetic dòng {r}" for r in range(2, 6)]
    source = {"tab": "DC PHÚ THỌ", "digest": "d"*64, "name": "GOOGLE_CONNECTOR_SNAPSHOT",
              "retrieved_at": time.time(), "created": time.time()}
    data = parse_booking(frame)
    context = booking_context(data, "2026-10", ["DC PHÚ THỌ"], datetime.now(TZ), source)
    configs = {n: ModelConfig("Groq", FREE_MODELS[0], "fake-key", free_pilot=True, free_account_confirmed=True) for n in AGENT_NAMES}
    reason = "Cần xác minh định nghĩa ngày và quy tắc mã GHN lặp trước khi chốt diễn giải."
    def transport(config, message, **kwargs):
        answer = dict(summary="Diễn giải Operations tổng hợp.", observations=[], hypotheses=[], proposals=[],
                      requests=[], disagreements=[], selected_agents=[], audit_decisions=[])
        if message["agent"] == "Auditor":
            task_id = message["review_tasks"][0]["task_id"]
            answer.update(summary="Nhận xét độc lập của Auditor.",
                audit_decisions=[{"task_id": task_id+"-OTHER" if mismatched else task_id, "verdict": verdict,
                                  "reason": reason, "evidence_ids": ["chief.revenue" if "chief.revenue" in message["metrics"] else "booking.rows"]}],
                disagreements=["Định nghĩa trạng thái nguồn chưa thống nhất."] if disagreement else [],
                proposals=["Đối chiếu lại các dòng trùng với đầu mối vận hành."],
                requests=[{"to": "Operations", "question": "Anh xác nhận giúp ý nghĩa Ngày xuất và cách xử lý nhiều trạng thái trên cùng mã?", "evidence_ids": []}])
        return answer, "fake-model"
    result = review_booking(context["metrics"], "Synthetic booking audit", configs["Operations"], configs["Auditor"], transport)
    agent = TelegramAgent(configs, context, transport=transport)
    return data, source, result, agent, reason


@pytest.mark.parametrize("verdict,mismatched,disagreement,accepted", [
    ("revise", False, True, False), ("unresolved", False, True, False),
    ("accept", True, False, False), ("accept", False, True, False), ("accept", False, False, True),
])
def test_booking_preserves_and_renders_exact_auditor_reason_without_closing_disagreements(verdict, mismatched, disagreement, accepted):
    from ops_team.telegram_bridge import Incoming
    _, _, result, agent, reason = _booking_audit_fixture(verdict, mismatched, disagreement)
    assert result["review_complete"] is accepted
    assert result["audit_answer"]["summary"] == "Nhận xét độc lập của Auditor."
    decision = result["audit_decisions"][0]
    assert decision["reason"] == reason and decision["verdict"] == verdict
    assert (decision["task_id"] == result["task_id"]) is (not mismatched)
    request = Incoming(GROUP_ID, 123, 1, None, "team", "Synthetic booking audit")
    rendered = agent.answer(request)
    detail = agent.details(request)
    assert result["task_id"] not in rendered and result["task_id"] in detail and reason in rendered
    assert agent.last_presentation["audit"]["audit_decisions"] == result["audit_decisions"]
    assert "Đối chiếu lại các dòng trùng" in rendered and "ý nghĩa Ngày xuất" in rendered
    if not accepted:
        assert "nhận định chưa được chấp nhận" in rendered
        assert "Operations chưa được chấp nhận" in detail
    if mismatched:
        assert "chưa khớp bản đang xét" in rendered and "Nhận xét đang trỏ sang bản khác" in rendered
        assert "Quyết định không áp dụng" in detail and "Chưa có quyết định cho đúng phiên bản" in detail
    if disagreement:
        assert "Định nghĩa trạng thái nguồn chưa thống nhất" in rendered and "Bất đồng còn mở" in detail
    if verdict == "revise":
        assert "Cần sửa:" in rendered and "Yêu cầu sửa:" in detail
    if verdict == "unresolved":
        assert "Cần làm rõ:" in rendered and "Chưa chốt:" in detail


def test_booking_report_does_not_run_ai_or_reuse_older_auditor_record(tmp_path, monkeypatch):
    data, source, result, agent, reason = _booking_audit_fixture("unresolved", disagreement=True)
    state = BusinessState(tmp_path)
    try:
        pins(state)
        config = settings()
        config["models"] = {"Operations": {}}
        assistant = AssistantResponder(state, config)
        monkeypatch.setattr(assistant, "_get_agent", lambda: pytest.fail("Booking thiếu finance không cần tạo model agent"))
        report = assistant._booking_report(data, source, assistant._facts(), "2026-10", ["DC PHÚ THỌ"], datetime.now(TZ))
        assert result["task_id"] not in report and reason not in report
        assert "chưa dùng AI" in report
        assert "Tổng doanh thu B2B: N/A" in report
        from ops_team.telegram_bridge import Incoming
        detail = assistant.answer(Incoming(GROUP_ID, 123, 2, None, "details", ""))
        assert "CHIEF-CODE-" in detail and "HTTP attempts cho yêu cầu này: 0" in detail
        assert reason not in detail
    finally:
        state.close()
