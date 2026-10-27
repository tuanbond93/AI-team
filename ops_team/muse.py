"""Small manual handoff of business results, built in RAM. No Muse API calls."""
from io import BytesIO
import zipfile
import pandas as pd
from .reporting import build_report, csv_bytes, source_catalog


def muse_bundle(run, private_fragments=()):
    rows = []
    catalog = source_catalog(run)
    sources = [{"evidence_id": identifier, "source": source} for source, identifier in catalog.items()]
    for prefix, metrics in (("current", run.metrics), ("previous", run.previous_metrics)):
        for metric in metrics.values():
            rows.append({"period_side": prefix, "period": metric.period, "metric_id": metric.id,
                "label": metric.label, "value": metric.value, "unit": metric.unit,
                "formula": metric.formula, "status": metric.status, "scope": metric.scope,
                "source_count": len(metric.sources), "evidence_ids": "; ".join(catalog[s] for s in metric.sources)})
    assumptions = [{k: v for k, v in scenario.items() if k != "sources"} for scenario in run.scenarios]
    # No tasks/questions, session instructions, skills, config, credentials,
    # transport payload, or internal agent transcript in the handoff.
    report = build_report(run, compact=True)
    report += "\n\nPhản hồi từ Muse là phân tích bên ngoài, CHƯA được Auditor của app xác thực. Không tự đóng kiểm toán.\n"
    report += "\nGói bàn giao chứa kết quả và bằng chứng của phạm vi hiện tại; không xác nhận dữ liệu đủ kỳ hoặc nhân quả.\n"
    parts = {"KET_QUA_NGHIEP_VU.md": report.encode("utf-8"),
        "CHI_SO.csv": csv_bytes(pd.DataFrame(rows)), "BANG_CHUNG.csv": csv_bytes(pd.DataFrame(sources)),
        "GIA_DINH.csv": csv_bytes(pd.DataFrame(assumptions)),
        "PHAM_VI_NGAY.csv": csv_bytes(pd.DataFrame(run.data_coverage))}
    for name, content in parts.items():
        text = content.decode("utf-8-sig")
        if run.question:
            text = text.replace(run.question, "[nội dung yêu cầu của phiên đã ẩn]")
        if any(value in text for value in private_fragments if value and len(value) >= 8):
            raise ValueError("Gói Muse trùng nội dung riêng của phiên; đã chặn xuất. Sửa giả định/ghi chú trước khi thử lại.")
        parts[name] = text.encode("utf-8-sig" if name.endswith(".csv") else "utf-8")
    if sum(map(len, parts.values())) > 256000:
        raise ValueError("Gói Muse vượt 256 KB; thu hẹp kỳ/kho. Không cắt ngầm bằng chứng.")
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def demo_muse_bundle():
    from .demo import sample_workbook_bytes
    from .ingestion import read_file, validate
    from .agents import run_team
    validation = validate(read_file(sample_workbook_bytes(), "DU_LIEU_DEMO.xlsx"))
    run = run_team(validation, "2026-09", "2026-08", ["Duc Hoa", "HCM"],
        "Đánh giá các bất thường trên dữ liệu mô phỏng", data_label="DỮ LIỆU DEMO — AGENT MÔ PHỎNG — 0 API")
    return muse_bundle(run)


def runtime_handoff_text():
    from .schema import DOMAINS
    roles = "\n".join(f"- {name}: {specialty}" for name, specialty in DOMAINS.items())
    return ("Phân tích gói kết quả nghiệp vụ tôi đính kèm. Giữ nhãn DEMO nếu nguồn ghi DEMO; "
        "không coi số liệu giả là kết quả thực tế. Dùng các góc nhìn:\n" + roles +
        "\nDẫn metric_id và file/sheet/dòng cho nhận định. Tách số đã tính, giả thuyết, giả định và phần thiếu. "
        "Trình bày: bất thường; kiểm tra chéo; bất đồng; đề xuất chờ duyệt; việc cần xác minh. "
        "Không cộng trùng chi phí nhân sự/xe. Phản hồi này CHƯA được Auditor trong app xác thực, không được tự đóng audit hoặc thực thi vận hành.")
