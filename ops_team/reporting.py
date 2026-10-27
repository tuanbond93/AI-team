"""Export findings and evidence; never export session prompts or configuration."""
from datetime import datetime
from zoneinfo import ZoneInfo
from io import BytesIO
import zipfile
import pandas as pd


def fmt(value, unit=""):
    if value is None:
        return "Chưa đủ dữ liệu"
    digits = 2 if unit in ("%", "đpt", "kiện/giờ", "VND/kiện", "giờ") else 0
    number = f"{value:,.{digits}f}".translate(str.maketrans({",": ".", ".": ","}))
    return f"{number} {unit}".strip()


def safe_text(value):
    # Prevent uploaded strings or model text from becoming active Markdown/HTML.
    return str(value).replace("<", "&lt;").replace(">", "&gt;").replace("[", "\\[").replace("]", "\\]").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def source_catalog(run):
    all_sources = sorted({s for m in [*run.metrics.values(), *run.previous_metrics.values()] for s in m.sources}
                         | {s for r in run.targets for s in r["sources"]}
                         | {s for r in run.reconciliation for s in r["sources"]}
                         | {i["source"] for i in run.issues if i["source"]}
                         | {s for scenario in run.scenarios for s in scenario["sources"]})
    return {s: f"E{i+1:03}" for i, s in enumerate(all_sources)}


def build_report(run, compact=False):
    evidence_numbers = source_catalog(run)

    def refs(sources):
        values = [evidence_numbers[s] for s in sources if s in evidence_numbers]
        return (", ".join(values[:8]) + (f" … ({len(values)} nguồn; gói bằng chứng có đủ)" if len(values)>8 else "")) or "Chưa có nguồn"

    m = run.metrics
    b = run.previous_metrics
    lines = ["# Báo cáo quản trị — AI Operations Team", "",
        f"**{safe_text(run.data_label)}** · Agent: **{'MÔ PHỎNG' if run.mode == 'Demo' else 'LIVE — API thật / có thể có lỗi'}**",
        f"Kỳ: {run.previous or 'không có kỳ so sánh'} → {run.current} · Phạm vi: {safe_text(', '.join(run.warehouses))}",
        f"Phạm vi chi phí: {safe_text(run.metrics['cost'].scope)}",
        f"Trạng thái: {run.status}. Lượt gọi model: {run.calls}.",
        f"Ngày xuất (Việt Nam): {datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')).isoformat(timespec='seconds')}", "",
        "## 1. KPI và chi phí bất thường — số liệu tính bằng code", "",
        "| Chỉ số | Kỳ trước | Kỳ hiện tại | Nguồn hiện tại |", "|---|---:|---:|---|"]
    observed = [f"{c['table']} {c['period']}: {c['date_min']}–{c['date_max']} ({c['observed_days']} ngày)" for c in run.data_coverage if c["date_min"]]
    index = lines.index("## 1. KPI và chi phí bất thường — số liệu tính bằng code")
    lines[index:index] = ["Phạm vi ngày quan sát: " + safe_text("; ".join(observed)) + ". Không xác nhận dữ liệu đủ cả tháng.", ""]
    for key in ("cost", "budget_delta", "volume", "cost_per_unit", "sla", "ot_rate", "empty_rate", "load_weight", "backlog"):
        lines.append(f"| {m[key].label} | {fmt(b[key].value, b[key].unit)} | {fmt(m[key].value, m[key].unit)} | {refs(m[key].sources)} |")
    lines += ["", "Cảnh báo theo mục tiêu từng kho (không bình quân mục tiêu khác nhau):"]
    if not run.targets:
        lines.append("- Chưa có mục tiêu; chờ xác nhận, không tự áp ngưỡng.")
    for target in run.targets:
        status = "Chưa đủ dữ liệu" if target["breach"] is None else "KHÔNG ĐẠT" if target["breach"] else "Đạt"
        lines.append(f"- {safe_text(target['warehouse'])} / {target['metric']}: {fmt(target['value'], '%')} so với {target['direction']} {target['target']}%: {status}. {safe_text(target['assumption'])}. [{refs(target['sources'])}]")
    lines += ["", "## 2. Bằng chứng và nguyên nhân đã xác minh", "",
        "Sự kiện được kiểm tra: công thức, tổng hợp theo phạm vi và liên kết dòng nguồn. Mức đủ dữ liệu là đủ theo các bảng đã khai báo; chưa xác nhận tính đầy đủ so với hệ thống gốc."]
    for result in run.reconciliation:
        lines.append(f"- {safe_text(result['warehouse'])} / {result['category']}: {result['status']}; chênh lệch {fmt(result['difference'], 'VND')}. [{refs(result['sources'])}]")
    lines += ["", "Chưa xác minh được nguyên nhân nhân quả từ bảng tổng hợp. Khớp số không đồng nghĩa chứng từ hợp lệ.",
        "Các giả thuyết dưới đây là diễn giải của agent; cần xác minh thêm:"]
    for agent in ("Cost", "Operations", "Workforce", "Fleet", "Accounting"):
        latest = next((e for e in reversed(run.exchanges) if e.receiver == agent and e.task_id in run.accepted_task_ids), None)
        if latest:
            for h in latest.answer.get("hypotheses", []):
                lines.append(f"- {agent}: {safe_text(h)}")
    final = next((e for e in reversed(run.exchanges) if e.receiver == "Coordinator" and e.answer), None)
    if final:
        lines += ["", "Tổng hợp của Coordinator (diễn giải chưa được xác nhận):", safe_text(final.answer["summary"])]
        for item in final.answer["observations"]:
            source_list = [s for key in item["evidence_ids"] for s in
                (run.previous_metrics[key[9:]].sources if key.startswith("previous.") else run.metrics[key].sources)]
            lines.append(f"- {safe_text(item['text'])}. [{refs(source_list)}]")
    lines += ["", "## 3. Dữ liệu thiếu, bất đồng và kiểm toán", "",
        f"Kiểm tra nguồn: {'ĐẠT CẤU TRÚC' if run.audit['sources_passed'] else 'CHƯA ĐẠT'}. "
        f"Auditor xét hết phiên bản: {'CÓ' if run.audit['review_complete'] else 'CHƯA'}. "
        f"Nội dung không còn vướng mắc ghi nhận: {'CÓ' if run.audit['passed'] else 'CHƯA'}. {run.audit['limitations']}",
        f"Số nhiệm vụ bị trả lại: {len(run.audit['returned'])}. Số vấn đề chất lượng dữ liệu: {len(run.issues)}.",
        "- Cần chứng từ gốc, timeline nhận/xuất, lịch ca, lịch xe, năng lực theo giờ và xác nhận mục tiêu KPI."]
    for item in run.disagreements:
        lines.append("- " + safe_text(item))
    for item in run.issues:
        lines.append(f"- {item['table']} / {item['code']}: {safe_text(item['detail'])}. Nguồn: {safe_text(item['source'])}")
    for e in run.exchanges:
        if e.error:
            lines.append(f"- {e.task_id} / {e.receiver}: {safe_text(e.error)}")
    lines += ["", "## 4. Phương án, lợi ích và SLA", "",
        "| Phương án đề xuất | Lợi ích | Ảnh hưởng SLA / điều kiện |", "|---|---|---|",
        "| Giữ phương án hiện tại và bổ sung đo lường | Có đường cơ sở trước thử nghiệm | Theo dõi SLA từng kho; chưa cam kết không suy giảm |",
        "| Thử cân ca trong cùng công đoạn / loại hàng | Có thể giảm OT; chưa đủ dữ liệu định lượng tiết kiệm | Chỉ sau duyệt; cần lịch hàng và ngưỡng SLA được xác nhận |",
        "| Thử ghép tải trên tuyến đủ điều kiện | Có thể giảm km rỗng; chưa đủ dữ liệu định lượng | Cần tải theo giờ, thời gian giao và ràng buộc tuyến |", "",
        "Không cộng lợi ích nhân sự/xe lần nữa vào tiết kiệm tổng. Không cộng hai phương án dùng cùng phần chi phí khi chưa tách phần giao nhau.",
        "Các đề xuất bổ sung của agent (chưa được kiểm chứng):"]
    for agent in ("Cost", "Operations", "Workforce", "Fleet", "Coordinator"):
        latest = next((e for e in reversed(run.exchanges) if e.receiver == agent and e.answer and
                       (agent == "Coordinator" or e.task_id in run.accepted_task_ids)), None)
        if latest:
            for p in latest.answer.get("proposals", []):
                lines.append(f"- {agent}: {safe_text(p)}")
    bridge = run.comparison.get("bridge", {})
    if bridge.get("ready"):
        lines += ["", "Phân rã thay đổi chi phí/kiện (đồng nhất phạm vi, không khẳng định nhân quả):",
            f"- Ảnh hưởng sản lượng: {fmt(bridge['volume_effect'], 'VND/kiện')}; ảnh hưởng chi phí: {fmt(bridge['cost_effect'], 'VND/kiện')}; tổng: {fmt(bridge['total_change'], 'VND/kiện')}.",
            f"- {bridge['formula']}. [{refs(bridge['sources'])}]"]
    elif run.comparison.get("limitation"):
        lines += ["", safe_text(run.comparison["limitation"])]
    if run.scenarios:
        lines += ["", "What-if theo giả định người dùng — hai phương án độc lập, không cộng lợi ích:"]
        for scenario in run.scenarios:
            lines += [f"- **{scenario['label']}**: cơ sở {safe_text(scenario['assumption'])}. "
                f"Giảm {fmt(scenario['savings_pct'], '%')}, triển khai {fmt(scenario['implementation_cost_vnd'], 'VND')}, "
                f"sản lượng thay đổi {fmt(scenario['volume_change_pct'], '%')}. "
                f"Tiết kiệm ròng giả định {fmt(scenario['net_savings_vnd'], 'VND')}; chi phí/kiện dự kiến {fmt(scenario['projected_cost_per_unit'], 'VND/kiện')}. "
                f"SLA: {fmt(scenario['projected_sla'], '%')} — {scenario['sla_status']}. [{refs(scenario['sources'])}]",
                f"  Điều kiện: {scenario['conditions']}; chủ trì đề xuất: {scenario['owner']}; ưu tiên: {scenario['priority']}."]
            decision = run.reviews.get(scenario["id"], {})
            lines.append(f"  Ý kiến quản lý trong phiên: {safe_text(decision.get('status', 'Chờ xem xét'))}; "
                         f"{safe_text(decision.get('note', ''))}; {safe_text(decision.get('reviewed_at', 'chưa ghi nhận'))}.")
    lines += ["", "## 5. Ưu tiên, đầu mối và thời hạn đề xuất", "",
        "| Ưu tiên | Công việc chờ duyệt | Đầu mối đề xuất | Thời hạn tính từ khi duyệt |", "|---|---|---|---|",
        "| P1 | Đối soát dòng cách ly, chứng từ và lệch sổ/chi tiết | Kế toán + kiểm soát nội bộ | T+2 ngày làm việc |",
        "| P1 | Xác minh SLA và điểm nghẽn bằng timeline | Quản lý vận hành kho | T+2 ngày làm việc |",
        "| P2 | Lập thử nghiệm ca làm và khai thác xe có giám sát | Nhân sự vận hành + quản lý xe | T+5 ngày làm việc |",
        "", "## 6. Quyết định cần quản lý duyệt", "",
        "- Xác nhận dữ liệu đủ kỳ, đơn vị, phạm vi và mục tiêu KPI.",
        "- Duyệt đầu mối thu thập bằng chứng và phạm vi thử nghiệm; chưa duyệt cắt giảm/điều phối trực tiếp.",
        "- Xác nhận ngưỡng dừng thử nghiệm, tác động SLA cho phép và cách tính tiết kiệm không trùng.",
        "Mọi hành động thực tế vẫn CHỜ DUYỆT theo quy trình doanh nghiệp. Ý kiến về phạm vi thử nghiệm trong phiên không phải lệnh thực thi hay chữ ký phê duyệt. Ứng dụng không chi tiền, điều xe, đổi nhân sự hoặc gửi thông báo.",
        "", "## Phụ lục A — công thức và tình trạng dữ liệu", "",
        "| Kỳ | Chỉ số | Giá trị | Công thức | Tình trạng | Nguồn |", "|---|---|---:|---|---|---|"]
    for metric in [*b.values(), *m.values()]:
        lines.append(f"| {metric.period or 'Không có kỳ trước'} | {metric.label} | {fmt(metric.value, metric.unit)} | {metric.formula} | {metric.status} | {refs(metric.sources)} |")
    lines += ["", "## Phụ lục B — thực thi agent", "",
        "| Nhiệm vụ | Gửi → nhận | Vòng | Trạng thái | Provider / model thực tế |", "|---|---|---:|---|---|"]
    for e in run.exchanges:
        lines.append(f"| {e.task_id} (v{e.version}; gốc {e.logical_id}; thay {e.replaces or '—'}) | {e.sender} → {e.receiver} | {e.round} | {e.status}; nguồn: {e.source_status}; nội dung: {e.review_status} | {safe_text(e.provider)} / {safe_text(e.model) or 'Chưa chạy thành công'} |")
    lines += ["", "Lịch sử quyết định Auditor (ràng buộc đúng mã phiên bản):"]
    lines.extend(f"- {record['review_id']} → {record['task_id']}: {record['verdict']} — {safe_text(record['reason'])}" for record in run.audit["history"])
    lines += ["", "## Phụ lục C — danh mục nguồn", ""]
    lines.extend(f"- {number}: {safe_text(source)}" for source, number in evidence_numbers.items())
    if compact:
        lines = lines[:lines.index("## Phụ lục A — công thức và tình trạng dữ liệu")]
        lines += ["Công thức, lịch sử phiên bản và danh mục dòng nguồn đầy đủ nằm trong bản tải xuống; tab Tổng quan → Bằng chứng cho phép xem theo chỉ số."]
    return "\n".join(lines)


def csv_bytes(frame):
    # Excel formula injection protection for text supplied by files/models/users.
    def safe(value):
        return "'"+value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value
    return frame.map(safe).to_csv(index=False).encode("utf-8-sig")


def report_bundle(run):
    catalog = source_catalog(run)
    rows = []
    for metric in [*run.previous_metrics.values(), *run.metrics.values()]:
        rows.extend({"metric_id": metric.id, "period": metric.period or "Không có kỳ trước", "scope": metric.scope,
                     "evidence_id": catalog[s], "source": s} for s in metric.sources)
    for target in run.targets:
        rows.extend({"metric_id": "target."+target["metric"], "period": run.current, "scope": target["warehouse"],
                     "evidence_id": catalog[s], "source": s} for s in target["sources"])
    for scenario in run.scenarios:
        rows.extend({"metric_id": scenario["id"], "period": run.current, "scope": ", ".join(run.warehouses),
                     "evidence_id": catalog[s], "source": s} for s in scenario["sources"])
    decisions = [{"scenario_id": k, **value} for k, value in run.reviews.items()]
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("BAO_CAO.md", build_report(run).encode("utf-8"))
        archive.writestr("BANG_CHUNG.csv", csv_bytes(pd.DataFrame(rows)))
        archive.writestr("Y_KIEN_QUAN_LY.csv", csv_bytes(pd.DataFrame(decisions)))
        archive.writestr("LICH_SU_KIEM_TOAN.csv", csv_bytes(pd.DataFrame(run.audit["history"])))
        archive.writestr("PHAM_VI_NGAY.csv", csv_bytes(pd.DataFrame(run.data_coverage)))
    return buffer.getvalue()
