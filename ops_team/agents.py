"""Bounded orchestration, evidence audit, and explicit simulated specialists."""
from dataclasses import dataclass, field
from .schema import DOMAINS


@dataclass
class Exchange:
    task_id: str
    parent_id: str
    sender: str
    receiver: str
    question: str
    scope: str
    expected: str
    evidence_ids: list
    status: str = "Chờ"
    round: int = 0
    answer: dict = field(default_factory=dict)
    model: str = ""
    provider: str = ""
    error: str = ""
    logical_id: str = ""
    version: int = 1
    replaces: str = ""
    source_status: str = "Chưa kiểm tra"
    review_status: str = "Chờ Auditor"
    usage: dict = field(default_factory=dict)
    phase: str = "analysis"


@dataclass
class RunResult:
    mode: str
    data_label: str
    current: str
    previous: str | None
    warehouses: list
    metrics: dict
    previous_metrics: dict
    exchanges: list
    reconciliation: list
    targets: list
    workforce_groups: list
    issues: list
    disagreements: list
    audit: dict
    calls: int
    duration: float
    status: str
    question: str
    accepted_task_ids: list = field(default_factory=list)
    latest_task_ids: list = field(default_factory=list)
    comparison: dict = field(default_factory=dict)
    scenarios: list = field(default_factory=list)
    reviews: dict = field(default_factory=dict)
    data_coverage: list = field(default_factory=list)


ROLE_METRICS = {
    "Coordinator": ["cost", "budget_delta", "sla"],
    "Accounting": ["cost", "budget"], "Cost": ["cost", "budget_delta", "cost_per_unit"],
    "Operations": ["sla", "backlog", "volume"], "Workforce": ["ot_rate", "productivity", "defect_rate"],
    "Fleet": ["load_weight", "load_volume", "empty_rate", "wait_hours", "cost_per_trip"],
    "KPI": ["sla", "ot_rate", "empty_rate"], "Auditor": ["cost", "sla"],
}


def simulated(agent, task, metrics, round_number=0):
    ids = [k for k in ROLE_METRICS[agent] if metrics[k]["value"] is not None]
    observations = [{"text": f"Mô phỏng: xem chỉ số {k} đã tính bằng code.", "evidence_ids": [k]} for k in ids]
    out = dict(summary="MÔ PHỎNG — " + DOMAINS[agent], observations=observations, hypotheses=[], proposals=[],
        requests=[], disagreements=[], selected_agents=[])
    if agent == "Coordinator":
        q = task.casefold()
        selected = ["Accounting", "Operations", "KPI"]
        for name, keywords in [("Cost", ["chi phí", "ngân sách", "cost"]), ("Workforce", ["nhân sự", "ot", "giờ công"]),
                               ("Fleet", ["xe", "fleet", "tuyến"])]:
            if any(word in q for word in keywords):
                selected.append(name)
        out["selected_agents"] = selected
        out["proposals"] = ["Ưu tiên xác minh điểm nghẽn trước khi duyệt thử nghiệm; chưa cam kết số tiền tiết kiệm."]
    if agent == "Cost":
        out["hypotheses"] = ["OT và km rỗng có thể góp phần tăng chi phí; chưa có bằng chứng nhân quả."]
        out["proposals"] = ["Lập thử nghiệm cải thiện; phần tiết kiệm nhân sự và xe nằm trong sổ chi phí, không cộng lợi ích trùng."]
        out["requests"] = [
            {"to": "Workforce", "question": "OT thay đổi ra sao trong cùng công đoạn, loại hàng và ca? Đối chiếu tỷ lệ lỗi.", "evidence_ids": ["ot_rate", "defect_rate"]},
            {"to": "Fleet", "question": "Giải thích chi phí chuyến, tải và km rỗng; chưa quy kết nguyên nhân khi thiếu lịch xe.", "evidence_ids": ["empty_rate", "load_weight", "cost_per_trip"]},
            {"to": "Operations", "question": "Phương án giảm OT/gộp chuyến có rủi ro SLA nào? Cần điều kiện thử nghiệm gì?", "evidence_ids": ["sla", "backlog"]},
        ] if round_number == 0 else []
        # Deliberately demonstrate Auditor returning an unsupported statement.
        if round_number == 0:
            out["observations"].append({"text": "Giả thuyết OT cần bổ sung bằng chứng.", "evidence_ids": []})
    if agent == "Operations":
        out["hypotheses"] = ["Thiếu timeline nhận/xuất, cutoff và công suất theo giờ để xác minh nguyên nhân tồn/SLA."]
        out["proposals"] = ["Đo SLA trong thử nghiệm có giám sát; chưa thể lượng hóa ảnh hưởng từ dữ liệu tổng hợp."]
        out["disagreements"] = ["Chưa đồng thuận giảm OT hoặc gộp chuyến khi chưa có lịch hàng và công suất theo giờ."]
    if agent == "Accounting":
        out["hypotheses"] = ["Số tổng khớp chưa chứng minh chứng từ hợp lệ; cần bản chứng từ và bảng kê chi tiết."]
    if agent == "Workforce":
        out["proposals"] = ["So sánh stage × goods_type × shift cùng tỷ lệ lỗi; chưa dùng năng suất gộp để xếp hạng người lao động."]
    if agent == "Fleet":
        out["hypotheses"] = ["Cần lịch xe, thời gian bốc dỡ và giới hạn tuyến để xác minh nguyên nhân km rỗng/giờ chờ."]
    if agent == "KPI":
        out["proposals"] = ["Mục tiêu KPI theo bảng nhập; nhãn DEMO phải được quản lý xác nhận trước áp dụng."]
    if agent == "Auditor":
        out["hypotheses"] = ["Chỉ xác nhận phép tính và liên kết nguồn; nguyên nhân, SLA tương lai và tiết kiệm chưa được kiểm chứng."]
    return out


def audit_answer(answer, metrics):
    problems = []
    for item in answer.get("observations", []):
        ids = item.get("evidence_ids", [])
        if not ids:
            problems.append("Nhận định thiếu bằng chứng: " + item["text"])
        elif any(k not in metrics or metrics[k]["value"] is None or not metrics[k]["sources"] for k in ids):
            problems.append("Bằng chứng thiếu dữ liệu hoặc không tồn tại: " + item["text"])
    return problems


def run_team(*args, **kwargs):
    from .orchestrator import run_team as implementation
    return implementation(*args, **kwargs)
