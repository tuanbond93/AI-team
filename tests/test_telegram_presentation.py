"""Synthetic presentation checks; no production files, provider or Telegram traffic."""
from copy import deepcopy
from datetime import datetime
import json
import re
import pandas as pd
import pytest

from ops_team.supra.booking import parse_booking, booking_context
from ops_team.telegram_agent import TelegramAgent
from ops_team.telegram_assistant import AssistantResponder, TZ
from ops_team.telegram_bridge import Incoming, chunks_for_telegram
from ops_team.telegram_presentation import guarded_booking_text, conversation
from ops_team.telegram_state import BusinessState, GROUP_ID
from ops_team.providers import ModelConfig, FREE_MODELS
from ops_team.schema import AGENT_NAMES


def context():
    frame = pd.DataFrame({"Ngày LTC": ["10/10/2026"]*3, "Ngày xuất": ["10/10/2026"]*3,
        "Mã đơn GHN": ["SYNTHETIC_A", "SYNTHETIC_A", "SYNTHETIC_B"],
        "Trạng thái": ["delivered", "delivering", "unknown"],
        "_source": [f"synthetic / dòng {i}" for i in range(2, 5)]})
    return booking_context(parse_booking(frame), "2026-10", ["DC PHÚ THỌ"], datetime(2026, 10, 25, 13, tzinfo=TZ),
        {"tab": "DC PHÚ THỌ", "digest": "e"*64, "retrieved_at": datetime(2026, 10, 25, 12, 55, tzinfo=TZ).timestamp(), "created": 1})


def answer(**updates):
    result = dict(summary="Cần xác nhận quy tắc cập nhật trước khi chốt booking.", observations=[],
        hypotheses=[], proposals=["Đối chiếu lịch sử cập nhật với đầu mối vận hành."],
        requests=[{"to": "Operations", "question": "Một mã có nhiều dòng là chi tiết hàng hay lịch sử trạng thái?", "evidence_ids": []}],
        disagreements=[], selected_agents=[], audit_decisions=[])
    result.update(updates)
    return result


@pytest.mark.parametrize("claim", [
    "Có 2 mã đơn hợp lệ.", "Có 1 dòng trùng thừa.", "Xóa dòng booking bị lặp.",
    "Giữ dòng cuối cho mỗi mã GHN.", "Có thể tính chuyến xe từ số mã.",
    "Đủ dữ liệu tính tải trọng.", "Unknown là hàng chưa giao.", "Chưa xác định tức là chưa hoàn tất.",
    "Delivered đạt SLA.", "Đã giao là hoàn tất thực tế.", "SLA chính là delivered.",
    "Chưa có chi phí, nhưng 7 mã đơn hợp lệ có thể dùng tính chuyến xe.",
    "Không có khối lượng, nhưng delivered đạt SLA.",
])
def test_unsupported_claim_is_replaced_by_specific_caveat(claim):
    safe, warnings = guarded_booking_text(claim)
    assert not safe and warnings


@pytest.mark.parametrize("qualification", [
    "Chưa đủ căn cứ coi là trùng thừa.",
    "Chưa xác nhận mã đơn hợp lệ.",
    "Không được xóa các dòng booking.",
    "Chưa đủ dữ liệu tính chuyến xe.",
    "Delivered không đồng nghĩa SLA.",
    "Trạng thái chưa xác định không đồng nghĩa hàng chưa giao.",
    "Unknown không phải là hàng chưa giao.",
    "Unknown không có nghĩa là chưa hoàn tất.",
])
def test_correct_negated_qualification_remains_visible(qualification):
    safe, warnings = guarded_booking_text(qualification)
    assert safe == qualification and not warnings


def test_default_booking_report_is_short_grounded_and_keeps_source_dates():
    ctx = context()
    original = deepcopy(ctx["metrics"])
    text = conversation(answer(), ctx, "CHƯA QUA AUDITOR", report=True)
    assert text.startswith("Booking tháng 10/2026: 3 dòng ghi nhận 2 mã GHN khác nhau.")
    assert "1 dòng có mã lặp lại" in text and "1 mã có nhiều trạng thái" in text
    assert "không được coi là hàng chưa giao" in text and "chưa phải số đơn đạt SLA" in text
    assert "12:55 25/10/2026" in text and "10/10/2026" in text
    assert "bản chụp" in text and "chưa đối soát thực tế" in text
    assert "chi phí, OT, số kiện, khối lượng, tải xe, chuyến/điểm giao hay SLA" in text
    assert "Không cộng hoặc điền xuống các tổng gộp" in text
    assert len(text.encode("utf-16-le"))//2 < 3500
    assert not re.search(r"booking\.[a-z_]", text)
    assert all(token not in text for token in ("SHA256", "OWNER SOURCE", "UNVERIFIED", "Operations", "Model:", "e"*64, "SYNTHETIC_A"))
    assert ctx["metrics"] == original


def test_all_late_objections_and_limits_survive_action_limit_and_chunking():
    ctx = context()
    output = answer(proposals=[f"Đối chiếu nguồn tổng hợp mục {i}." for i in range(8)],
        observations=[{"text": "Chưa xác minh chứng từ quan trọng cuối bản.", "evidence_ids": ["booking.rows"]}],
        hypotheses=["Thiếu định nghĩa ngày thực tế."], disagreements=["Phản biện cuối cần đối soát. "+"🔎"*1900])
    audit = {"task_id": "BOOKING-TEST-v1", "review_complete": False, "source_issues": ["Thiếu mẫu đối chiếu nguồn."],
        "audit_decisions": [{"task_id": "BOOKING-TEST-v1", "verdict": "revise", "reason": "Cần xác minh định nghĩa trạng thái.", "evidence_ids": []}],
        "audit_answer": answer(summary="Bản cần sửa trước khi chốt.", observations=[{"text": "Lưu ý của kiểm tra ở cuối.", "evidence_ids": []}],
            hypotheses=["Chưa kiểm chứng nguyên nhân."], proposals=[], requests=[], disagreements=["Phản biện độc lập cuối."])}
    text = conversation(output, ctx, "KIỂM TOÁN CÒN MỞ", audit)
    for value in ("Chưa xác minh chứng từ quan trọng cuối bản.", "Thiếu định nghĩa ngày thực tế.",
                  "Phản biện cuối cần đối soát.", "Cần sửa: Cần xác minh định nghĩa trạng thái.",
                  "Thiếu mẫu đối chiếu nguồn.", "Lưu ý của kiểm tra ở cuối.", "Chưa kiểm chứng nguyên nhân.", "Phản biện độc lập cuối."):
        assert value in text
    parts = list(chunks_for_telegram(text, continuation=True))
    assert len(parts) > 1 and all(p.startswith("Phần bổ sung (tiếp):\n") for p in parts[1:])
    restored = parts[0]+"".join(p.removeprefix("Phần bổ sung (tiếp):\n") for p in parts[1:])
    assert restored == text and all(len(p.encode("utf-16-le"))//2 <= 3500 for p in parts)
    assert "BOOKING-TEST-v1" not in text and "nhận định chưa được chấp nhận" in text


def test_owner_details_is_local_isolated_and_cleared_with_session(tmp_path):
    calls = []
    def fake(config, payload, **kwargs):
        calls.append(payload)
        return answer(), "synthetic-model"
    profiles = {n: ModelConfig("Groq", FREE_MODELS[0], "synthetic-key", instructions="PRIVATE_PROMPT_SENTINEL",
        free_pilot=True, free_account_confirmed=True) for n in AGENT_NAMES}
    agent = TelegramAgent(profiles, context(), transport=fake)
    state = BusinessState(tmp_path)
    try:
        responder = AssistantResponder(state, {"owner_id": 123, "models": {}, "bot_token": "synthetic-not-used"})
        responder._agent = agent
        ask = Incoming(GROUP_ID, 123, 1, 9, "operations", "PRIVATE_QUESTION_SENTINEL")
        text = agent.answer(ask)
        assert "synthetic-model" not in text and len(calls) == 1
        assert "Chỉ chủ sở hữu" in responder.answer(Incoming(GROUP_ID, 456, 2, 9, "details", ""))
        details = responder.answer(Incoming(GROUP_ID, 123, 3, 9, "details", ""))
        assert "Model: synthetic-model" in details and "booking.rows: 3" in details
        assert len(calls) == 1 and all(s not in details for s in ("PRIVATE_PROMPT_SENTINEL", "PRIVATE_QUESTION_SENTINEL", "synthetic-key", "SYNTHETIC_A"))
        record = json.dumps(agent.last_presentation, ensure_ascii=False)
        assert "PRIVATE_PROMPT_SENTINEL" not in record and "PRIVATE_QUESTION_SENTINEL" not in record
        assert "chưa giữ chi tiết" in agent.details(Incoming(GROUP_ID, 123, 4, 10, "details", "")).lower()
        agent.answer(Incoming(GROUP_ID, 123, 5, 9, "reset", ""))
        assert not agent._records and agent.last_presentation is None
        assert "chưa giữ chi tiết" in responder.answer(Incoming(GROUP_ID, 123, 6, 9, "details", "")).lower()
    finally:
        state.close()


def test_no_source_chat_still_asks_concrete_inputs_without_technical_introduction():
    text = conversation(answer(summary="Để tính chi phí, anh cần gửi số liệu cùng kỳ.",
        requests=[{"to": "Accounting", "question": "Anh có sổ chi phí theo ngày và kho không?", "evidence_ids": []}]),
        {"metrics": {}, "label": "TRAO ĐỔI CHUNG"}, "CHƯA QUA AUDITOR")
    assert text.startswith("Để tính chi phí") and "Anh có sổ chi phí" in text
    assert "chưa có số liệu doanh nghiệp" in text and "chưa được kiểm tra chéo" in text
    assert "Accounting" not in text and "CHƯA QUA AUDITOR" not in text


def test_no_source_invented_quantities_and_empty_requests_get_one_local_question():
    raw = answer(summary="Đối chiếu chi phí với sản lượng cùng kỳ. lấy 5% dữ liệu đầu tiên.",
        proposals=["Bố trí 2 xe.", "Giảm 10% giờ công.", "Tiết kiệm 20 triệu đồng.",
                   "Thu thập ba nhóm dữ liệu: chi phí, nhân sự và nhật ký xe."], requests=[])
    original, calls = deepcopy(raw), []
    def fake(config, payload, **kwargs):
        calls.append(payload)
        return deepcopy(raw), "synthetic-model"
    profiles = {n: ModelConfig("Groq", FREE_MODELS[0], "synthetic-key", free_pilot=True,
        free_account_confirmed=True) for n in AGENT_NAMES}
    agent = TelegramAgent(profiles, transport=fake)
    request = Incoming(GROUP_ID, 123, 1, None, "cost", "Bắt đầu đánh giá chi phí, nhân sự và xe thế nào? Hỏi một câu quan trọng nhất.")
    text = agent.answer(request)
    assert len(calls) == 1 and calls[0]["metrics"] == {}
    assert "Đối chiếu chi phí với sản lượng cùng kỳ." in text
    assert "Thu thập ba nhóm dữ liệu" in text
    assert "sổ chi phí cùng sản lượng" in text and text.count("?") == 1
    assert "Nhận định sơ bộ, chưa được kiểm tra chéo." in text
    for quantity in ("5%", "2 xe", "10%", "20 triệu"):
        assert quantity not in text and quantity not in agent.details(request)
    # The question is local scaffolding, not a fabricated model request or new call.
    assert agent.last_presentation["answer"] == original and raw == original
    assert agent.last_presentation["answer"]["requests"] == []


@pytest.mark.parametrize("role,expected", [
    ("Accounting", "chứng từ"), ("Operations", "nhật ký vận hành"),
    ("Workforce", "giờ công"), ("Fleet", "nhật ký chuyến xe"),
    ("KPI", "chỉ số nào"), ("Auditor", "nhận định nào"), ("Coordinator", "ưu tiên vấn đề nào"),
])
def test_no_source_fallback_question_matches_role(role, expected):
    text = conversation(answer(summary="Cần thống nhất phạm vi trước.", requests=[]),
        {"metrics": {}, "label": "TRAO ĐỔI CHUNG"}, "CHƯA QUA AUDITOR", role=role)
    assert expected in text and text.count("?") == 1


def test_existing_model_question_is_not_duplicated_and_grounded_numbers_are_unchanged():
    raw = answer(summary="Có 3 dòng trong phạm vi.", proposals=["Xem 5% dữ liệu."],
        requests=[{"to": "Cost", "question": "Anh cần phân tích kho nào?", "evidence_ids": []}])
    text = conversation(raw, {"metrics": {}, "label": "TRAO ĐỔI CHUNG"}, "CHƯA QUA AUDITOR", role="Cost")
    assert "Anh cần phân tích kho nào?" in text and text.count("?") == 1
    assert "3 dòng" not in text and "5%" not in text
    ctx = {"metrics": {"synthetic.count": {"value": 3}}, "label": "DEMO"}
    assert "Có 3 dòng trong phạm vi." in conversation(raw, ctx, "CHƯA QUA AUDITOR", role="Cost")


@pytest.mark.parametrize("summary", [
    "Câu trả lời đã được đưa ra theo đúng quy trình. Câu trả lời ngắn gọn nhưng mở đã được đưa ra. Chưa cần điều chỉnh kỹ thuật hay tìm kiếm thêm.",
    "Phản hồi phù hợp quy trình trao đổi. Em đã trả lời theo yêu cầu.",
])
def test_no_source_meta_only_summary_gets_practical_lead_and_question(summary):
    raw = answer(summary=summary, proposals=[], requests=[])
    original = deepcopy(raw)
    text = conversation(raw, {"metrics": {}, "label": "TRAO ĐỔI CHUNG"}, "CHƯA QUA AUDITOR", role="Cost")
    assert text.startswith("Trước hết cần thống nhất phạm vi và nguồn số liệu để đánh giá.")
    assert "sổ chi phí cùng sản lượng" in text and text.count("?") == 1
    assert "Nhận định sơ bộ, chưa được kiểm tra chéo." in text
    assert all(meta not in text.lower() for meta in ("câu trả lời", "quy trình trao đổi", "điều chỉnh kỹ thuật", "em đã trả lời"))
    assert raw == original


def test_no_source_meta_filter_preserves_useful_business_guidance_and_existing_question():
    text = conversation(answer(summary="Câu trả lời đã được đưa ra. Đối chiếu chi phí với sản lượng cùng kỳ.",
        proposals=["Phản hồi ngắn gọn đã được đưa ra.", "Tách giờ công theo kho và ca."],
        requests=[{"to": "Cost", "question": "Anh có số liệu của kỳ nào?", "evidence_ids": []}]),
        {"metrics": {}, "label": "TRAO ĐỔI CHUNG"}, "CHƯA QUA AUDITOR", role="Cost")
    assert text.startswith("Đối chiếu chi phí với sản lượng cùng kỳ.")
    assert "Tách giờ công theo kho và ca." in text and "Anh có số liệu của kỳ nào?" in text
    assert text.count("?") == 1 and "đã được đưa ra" not in text
