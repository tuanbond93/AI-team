import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import streamlit as st
from .schema import SPECS, AGENT_NAMES, DOMAINS
from .ingestion import read_file, map_columns, validate
from .metrics import compute, evaluate_targets, workforce_comparisons
from .analysis import compare_periods, what_if
from .demo import sample_workbook_bytes
from .agents import run_team
from .reporting import build_report, fmt, report_bundle
from .views import coverage, issues_view, evidence_view, live_settings, answer_view, UNIT_CONSTANT
from .muse import muse_bundle, demo_muse_bundle, runtime_handoff_text
from .limits import UsageWindow
from .supra.ui import render_supra, stop_api
from .telegram_ui import render_telegram, stop_telegram
from .chief.ui import render_chief


def clear_analysis():
    st.session_state.pop("run", None)
    st.session_state.pop("reviews", None)


def change_mode():
    stop_telegram()
    clear_analysis()


def set_demo():
    st.session_state.tables = read_file(sample_workbook_bytes(), "DU_LIEU_DEMO.xlsx")
    st.session_state.data_label = "DỮ LIỆU DEMO"
    st.session_state.numeric_format = "machine"
    st.session_state.dataset_version = st.session_state.get("dataset_version", 0)+1
    for key in ("previous", "current", "import_preview", "scenarios", "validation_cache"):
        st.session_state.pop(key, None)
    clear_analysis()


def merge_label(old, incoming_demo, replace_all, empty):
    if replace_all or empty:
        return "DỮ LIỆU DEMO" if incoming_demo else "DỮ LIỆU NGƯỜI DÙNG — CHƯA XÁC NHẬN ĐẦY ĐỦ"
    if "HỖN HỢP" in old or ((old == "DỮ LIỆU DEMO") != incoming_demo):
        return "DỮ LIỆU HỖN HỢP — CÓ DEMO, KHÔNG DÙNG QUYẾT ĐỊNH THẬT"
    return old


def render_import(validation):
    st.header("Nhập dữ liệu có kiểm tra trước khi áp dụng")
    st.caption("1. Chọn file → 2. Ánh xạ / đơn vị / định dạng số → 3. Xem lỗi và phạm vi ngày → 4. Xác nhận nhập. Không sửa file nguồn.")
    uploads = st.file_uploader("Chọn Excel / CSV (tối đa 20 MB mỗi file)", type=["xlsx", "csv"], accept_multiple_files=True)
    if uploads:
        numeric_format = st.selectbox("Định dạng số văn bản trong file", ["machine", "vi", "en"],
            format_func=lambda f: {"machine": "Số thuần: 1234.56 (không phân nhóm)", "vi": "Việt Nam: 1.234,56", "en": "Quốc tế: 1,234.56"}[f], key="import_number_format")
        st.caption("Ô số thực trong Excel được giữ đúng giá trị. Quy tắc trên chỉ áp dụng ô văn bản/CSV; dữ liệu sai quy tắc bị cảnh báo, không tự đoán.")
        loaded, assignments, errors, mapping_summary = {}, [], [], []
        for i, file in enumerate(uploads):
            digest = hashlib.sha256(file.getvalue()).hexdigest()[:10]
            with st.expander(f"File {i+1}: {file.name}", expanded=True):
                header = st.number_input("Dòng tiêu đề vật lý", min_value=1, max_value=50, value=1, key=f"header_{i}_{digest}")
                delimiter = "auto"
                if file.name.lower().endswith(".csv"):
                    delimiter = st.selectbox("Dấu phân cột CSV", ["auto", ",", ";", "\t", "|"],
                        format_func=lambda d: {"auto": "Nhận diện dấu phân cột", ",": "Dấu phẩy (,)", ";": "Chấm phẩy (;)", "\t": "Tab", "|": "Gạch đứng (|)"}[d], key=f"delimiter_{i}_{digest}")
                try:
                    loaded[i] = read_file(file.getvalue(), file.name, int(header), delimiter)
                except ValueError as error:
                    st.error(str(error))
                    errors.append(str(error))
                    continue
                except Exception:
                    st.error("Không đọc được workbook. Kiểm tra file .xlsx không mã hóa, UTF-8 và dung lượng; xuất lại bản sao.")
                    errors.append("Không đọc được file.")
                    continue
                for sheet, df in loaded[i].items():
                    key = f"map_{i}_{digest}_{header}_{delimiter}_{sheet}"
                    st.markdown("**Sheet: " + str(sheet) + f" · {len(df):,} dòng**")
                    st.dataframe(df.head(8), hide_index=True, width="stretch")
                    options = ["Bỏ qua", *SPECS]
                    guess = sheet if sheet in SPECS else next((kind for kind in SPECS if file.name.lower() == f"demo_{kind}.csv"), "Bỏ qua")
                    kind = st.selectbox("Nhóm dữ liệu", options, index=options.index(guess), key=key,
                        format_func=lambda k: SPECS[k].label if k in SPECS else k)
                    mapping, constants = {}, {}
                    if kind != "Bỏ qua":
                        spec = SPECS[kind]
                        st.caption("Mức chi tiết / khóa: " + spec.grain)
                        cols = ["Chưa chọn", *[c for c in df if not c.startswith("_")]]
                        grid = st.columns(3)
                        for j, col in enumerate(spec.columns):
                            choices = cols + ([UNIT_CONSTANT+spec.units[col]] if col in spec.units else [])
                            if col == "document_ref":
                                choices += ["Chưa có tham chiếu chứng từ"]
                            value = grid[j % 3].selectbox(col, choices, index=choices.index(col) if col in choices else 0, key=key+"_"+kind+"_"+col)
                            if value.startswith(UNIT_CONSTANT):
                                constants[col] = spec.units[col]
                            elif value == "Chưa có tham chiếu chứng từ":
                                constants[col] = ""
                            else:
                                mapping[col] = None if value == "Chưa chọn" else value
                            mapping_summary.append({"File / sheet": file.name+" / "+str(sheet), "Bảng": spec.label, "Trường đích": col,
                                                    "Nguồn / hằng số": value})
                    assignments.append((i, sheet, None if kind == "Bỏ qua" else kind, mapping, constants))
        uses_constants = any(a[4] for a in assignments)
        constant_signature = hashlib.sha256(json.dumps([(a[0], a[1], a[2], a[4]) for a in assignments], sort_keys=True).encode()).hexdigest()[:12]
        constants_confirmed = st.checkbox("Tôi xác nhận các hằng số đơn vị / chứng từ trống đã chọn", key="constants_confirm_"+constant_signature) if uses_constants else True
        replace_all = st.checkbox("Thay toàn bộ bộ dữ liệu hiện tại", value=True)
        known_demo = any("demo" in f.name.casefold() for f in uploads) or any(df[c].astype(str).str.startswith("DEMO-").any()
            for sheets in loaded.values() for df in sheets.values() for c in ("record_id", "trip_id") if c in df)
        incoming_demo = st.checkbox("Các file đang nhập là DỮ LIỆU DEMO", value=known_demo) or known_demo
        signature = hashlib.sha256(json.dumps([[(a[0], a[1], a[2], a[3], a[4]) for a in assignments],
            [hashlib.sha256(f.getvalue()).hexdigest() for f in uploads], numeric_format, replace_all, incoming_demo], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if st.button("Kiểm tra bản nhập", type="primary", disabled=bool(errors) or not constants_confirmed):
            try:
                result, selected = ({} if replace_all else dict(st.session_state.tables)), set()
                for i, sheet, kind, mapping, constants in assignments:
                    if kind:
                        if kind in selected:
                            raise ValueError("Hai sheet cùng nhóm trong một lượt. Hãy gộp cùng mức chi tiết trước khi nhập.")
                        selected.add(kind)
                        result[kind] = map_columns(loaded[i][sheet], kind, mapping, constants)
                if not selected:
                    raise ValueError("Chọn ít nhất một nhóm dữ liệu.")
                # A retained table has already been interpreted in the previous locale.
                # Canonicalize its numeric fields before mixing locale conventions.
                if not replace_all:
                    for kind in set(result)-selected:
                        result[kind] = validation.tables[kind].copy()
                        if validation.rejected[kind]:
                            raise ValueError("Bảng giữ lại còn dòng cách ly. Hãy thay toàn bộ hoặc xử lý nguồn trước khi trộn định dạng số.")
                check = validate(result, numeric_format)
                st.session_state.import_preview = {"tables": result, "validation": check, "signature": signature,
                    "number_format": numeric_format, "label": merge_label(st.session_state.data_label, incoming_demo, replace_all, not st.session_state.tables)}
            except ValueError as error:
                st.error(str(error))
        preview = st.session_state.get("import_preview")
        if preview and preview["signature"] == signature:
            st.markdown("### Xem lại trước khi nhập")
            st.dataframe(pd.DataFrame(mapping_summary), hide_index=True, width="stretch")
            st.info(preview["label"])
            issues_view(preview["validation"])
            st.dataframe(coverage(preview["validation"]), hide_index=True, width="stretch")
            acknowledge = st.checkbox("Tôi đã xem các vấn đề và hiểu chỉ số thiếu nguồn sẽ chưa được kết luận") if preview["validation"].issues else True
            if st.button("Xác nhận nhập dữ liệu", type="primary", disabled=not acknowledge or not constants_confirmed):
                st.session_state.tables = preview["tables"]
                st.session_state.numeric_format = preview["number_format"]
                st.session_state.data_label = preview["label"]
                st.session_state.dataset_version += 1
                for key in ("previous", "current", "import_preview", "validation_cache", "scenarios"):
                    st.session_state.pop(key, None)
                clear_analysis()
                st.rerun()
        elif preview:
            st.caption("Ánh xạ hoặc lựa chọn đã đổi. Bấm Kiểm tra bản nhập để tạo bản xem lại mới.")
    st.markdown("### Bộ dữ liệu đang dùng")
    st.dataframe(pd.DataFrame([{"Nhóm": s.label, "Dòng nguồn": validation.raw_counts[k], "Hợp lệ": len(validation.tables[k]),
        "Cách ly": len(validation.rejected[k]), "Mức chi tiết": s.grain} for k, s in SPECS.items()]), hide_index=True, width="stretch")
    if st.session_state.tables:
        issues_view(validation)
        with st.expander("Phạm vi ngày và mức phủ của từng kỳ", expanded=True):
            st.dataframe(coverage(validation), hide_index=True, width="stretch")


def scenarios_view(validation, current, warehouses, categories):
    st.caption("Hai phương án độc lập. Chỉ tính từ giả định bạn nhập; không cộng lợi ích hai phương án và không tự dự đoán SLA.")
    candidates = []
    for category, label in (("labor", "Cân ca / OT"), ("fleet", "Tải xe / km rỗng")):
        with st.expander("What-if: " + label, expanded=True):
            a, b, c = st.columns(3)
            rate = a.number_input("Giảm chi phí loại này (%)", min_value=0.0, max_value=100.0, value=None, key="scenario_rate_"+category)
            setup = b.number_input("Chi phí triển khai kỳ này (VND)", min_value=0.0, value=0.0, key="scenario_setup_"+category)
            volume_change = c.number_input("Thay đổi sản lượng (%)", min_value=-99.0, max_value=1000.0, value=0.0, key="scenario_volume_"+category)
            with_sla = st.checkbox("Tôi có giả định riêng về ảnh hưởng SLA", key="scenario_has_sla_"+category)
            sla_delta = st.number_input("Thay đổi SLA (điểm phần trăm)", min_value=-100.0, max_value=100.0, value=0.0, key="scenario_sla_"+category) if with_sla else None
            note = st.text_input("Cơ sở và điều kiện của giả định", key="scenario_note_"+category, max_chars=1000)
            if rate is not None:
                try:
                    item = what_if(validation, current, warehouses, category, rate, setup, volume_change, sla_delta, note, categories)
                    candidates.append(item)
                    st.write("Tiết kiệm ròng giả định: **" + fmt(item["net_savings_vnd"], "VND") + "** · Chi phí/kiện sau giả định: **" + fmt(item["projected_cost_per_unit"], "VND/kiện") + "**")
                    st.caption(item["formula"] + " · " + item["sla_status"])
                    if not item["ready"]:
                        st.warning("Cần ghi cơ sở giả định và dữ liệu chi phí đúng phạm vi trước khi đưa vào báo cáo.")
                except ValueError as error:
                    st.error(str(error))
    confirm = st.checkbox("Tôi xác nhận giả định what-if, gồm chi phí triển khai và phần chưa biết về SLA")
    if st.button("Đưa what-if vào phiên phân tích", disabled=not candidates or not confirm or not all(x["ready"] for x in candidates)):
        changed = st.session_state.get("scenarios") != candidates
        st.session_state.scenarios = candidates
        if changed:
            clear_analysis()
        st.success("Đã lưu giả định trong RAM. Chạy đội agent để phân tích/kiểm toán các phương án mới trước khi xem xét.")


def render_overview(validation, current, previous, scope, categories):
    st.header("Bức tranh vận hành")
    if not current or not scope:
        st.info("Bắt đầu bằng Nạp dữ liệu demo hoặc nhập file ở Nguồn dữ liệu. Một kỳ cũng xem được tổng quan; kỳ trước là tùy chọn.")
        return
    metrics = compute(validation, current, scope, categories)
    comparison = compare_periods(validation, current, previous, scope, categories)
    changes = {r["id"]: r for r in comparison["changes"]}
    keys = ["cost", "cost_per_unit", "sla", "ot_rate", "load_weight", "empty_rate"]
    for start in (0, 3):
        for column, key in zip(st.columns(3), keys[start:start+3]):
            m = metrics[key]
            display = fmt(m.value, m.unit)
            if m.value is not None and key == "cost":
                display = f"{m.value/1e6:.2f}".replace(".", ",") + " triệu ₫"
            elif m.value is not None and key == "cost_per_unit":
                display = f"{m.value:,.0f}".replace(",", ".") + " ₫/kiện"
            delta = changes[key]["delta"]
            column.metric(m.label, display, delta=fmt(delta, "đpt" if m.unit == "%" else m.unit) if delta is not None else None,
                delta_color="normal" if key in ("sla", "load_weight") else "inverse", help=m.formula+" · "+m.status)
    if comparison["limitation"]:
        st.info(comparison["limitation"])
    domain = st.tabs(["Chi phí & kế toán", "Vận hành & KPI", "Nhân sự", "Xe", "What-if", "Bằng chứng"])
    with domain[0]:
        st.caption("Chỉ sổ chi phí được cộng vào tổng. Chi tiết nhân sự và xe dùng để đối soát, không cộng lần hai.")
        bridge = comparison["bridge"]
        if bridge["ready"]:
            st.markdown("### Phân rã thay đổi chi phí/kiện")
            st.dataframe(pd.DataFrame([{"Thành phần": "Ảnh hưởng sản lượng (giữ C0)", "VND/kiện": bridge["volume_effect"]},
                {"Thành phần": "Ảnh hưởng chi phí (giữ Q1)", "VND/kiện": bridge["cost_effect"]},
                {"Thành phần": "Tổng biến động", "VND/kiện": bridge["total_change"]}]), hide_index=True, width="stretch")
            st.caption(bridge["formula"]+" · "+bridge["order"])
        breakdown = comparison["breakdown"]
        if breakdown:
            st.dataframe(pd.DataFrame([{ "Kho": r["warehouse"], "Loại chi phí": r["category"], "Kỳ trước": r["previous"],
                "Kỳ hiện tại": r["current"], "Chênh lệch": r["delta"]} for r in breakdown]), hide_index=True, width="stretch")
            chart = [{"Kho / loại": r["warehouse"]+" / "+r["category"], "Kỳ": current, "Triệu VND": None if r["current"] is None else r["current"]/1e6} for r in breakdown]
            st.bar_chart(pd.DataFrame(chart), x="Kho / loại", y="Triệu VND", color="Kỳ")
        from .metrics import reconcile
        st.markdown("### Đối soát sổ với chi tiết")
        st.dataframe(pd.DataFrame([{"Kho": r["warehouse"], "Loại": r["category"], "Lệch (VND)": r["difference"], "Kết quả": r["status"]}
            for r in reconcile(validation, current, scope)]), hide_index=True, width="stretch")
    with domain[1]:
        st.write("Sản lượng: **"+fmt(metrics["volume"].value, "kiện")+"** · Tồn snapshot cuối: **"+fmt(metrics["backlog"].value, "kiện")+"**")
        targets = evaluate_targets(validation, current, scope)
        if targets:
            st.dataframe(pd.DataFrame([{"Kho": r["warehouse"], "KPI": metrics[r["metric"]].label, "Thực tế (%)": r["value"],
                "Mục tiêu (%)": r["target"], "Quy tắc": "Tối thiểu" if r["direction"] == "min" else "Tối đa",
                "Đánh giá": "Thiếu dữ liệu" if r["breach"] is None else "Không đạt" if r["breach"] else "Đạt", "Ghi chú": r["assumption"]} for r in targets]), hide_index=True, width="stretch")
        else:
            st.warning("Chưa có mục tiêu KPI; không tự áp ngưỡng. Cần quản lý xác nhận.")
    with domain[2]:
        rows = []
        for group in workforce_comparisons(validation, current, previous, scope):
            old, new = group["periods"].get(previous), group["periods"].get(current)
            rows.append({"Kho": group["warehouse"], "Công đoạn": group["stage"], "Loại hàng": group["goods_type"], "Ca": group["shift"],
                "Kỳ trước (kiện/giờ)": old["productivity"] if old else None, "Kỳ này (kiện/giờ)": new["productivity"] if new else None,
                "Lỗi kỳ trước (%)": old["defect_rate"] if old else None, "Lỗi kỳ này (%)": new["defect_rate"] if new else None})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption("Chỉ đối chiếu cùng công đoạn × loại hàng × ca, luôn xem cùng chất lượng. Không dùng năng suất gộp để xếp hạng cá nhân.")
    with domain[3]:
        st.dataframe(pd.DataFrame([{"Chỉ số": metrics[k].label, "Kỳ trước": changes[k]["previous"], "Kỳ này": metrics[k].value,
            "Đơn vị": metrics[k].unit, "Tình trạng": metrics[k].status} for k in ("load_weight", "load_volume", "empty_rate", "wait_hours", "cost_per_trip")]), hide_index=True, width="stretch")
        overload = [r for r in validation.issues if r["code"] == "OVER_CAPACITY"]
        if overload:
            st.warning("Có chuyến vượt sức chứa khai báo. Giữ số để cảnh báo và xác minh quá tải/đơn vị.")
            st.dataframe(pd.DataFrame(overload), hide_index=True, width="stretch")
    with domain[4]:
        scenarios_view(validation, current, scope, categories)
    with domain[5]:
        evidence_view(metrics)
        with st.expander("Phạm vi ngày quan sát — chưa xác nhận đủ tháng"):
            st.dataframe(coverage(validation), hide_index=True, width="stretch")
    if validation.issues:
        st.warning(f"{len(validation.issues)} vấn đề/cảnh báo; xem Nguồn dữ liệu để xử lý theo dòng và cột.")


def render_report(run, private_fragments=()):
    st.header("Kết luận và quyết định chờ quản lý")
    st.write("**"+run.status+"**")
    a, b, c = st.columns(3)
    a.write("Nguồn: " + ("Đạt cấu trúc" if run.audit["sources_passed"] else "Cần bổ sung"))
    b.write("Auditor: " + ("Đã xét hết phiên bản" if run.audit["review_complete"] else "Còn phiên bản mở"))
    c.write("Nội dung: " + ("Không còn vướng mắc ghi nhận" if run.audit["passed"] else "Chờ xác minh / quyết định"))
    if run.scenarios:
        review_context = str(id(run))+"_"+hashlib.sha256(json.dumps(run.scenarios, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:10]
        st.markdown("### Xem xét phạm vi thử nghiệm (chỉ ghi nhận trong RAM)")
        st.caption("Đây là ghi nhận ý kiến quản lý, không phải lệnh chi tiền, điều xe hoặc thay đổi ca. Đổi giả định sẽ xóa ý kiến cũ.")
        with st.form("management_review_"+review_context):
            proposed = {}
            for scenario in run.scenarios:
                prior = run.reviews.get(scenario["id"], {})
                states = ["Chờ xem xét", "Duyệt phạm vi thử nghiệm", "Yêu cầu bổ sung", "Không duyệt"]
                decision = st.selectbox(scenario["label"], states, index=states.index(prior.get("status", "Chờ xem xét")), key="review_"+review_context+scenario["id"])
                note = st.text_input("Ghi chú / điều kiện duyệt", value=prior.get("note", ""), key="review_note_"+review_context+scenario["id"], max_chars=1000)
                proposed[scenario["id"]] = {"status": decision, "note": note}
            if st.form_submit_button("Ghi nhận xem xét trong phiên"):
                stamp = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).isoformat(timespec="seconds")
                run.reviews = {k: {**value, "reviewed_at": stamp} for k, value in proposed.items()}
                st.success("Đã ghi nhận trong RAM. Không thực hiện thay đổi vận hành.")
    full_report = build_report(run)
    if any(value in full_report for value in private_fragments if value and len(value) >= 8):
        st.error("Báo cáo chứa nội dung trùng khóa hoặc chỉ dẫn riêng của phiên. Đã chặn hiển thị/xuất; hãy xóa khỏi ghi chú hoặc giả định rồi thử lại.")
        return
    left, right = st.columns(2)
    left.download_button("Tải báo cáo Markdown", full_report.encode("utf-8"), "bao_cao_quan_tri.md", mime="text/markdown", type="primary")
    right.download_button("Tải gói báo cáo + bằng chứng + ý kiến duyệt", report_bundle(run), "bao_cao_bang_chung.zip", mime="application/zip")
    try:
        handoff = muse_bundle(run, private_fragments)
        st.download_button("Tải gói kết quả hiện tại để chuyển sang Muse", handoff, "MUSE_KET_QUA.zip", mime="application/zip")
        st.caption("Gói hiện tại có thể chứa dữ liệu thật nếu bạn đã nhập. Chỉ tự chuyển sang Muse sau khi kiểm tra nội dung và quyền chia sẻ.")
    except ValueError as exc:
        st.warning(str(exc))
    st.markdown(build_report(run, compact=True))


def main():
    st.set_page_config(page_title="AI Operations Team", page_icon=":material/hub:", layout="wide")
    st.markdown("""<style>
    :root{--ops-blue:#1e40af;--ops-ink:#17243c;--ops-muted:#475569;--ops-bg:#f8fafc;--ops-line:#dbeafe}
    .stApp{background:var(--ops-bg);color:var(--ops-ink)}.block-container{padding-top:3.5rem;max-width:1500px}
    h1,h2,h3{letter-spacing:-.025em}h1{font-size:2.5rem!important}.ops-kicker{color:var(--ops-blue);font-size:.8rem;font-weight:700;letter-spacing:.12em}
    [data-testid="stMetric"]{background:#fff;border:1px solid var(--ops-line);border-radius:12px;padding:14px}
    [data-testid="stMetricValue"]{font-size:clamp(1.15rem,1.8vw,1.8rem);color:var(--ops-ink);font-variant-numeric:tabular-nums}
    .stButton button,.stDownloadButton button{min-height:44px}button:focus-visible{outline:3px solid var(--ops-blue)!important;outline-offset:3px}
    @media(max-width:768px){.block-container{padding:3.5rem 1rem 1rem}h1{font-size:1.9rem!important}[data-testid="stMetricValue"]{font-size:1.5rem}p{overflow-wrap:anywhere}}
    @media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
    </style>""", unsafe_allow_html=True)
    if "tables" not in st.session_state:
        st.session_state.tables, st.session_state.data_label, st.session_state.dataset_version = {}, "CHƯA CÓ DỮ LIỆU", 0
        st.session_state.numeric_format = "machine"
    with st.sidebar:
        st.header("Phiên phân tích")
        mode = st.radio("Chế độ agent", ["Demo", "Live"], key="mode", on_change=change_mode)
        st.button("Nạp dữ liệu demo", on_click=set_demo, type="primary", width="stretch")
        st.download_button("Tải file Excel mẫu", sample_workbook_bytes(), "DU_LIEU_DEMO.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch")
        if st.button("Xóa dữ liệu & khóa phiên", width="stretch"):
            stop_telegram()
            stop_api()
            if "supra_store" in st.session_state:
                st.session_state.supra_store.close()
            st.session_state.clear()
            st.rerun()
    cache_key = (st.session_state.dataset_version, st.session_state.numeric_format)
    cached = st.session_state.get("validation_cache")
    if not cached or cached[0] != cache_key:
        cached = (cache_key, validate(st.session_state.tables, st.session_state.numeric_format))
        st.session_state.validation_cache = cached
    validation = cached[1]
    periods = sorted({str(p) for df in validation.tables.values() for p in df.period.dropna()})
    warehouses = sorted({str(w) for df in validation.tables.values() for w in df.warehouse.dropna()})
    categories = sorted({str(c) for kind in ("costs", "budgets") for c in validation.tables[kind].category.dropna()})
    with st.sidebar:
        if periods:
            if st.session_state.get("current") not in periods:
                st.session_state.pop("current", None)
            current = st.selectbox("Kỳ phân tích", periods, index=len(periods)-1, key="current", on_change=clear_analysis)
            past = [None]+[p for p in periods if p < current]
            if st.session_state.get("previous") not in past:
                st.session_state.pop("previous", None)
            previous = st.selectbox("Kỳ so sánh (tùy chọn)", past, index=len(past)-1, key="previous", on_change=clear_analysis,
                format_func=lambda p: p or "Không so sánh")
            scope = st.multiselect("Kho trong phạm vi", warehouses, default=warehouses, key=f"scope_{st.session_state.dataset_version}", on_change=clear_analysis)
            selected_categories = st.multiselect("Loại chi phí (áp dụng cùng hai kỳ)", categories, default=categories,
                key=f"categories_{st.session_state.dataset_version}", on_change=clear_analysis)
        else:
            current, previous, scope, selected_categories = None, None, [], None
        with st.expander("Dữ liệu phiên & quyền"):
            st.caption("Nguồn đọc nguyên trạng. Khóa, chỉ dẫn/skill và cấu hình chỉ ở RAM. Báo cáo xuất không chứa prompt/payload. Mọi đề xuất chờ quản lý; không tự chi tiền/điều xe/đổi nhân sự.")
    st.markdown('<div class="ops-kicker">VẬN HÀNH LOGISTICS / WORKSPACE LOCAL</div>', unsafe_allow_html=True)
    st.title("AI Operations Team")
    st.caption("Chi phí, chất lượng và năng lực — có bằng chứng, có kiểm tra chéo, quyết định bởi bạn.")
    st.info(st.session_state.data_label+" · "+("AGENT MÔ PHỎNG — 0 API" if mode == "Demo" else "LIVE — chỉ gọi khi bạn bật"))
    if not st.session_state.tables:
        with st.container(border=True):
            st.markdown("### Thử trong 5 phút")
            st.write("**1.** Nạp demo hoặc tải file mẫu · **2.** Kiểm tra dữ liệu và kỳ · **3.** Chạy đội agent · **4.** Xem kiểm toán và xuất báo cáo.")
            st.caption("File thật: xuất Excel/CSV từ Google Sheets rồi dùng Nguồn dữ liệu. Không cần API để thử Demo.")
    tabs = st.tabs(["Tổng quan", "Nguồn dữ liệu", "Đội agent", "Báo cáo", "Cấu hình Live", "Muse thủ công", "Supra Account AI", "Telegram", "Chief Auditor"])
    with tabs[0]:
        render_overview(validation, current, previous, scope, selected_categories)
    with tabs[1]:
        render_import(validation)
    configs, ready = {}, False
    with tabs[4]:
        if mode == "Live":
            configs, ready = live_settings()
            fragments = set(st.session_state.get("private_export_fragments", []))
            fragments.update(value for c in configs.values() for value in (c.api_key, c.instructions) if value and len(value) >= 8)
            st.session_state.private_export_fragments = list(fragments)
        else:
            st.info("Chọn Live ở thanh bên để cấu hình profile/model. Demo không cần khóa và không gọi API.")
    with tabs[5]:
        st.header("Thử DEMO với Muse đã đăng nhập")
        st.write("1. Tải gói DEMO dưới đây và giải nén. 2. Mở Muse bạn đã đăng nhập, đính kèm các file trong gói. 3. Sao chép nội dung hướng dẫn, gửi và nhận báo cáo ngay trong Muse.")
        st.caption("Chưa xác nhận API miễn phí tổng quát để app gọi Muse. Không dùng cookie/credential đăng nhập làm API key. App không tự upload hoặc đọc phản hồi từ Muse.")
        if "muse_demo_bundle" not in st.session_state:
            st.session_state.muse_demo_bundle = demo_muse_bundle()
        st.download_button("Tải gói DEMO cho Muse (không cần key)", st.session_state.muse_demo_bundle, "MUSE_DEMO.zip", mime="application/zip")
        st.text_area("Hướng dẫn để sao chép sang Muse (chỉ trong RAM)", runtime_handoff_text(), height=220, key="muse_runtime_handoff")
        st.warning("Phản hồi Muse chưa qua Auditor của app. App không đánh dấu hoàn thành kiểm toán từ văn bản bên ngoài. Gói DEMO luôn dùng dữ liệu giả độc lập với file bạn nhập.")
    with tabs[2]:
        st.header("Phòng phân tích")
        st.caption("Giao việc → hỏi chéo → Auditor xét từng phiên bản → sửa và kiểm toán lại → Coordinator chỉ tổng hợp bản chấp nhận. Kiểm tra nguồn không thay thế kiểm toán nội dung.")
        with st.expander("8 vai trò và trách nhiệm"):
            st.dataframe(pd.DataFrame([{"Agent": n, "Chuyên môn": DOMAINS[n]} for n in AGENT_NAMES]), hide_index=True, width="stretch")
        question = st.text_area("Câu hỏi quản trị", "Chi phí, OT, hiệu suất xe và SLA kỳ này bất thường ở đâu? Ưu tiên kiểm tra gì trước khi duyệt?", key="question", max_chars=8000, on_change=clear_analysis)
        cols = st.columns(3)
        max_calls = cols[0].number_input("Tối đa lượt gọi / bước mô phỏng", min_value=3, max_value=40, value=24)
        pilot = mode == "Live" and st.session_state.get("free_pilot", False)
        limit = cols[1].number_input("Giới hạn phiên (giây)", min_value=10, max_value=600, value=600 if pilot else 240)
        timeout = cols[2].number_input("Timeout mỗi lần gọi (giây)", min_value=1, max_value=60, value=30)
        token_budget = st.number_input("Ngân sách token ước tính mỗi lượt (gồm đầu vào + dự phòng đầu ra)", min_value=1000, max_value=200000, value=60000, step=1000) if pilot else 60000
        if pilot:
            st.info("FREE PILOT: 7.500 token/phút ước tính trong RAM, dưới mốc công bố 8K; gồm schema và dự phòng đầu ra. Đội 8 vai trò có thể chờ vài phút hoặc chưa hoàn tất. Bộ đếm theo phiên local, không kiểm soát mức dùng ngoài app/ngày hoặc hóa đơn. Yêu cầu quá lớn sẽ bị chặn, không cắt ngầm phiên bản/bằng chứng.")
        activated = st.checkbox("Tôi chủ động bật Live và cho phép gửi câu hỏi, chỉ dẫn/skill, số liệu tổng hợp và tham chiếu nguồn tới provider đã chọn") if mode == "Live" else False
        if mode == "Live" and not ready:
            st.warning("Cấu hình Live chưa sẵn sàng; xem tab Cấu hình Live. Không thử gọi API khi thiếu model/khóa/skill hợp lệ.")
        if st.button("Chạy đội agent", type="primary", disabled=not(current and scope) or (mode == "Live" and (not ready or not activated))):
            clear_analysis()
            status, bar = st.empty(), st.progress(0.0)
            def update(name, number):
                status.info(f"Nhiệm vụ {number:02} · {name} · đang phân tích / kiểm toán")
                bar.progress(min(.95, number/int(max_calls)))
            try:
                with st.spinner("Đang kiểm tra chéo và xét phiên bản..."):
                    if "pilot_window" not in st.session_state:
                        st.session_state.pilot_window = UsageWindow()
                    st.session_state.run = run_team(validation, current, previous, scope, question, mode, configs, activated,
                        st.session_state.data_label, int(max_calls), int(limit), int(timeout), progress=update,
                        categories=selected_categories, scenarios=st.session_state.get("scenarios", []), free_pilot=pilot,
                        token_budget=int(token_budget), usage_window=st.session_state.pilot_window if pilot else None)
                bar.progress(1.0)
                status.info(st.session_state.run.status)
            except ValueError as error:
                status.error(str(error))
        if "run" in st.session_state:
            run = st.session_state.run
            st.write(f"**{run.status}** · {len(run.exchanges)} nhiệm vụ · {run.calls} lượt API · {run.duration}s")
            if run.audit.get("free_pilot_budget"):
                budget_info = run.audit["free_pilot_budget"]
                st.caption(f"FREE PILOT · {budget_info['attempts_including_retry']}/{budget_info['max_calls']} HTTP attempts (gồm retry) · {budget_info['reserved_tokens_estimate']:,}/{budget_info['max_tokens_estimate']:,} token dự phòng ước tính. Không phải hóa đơn hoặc quota tổ chức/ngày.")
            with st.expander("Lịch sử kiểm toán theo phiên bản", expanded=True):
                history_rows = [{**r, "verdict": {"accept": "Chấp nhận", "revise": "Trả lại", "unresolved": "Chưa chốt"}.get(r["verdict"], r["verdict"])} for r in run.audit["history"]]
                st.dataframe(pd.DataFrame(history_rows).rename(columns={"review_id": "Lượt kiểm toán", "task_id": "Mã phiên bản",
                    "logical_id": "Nhiệm vụ gốc", "version": "Phiên bản số", "verdict": "Quyết định", "reason": "Lý do"}), hide_index=True, width="stretch")
            for e in run.exchanges:
                with st.expander(f"{e.task_id} · {e.sender} → {e.receiver} · v{e.version} · {e.status}"):
                    answer_view(e, run.metrics)
            if run.disagreements:
                st.markdown("### Còn mở")
                for item in run.disagreements:
                    st.warning(item)
    with tabs[3]:
        if "run" in st.session_state:
            render_report(st.session_state.run, st.session_state.get("private_export_fragments", []))
        else:
            st.info("Chạy đội agent để tổng hợp báo cáo. Một kỳ cũng phân tích được; so sánh hai kỳ chỉ khi có kỳ trước.")
    with tabs[6]:
        render_supra(configs, ready)
    with tabs[7]:
        render_telegram(configs, ready, validation, current, previous, scope, selected_categories)
    with tabs[8]:
        render_chief(configs, ready)
