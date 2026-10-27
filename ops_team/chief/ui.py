"""Local review surface: preview, explicit source confirmation, optional AI review."""
from pathlib import Path
from copy import deepcopy
import hashlib
import streamlit as st
import pandas as pd
from .demo import demo_tables, csv_bundle
from .engine import evaluate
from .ingestion import FinancialData, read_many
from .report import render, metric_rows


def _review_agent(configs, context):
    from ..telegram_agent import TelegramAgent
    from ..limits import RequestBudget, UsageWindow
    if "pilot_window" not in st.session_state:
        st.session_state.pilot_window = UsageWindow()
    agent = TelegramAgent(configs, context)
    agent._budget = RequestBudget(max_calls=24, max_tokens=60000, window=st.session_state.pilot_window, sleep=agent._wait)
    return agent


def render_chief(configs, ready):
    st.header("Chief Auditor — thẩm định vận hành và tài chính")
    st.caption("Tính P&L theo đơn/chuyến và cước hiệu lực. Dữ liệu thiếu hiện N/A; giả thuyết không phải nguyên nhân đã xác minh. Các đề xuất chờ chủ sở hữu duyệt.")
    left, right = st.columns(2)
    with left:
        st.download_button("Tải header CSV chuẩn (7 bảng)", csv_bundle(), "CHIEF_HEADERS.zip", mime="application/zip", key="chief_headers")
    with right:
        template = Path(__file__).resolve().parents[2]/"outputs"/"chief-auditor"/"SYNTHETIC_CHIEF_INPUT.xlsx"
        if template.exists():
            st.download_button("Tải mẫu Excel kèm dữ liệu giả", template.read_bytes(), template.name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="chief_xlsx")
    if st.button("Nạp ví dụ tài chính tổng hợp — 0 API", key="chief_demo"):
        st.session_state.chief_data = FinancialData(demo_tables(), "SYNTHETIC DEMO / PLAN / UNVERIFIED")
        st.session_state.pop("chief_audit", None)
    uploads = st.file_uploader("Chọn workbook chuẩn hoặc các CSV tên bảng (không sửa file gốc)", type=["xlsx", "csv"],
        accept_multiple_files=True, key="chief_uploads")
    if uploads:
        signature = hashlib.sha256(b"".join(f.name.encode()+f.getvalue() for f in uploads)).hexdigest()
        if signature != st.session_state.get("chief_import_signature"):
            st.session_state.chief_import_signature = signature
            st.session_state.pop("chief_preview", None)
        if st.button("Kiểm tra nguồn tài chính", key="chief_validate"):
            try:
                st.session_state.chief_preview = read_many([(f.name, f.getvalue()) for f in uploads])
            except (ValueError, OSError, KeyError):
                st.session_state.pop("chief_preview", None)
                st.error("Nguồn chưa đúng header/giá trị hoặc có công thức. Xem data dictionary và file mẫu; nguồn đang dùng được giữ.")
        preview = st.session_state.get("chief_preview")
        if preview:
            checked = evaluate(preview.tables)
            st.write(f"{sum(preview.raw_counts.values())} dòng nguồn; {len(checked['quarantined_sources'])} tham chiếu cách ly; trạng thái {checked['status']}.")
            with st.expander("Lỗi nguồn và bước sửa", expanded=bool(checked["quarantined_sources"])):
                st.dataframe(pd.DataFrame([{"Lỗi": i["fact"], "Nguồn": "; ".join(i["sources"])} for i in checked["issues"]]), hide_index=True, width="stretch")
            if st.button("Dùng bản đã kiểm tra cho thẩm định trong phiên", key="chief_confirm"):
                st.session_state.chief_data = preview
                st.session_state.pop("chief_audit", None)
    data = st.session_state.get("chief_data")
    if data is None:
        st.info("Nạp ví dụ tổng hợp hoặc kiểm tra file rồi xác nhận dùng. Tab này không thay nguồn hoặc lịch Telegram.")
        return
    result = evaluate(data.tables)
    result["data_label"] = data.label
    st.info(data.label+" · "+result["status"]+" · "+("đủ thành phần theo khai báo" if result["financial_complete"] else "PARTIAL"))
    question = st.text_area("Yêu cầu thẩm định", "Đánh giá P&L, điểm chưa được và ba quyết định cần chốt.", key="chief_question")
    fingerprint = hashlib.sha256((str(result)+question).encode()).hexdigest()
    if st.session_state.get("chief_fingerprint") != fingerprint:
        st.session_state.pop("chief_audit", None)
        st.session_state.chief_fingerprint = fingerprint
    allowed = st.checkbox("Cho phép gửi yêu cầu và số tổng hợp tới model đã cấu hình để Chief + Auditor xét độc lập", key="chief_live_consent")
    if st.button("Yêu cầu kiểm tra chéo AI", disabled=not (ready and allowed), key="chief_ai"):
        from ..telegram_bridge import Incoming
        from .agent import metric_context
        context = {"label": data.label, "scope": result["scope"], "metrics": metric_context(result), "chief_result": result}
        agent = _review_agent(configs, context)
        with st.spinner("Chief thẩm định và Auditor xét đúng phiên bản..."):
            text = agent.answer(Incoming(-1, 1, 1, None, "chief", question))
        if agent.last_presentation:
            st.session_state.chief_audit = deepcopy(agent.last_presentation["audit"])
        else:
            st.warning(text)
        agent.clear()
    from .agent import review_text
    audit = st.session_state.get("chief_audit")
    report = render(result, audit_text=review_text(audit) if audit else "")
    st.markdown(report)
    st.download_button("Tải báo cáo thẩm định", report.encode("utf-8"), "CHIEF_REVIEW.md", mime="text/markdown", key="chief_report")
    with st.expander("Truy vết số và kiểm tra bảo toàn"):
        st.dataframe(pd.DataFrame(metric_rows(result)), hide_index=True, width="stretch")
        for label, rows in (("P&L B2B theo đơn", result["orders"]), ("Phân bổ đủ B2B/B2C", result["allocations"]),
                            ("Chuyến/ngày/tuyến đã khai báo", result["trips"]), ("Bảo toàn VND", result["checks"])):
            st.write(label)
            st.dataframe(pd.DataFrame(rows).astype(str), hide_index=True, width="stretch")
        if audit:
            st.json({"task_id": audit["task_id"], "decisions": audit["audit_decisions"], "source_issues": audit["source_issues"], "review_complete": audit["review_complete"]})
