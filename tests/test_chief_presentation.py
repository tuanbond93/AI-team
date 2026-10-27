"""Present business audit caveats without treating model meta as a conclusion."""
from copy import deepcopy
from ops_team.chief.agent import review_text
from ops_team.telegram_agent import TelegramAgent
from ops_team.telegram_bridge import chunks_for_telegram


def answer(**changes):
    value = dict(summary="", observations=[], proposals=[], hypotheses=[], requests=[], disagreements=[], selected_agents=[], audit_decisions=[])
    value.update(changes)
    return value


def audit():
    return dict(task_id="CHIEF-0123456789abcdef-v1", review_complete=False, source_issues=[], finance_available=True,
        answer=answer(), audit_answer=answer(), audit_decisions=[dict(task_id="OTHER", verdict="unresolved", reason="Cần đối chiếu chứng từ gốc.", evidence_ids=["chief.revenue"])])


def test_large_meta_is_excluded_explicitly_without_hiding_wrong_version_or_real_objections():
    data = audit()
    data["answer"] = answer(summary=("Câu trả lời đã được đưa ra. Cần đối soát vật lý mã nguồn. Cần quyền truy cập sâu. Cần định dạng 2 chữ số. zzxyzzq. "*40)+"Chưa có chứng từ chi phí gốc.",
        proposals=["Cấp quyền truy cập toàn bộ mã nguồn."])
    data["audit_answer"] = answer(disagreements=["Định nghĩa doanh thu chưa khớp phạm vi."],
        requests=[dict(to="Accounting", question="Anh xác nhận chi phí ngoài ba chặng đã đủ chưa?", evidence_ids=[])])
    original = deepcopy(data)
    text = review_text(data)
    assert len(text) < 1800
    assert "còn mở; nhận định chưa được chấp nhận" in text
    assert "cho bản khác, không áp dụng" in text and "Chưa có quyết định đúng phiên bản" in text
    assert "Chưa có chứng từ chi phí gốc" in text and "Định nghĩa doanh thu chưa khớp phạm vi" in text
    assert "chi phí ngoài ba chặng" in text and "Đã loại khỏi kết luận, chờ sửa" in text
    assert all(raw not in text for raw in ("đối soát vật lý mã nguồn", "quyền truy cập sâu", "định dạng 2 chữ số", "zzxyzzq", "Câu trả lời đã được"))
    assert data == original


def test_all_business_objections_survive_optional_limit_and_long_message_split():
    data = audit()
    objections = [f"Chưa đối soát chứng từ chi phí mục {i}: "+"cần xác minh phạm vi nguồn "*12 for i in range(12)]
    data["answer"] = answer(proposals=[*[f"Đối chiếu bảng cước tuyến mục {i}." for i in range(9)], "CRITICAL: GP âm tại đơn trong phạm vi tính."])
    data["audit_answer"] = answer(disagreements=objections,
        requests=[dict(to="Operations", question="Kho nào xác nhận manifest cuối chưa đủ B2C?", evidence_ids=[])])
    data["source_issues"] = ["Bằng chứng nguồn cuối không khớp đơn vị kg."]
    text = review_text(data)
    assert all(value.strip() in text for value in objections)
    assert "manifest cuối chưa đủ B2C" in text and "nguồn cuối không khớp đơn vị kg" in text
    assert "CRITICAL: GP âm tại đơn trong phạm vi tính." in text
    parts = list(chunks_for_telegram(text, continuation=True))
    assert len(parts) > 1
    assert parts[0]+"".join(p.removeprefix("Phần bổ sung (tiếp):\n") for p in parts[1:]) == text
    assert all(len(p.encode("utf-16-le"))//2 <= 3500 for p in parts)


def test_real_source_format_error_and_business_clause_beside_meta_remain_visible():
    data = audit()
    data["answer"] = answer(summary="Cần truy cập sâu mã nguồn, chi phí xe thiếu cước.",
        observations=[dict(text="Định dạng ngày xuất sai làm phạm vi kỳ chưa xác định.", evidence_ids=["chief.gp"])],
        requests=[dict(to="Operations", question="Anh kiểm tra định dạng mã GHN trước khi ghép manifest được không?", evidence_ids=[])])
    text = review_text(data)
    assert "truy cập sâu mã nguồn" not in text and "chi phí xe thiếu cước" in text
    assert "Định dạng ngày xuất sai" in text and "định dạng mã GHN" in text


def test_late_disagreement_promotes_same_optional_summary_instead_of_losing_it():
    data = audit()
    shared = "Doanh thu theo phạm vi khai báo."
    data["answer"] = answer(summary=shared, proposals=[f"Đối chiếu bảng cước tuyến {i}." for i in range(7)])
    data["audit_answer"] = answer(disagreements=[shared])
    text = review_text(data)
    assert "Bất đồng còn mở: "+shared in text
    assert text.count(shared) == 1


def test_meta_only_decision_stays_open_and_full_original_dto_stays_in_ram_details_are_marked_short():
    data = audit()
    data["audit_decisions"][0]["reason"] = "Cần định dạng 2 chữ số và quyền truy cập sâu mã nguồn."
    data["audit_answer"] = answer(summary="Câu trả lời đã được đưa ra.", disagreements=[f"Chưa kiểm chứng chi phí mục {i}." for i in range(9)])
    record = dict(context=dict(label="SYNTHETIC", scope="SYNTHETIC scope", metrics={}), model="fake-model", review="KIỂM TOÁN CÒN MỞ",
        answer=data["answer"], audit=data, chief_result=dict(financial_complete=False, checks=[]))
    original = deepcopy(record)
    main, detail = review_text(data), TelegramAgent.detail_text(record)
    assert "Lý do chưa có cơ sở nghiệp vụ rõ; loại khỏi kết luận, chờ sửa" in main
    assert "Chưa có quyết định đúng phiên bản" in main
    assert data["audit_decisions"][0]["reason"] in detail
    assert "Truy vết hiển thị rút gọn" in detail and "RAM" in detail
    assert data["audit_answer"]["disagreements"][-1] in main
    assert record == original and not data["review_complete"]


def test_presentation_does_not_rewrite_acceptance_even_when_technical_proposal_is_excluded():
    data = audit()
    data["review_complete"] = True
    data["audit_decisions"][0].update(task_id=data["task_id"], verdict="accept")
    data["answer"] = answer(proposals=["Cần định dạng 2 chữ số."])
    original = deepcopy(data)
    text = review_text(data)
    assert "đã xét phần diễn giải, chưa thay đối soát" in text and "Đã loại khỏi kết luận, chờ sửa" in text
    assert data == original


def test_web_report_uses_headings_separate_table_block_and_new_priority_list():
    from ops_team.chief.engine import evaluate
    from ops_team.chief.demo import demo_tables
    from ops_team.chief.report import render, priorities
    result = evaluate(demo_tables())
    original = deepcopy(result)
    text = render(result)
    assert text.startswith("### 1. Báo cáo nhanh\n\n")
    assert text.count("\n### ") == 3
    summary = text.split("### 1. Báo cáo nhanh\n\n", 1)[1].split("\nTrạng thái:", 1)[0]
    assert len(summary.splitlines()) == 3 and all(line.endswith("  ") for line in summary.splitlines())
    assert "\n\n### 2. Bảng số liệu đã tính\n\n| Chỉ tiêu |" in text
    table = text.split("### 2. Bảng số liệu đã tính\n\n", 1)[1].split("\n\n### 3.", 1)[0]
    assert all(line.startswith("|") for line in table.splitlines())
    priority = text.split("### 4. Đúng 3 việc cần chủ sở hữu chốt\n\n", 1)[1]
    assert priority.splitlines() == [f"{i}. {action}" for i, action in enumerate(priorities(result), 1)]
    assert result == original


def test_telegram_report_keeps_plaintext_sections_and_priority_values():
    from ops_team.chief.engine import evaluate
    from ops_team.chief.demo import demo_tables
    from ops_team.chief.report import render, priorities, metric_rows
    result = evaluate(demo_tables())
    text = render(result, telegram=True)
    assert text.startswith("1. Báo cáo nhanh\nSản lượng:")
    assert "###" not in text and "| Chỉ tiêu |" not in text
    for row in metric_rows(result):
        assert f"• {row['Chỉ tiêu']}: {row['Giá trị tính toán']} {row['Đơn vị']} — {row['Đánh giá sơ bộ']}" in text
    assert text.split("4. Đúng 3 việc cần chủ sở hữu chốt\n", 1)[1].splitlines() == [f"{i}. {a}" for i, a in enumerate(priorities(result), 1)]
