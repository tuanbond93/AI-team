"""Supra area inside the existing AI Team dashboard; never a separate app."""
from pathlib import Path
from copy import deepcopy
from decimal import Decimal
import json
import secrets
import pandas as pd
import streamlit as st
from .domain import Principal, encode, identity, number
from .ingestion import read_workbook, read_document, read_actual_csv
from .service import SupraService
from .store import EventStore
from .api import LocalAPI, AccessGate
from .capacity import fleet_review, capacity_case
from .workflow import ShadowWorkflow, MockGTalkAdapter, detect_incidents, proposal
from .agent import analyze_account
from .ledger import DIMENSIONS
from .daily import daily_incidents
from .legacy import read_legacy_month
from ..providers import ProviderError
from ..limits import UsageWindow
from ..reporting import fmt, csv_bytes

ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = Path.home()/"Desktop"/"Super Tracking chi phí Supra"
XLSX_NAME = "Supra_Phu_Tho_PnL_CostToServe_Pilot14Ngay.xlsx"
DOCX_NAME = "Phuong_An_Toi_Uu_Chi_Phi_Supra_Phu_Tho.docx"
VIEWER = Principal("Chủ máy local / chỉ xem")
ANALYST = Principal("Supra Account AI", permissions=frozenset({"VIEW_PNL", "ANALYZE", "NOTIFY_MOCK"}))


def frame(value):
    records = json.loads(encode(value))
    # Heterogeneous event bodies are business text in the viewer, not Arrow
    # structs. Keep the canonical typed values in the engine/export unchanged.
    records = [{k: encode(v) if isinstance(v, (dict, list)) else v for k, v in row.items()} for row in records]
    return pd.DataFrame(records).rename(columns={"route": "Tuyến", "name": "Tên", "hub": "Hub", "flow": "Luồng",
        "revenue": "Doanh thu VND", "cost": "Chi phí VND", "profit": "Lãi/lỗ VND", "source": "Nguồn", "sources": "Các nguồn",
        "verification": "Đối soát", "mode": "Loại số", "cost_per_kg": "VND/kg", "cost_per_pcs": "VND/pcs",
        "cost_per_drop": "VND/drop", "contribution_margin": "Contribution margin VND", "stores": "Số điểm kế hoạch",
        "amount": "Tiền phân bổ VND", "category": "Nhóm phí", "pool_id": "Khoản gốc", "cost_basis": "Cơ sở phí",
        "status": "Trạng thái", "finding": "Phát hiện", "missing_data": "Dữ liệu còn thiếu", "computed": "Số đã tính",
        "legal_verified": "Tải hợp pháp đã xác minh", "feasible": "Khả thi", "utilization": "Tỷ lệ tải", "fact": "Dữ kiện nguồn",
        "expected_savings_vnd": "Tiết kiệm dự kiến VND", "verified_savings_vnd": "Tiết kiệm đã xác minh VND"})


def display_number(value, unit):
    if value is None:
        return "Chưa đủ dữ liệu"
    if unit == "%":
        value *= 100
    return f"{value:,.2f}".translate(str.maketrans({",": ".", ".": ","}))+" "+unit


def stop_api():
    handle = st.session_state.pop("supra_api_handle", None)
    if handle:
        handle.close()
    st.session_state.pop("supra_api_token", None)


def clear_source_preview():
    st.session_state.pop("supra_preview", None)


def activate_service(service, store):
    stop_api()
    store.save_version(service.dataset, service.dataset["version"]+":source", "SOURCE_VERSION", "local-import",
        {"source": service.dataset["source"], "mode": "PLAN", "verification": "UNVERIFIED"})
    st.session_state.supra_service = service
    st.session_state.pop("supra_ai_result", None)


def scoped_export(value):
    text = encode(value)
    if any(s in text for s in st.session_state.get("private_export_fragments", []) if s and len(s) >= 8):
        raise ValueError("Nội dung xuất trùng khóa/chỉ dẫn riêng của phiên; đã chặn xuất.")
    return text.encode("utf-8")


def render_supra(configs=None, live_ready=False):
    st.header("Supra Account AI · Phú Thọ")
    st.caption("AI Team → Customer Operations Agent → SUPRA / PHU_THO · COST_TO_SERVE · SHADOW")
    st.info("PLAN và proxy từ đề án chưa là P&L thực tế. Dòng chữ phê duyệt trong tài liệu không cấp quyền cho app. Không tự gửi GTalk, điều xe, sửa giá/hợp đồng hoặc gửi khách hàng.")
    with st.expander("Nguồn và lịch sử phiên bản", expanded="supra_service" not in st.session_state):
        st.caption("Hai file nguồn chỉ đọc. Bản chuẩn hóa và sự kiện nghiệp vụ được lưu local ở .supra_state; khóa/chỉ dẫn/skill/payload AI chỉ trong RAM, không vào SQLite/Git. Chưa có SSO/ACL nhiều người dùng; chỉ dùng loopback trên máy tin cậy.")
        uploads = st.file_uploader("Tài liệu Supra (.xlsx và .docx)", type=["xlsx", "docx"], accept_multiple_files=True, key="supra_sources", on_change=clear_source_preview)
        a, b = st.columns(2)
        local = a.button("Đọc hai tài liệu Supra đã chỉ định", key="supra_read_local")
        uploaded = b.button("Kiểm tra tài liệu Supra tải lên", disabled=not uploads, key="supra_read_upload")
        if local or uploaded:
            clear_source_preview()
            try:
                if local:
                    xb, db = (SOURCE_ROOT/XLSX_NAME).read_bytes(), (SOURCE_ROOT/DOCX_NAME).read_bytes()
                    xn, dn = XLSX_NAME, DOCX_NAME
                else:
                    books = [u for u in uploads if u.name.lower().endswith(".xlsx")]
                    docs = [u for u in uploads if u.name.lower().endswith(".docx")]
                    if len(books) != 1 or len(docs) != 1:
                        raise ValueError("Cần đúng một workbook Supra và một đề án DOCX.")
                    xb, db, xn, dn = books[0].getvalue(), docs[0].getvalue(), books[0].name, docs[0].name
                dataset = read_workbook(xb, xn)
                document = read_document(db, dn)
                candidate = SupraService(dataset, document)
                scoped_export(dataset)  # Guard known session secrets before preview/storage.
                candidate.query_pnl(VIEWER)
                st.session_state.supra_preview = candidate
            except (OSError, ValueError, KeyError, TypeError) as exc:
                st.error("Chưa nhập nguồn: " + str(exc))
        preview = st.session_state.get("supra_preview")
        if preview:
            d = preview.dataset
            st.write({"PLAN": "UNVERIFIED", "tuyến": len(d["routes"]), "điểm tạm": len(d["stores"]),
                "actual nhập": len(d["actuals"]), "version": d["version"], "lỗi": d["issues"]})
            if st.button("Xác nhận nhập Supra và lưu phiên bản nghiệp vụ", key="supra_confirm"):
                if "supra_store" not in st.session_state:
                    st.session_state.supra_store = EventStore(ROOT/".supra_state"/"supra.sqlite3", st.session_state.get("private_export_fragments", []))
                activate_service(preview, st.session_state.supra_store)
                st.session_state.pop("supra_preview", None)
                st.rerun()
        if "supra_store" not in st.session_state and (ROOT/".supra_state"/"supra.sqlite3").exists():
            if st.button("Mở lịch sử nghiệp vụ local", key="supra_open_history"):
                st.session_state.supra_store = EventStore(ROOT/".supra_state"/"supra.sqlite3", st.session_state.get("private_export_fragments", []))
                st.rerun()
        if "supra_store" in st.session_state:
            store = st.session_state.supra_store
            versions = store.versions(VIEWER)
            if versions:
                version = st.selectbox("Phiên bản nguồn để xem lại", versions, key="supra_history")
                if st.button("Xem lại phiên bản (không ghi đè nguồn)", key="supra_restore"):
                    activate_service(SupraService(store.load_snapshot(VIEWER, version)), store)
                    st.rerun()
    if "supra_service" not in st.session_state:
        st.write("Đọc và xác nhận hai tài liệu để mở P&L, đối soát baseline và pilot shadow.")
        return
    service, store = st.session_state.supra_service, st.session_state.supra_store
    store.private_fragments = list(st.session_state.get("private_export_fragments", []))
    days = st.selectbox("Ngày vận hành PLAN cùng một tháng", [26, 30], key="supra_days")
    plan = service.query_pnl(VIEWER, days=days)
    actual = service.query_pnl(VIEWER, mode="ACTUAL")
    findings = service.reconciliation(VIEWER)
    st.caption(f"Version {service.dataset['version'][:16]} · nguồn {service.dataset['source']['file']} · dữ liệu cập nhật lúc: chưa xác minh; thời điểm nhập {service.dataset['source'].get('imported_at')}")
    sections = st.tabs(["Executive Overview", "Route P&L", "Fleet & Capacity", "AI Recommendations", "Incident & Approval"])
    with sections[0]:
        for col, key, label in zip(st.columns(3), ("revenue", "cost", "profit"), ("Doanh thu PLAN", "Chi phí PLAN", "Lãi/lỗ PLAN")):
            col.metric(label, fmt(plan["summary"][key], "VND"))
        st.caption("Contribution margin = doanh thu trừ các pool ngoài xe 5T khoán tháng; phân loại phí hub cố định/biến đổi chưa được xác minh. Lãi/lỗ là phạm vi chi phí khai báo, chưa là lợi nhuận ròng kế toán.")
        summary = plan["summary"]
        kpis = [("Contribution margin (phân loại tạm)", "contribution_margin", "VND"), ("Chi phí/kg", "cost_per_kg", "VND/kg"),
            ("Chi phí/pcs", "cost_per_pcs", "VND/pcs"), ("Chi phí/drop thực", "cost_per_drop", "VND/drop"),
            ("Chi phí/xe kế hoạch-ngày", "cost_per_vehicle_day", "VND"), ("Chi phí/tuyến bình quân", "cost_per_route", "VND"),
            ("Chi phí/điểm kế hoạch bình quân", "cost_per_store", "VND"), ("Chi phí/doanh thu", "cost_revenue_ratio", "%")]
        st.dataframe(pd.DataFrame([{"Chỉ tiêu": label, "Giá trị PLAN": display_number(summary[key], unit)} for label, key, unit in kpis]), hide_index=True, width="stretch")
        st.warning("ACTUAL trong các dòng nguồn đã nhập, chưa đại diện toàn tỉnh/tháng: "+actual["verification"]+" · P&L: "+fmt(actual["summary"]["profit"], "VND")+f" · {actual['observed_days']} ngày quan sát")
        pilot = service.pilot_status(VIEWER)
        st.write(f"Pilot shadow {pilot['start']} → {pilot['end']}: {pilot['verified_route_days']}/{pilot['expected_route_days']} ngày-tuyến được Finance xác nhận. Chưa đối soát đầy đủ điểm/xe; chưa có tiết kiệm đã xác minh.")
        st.caption("Theo dõi khi nạp dữ liệu; chưa có worker theo lịch hoặc theo dõi 14 ngày ngoài đời đã hoàn tất.")
        with st.expander("Nguồn lịch sử Supra sẵn có — đọc riêng, không là ACTUAL đã đối soát"):
            st.caption("Tái sử dụng snapshot/validator từ project Supra cạnh hai tài liệu. Không chạy script đồng bộ/publish, không đọc credential, không sửa dữ liệu project đó. Chỉ lấy Phú Thọ; cost vẫn là mô hình và tập khách hàng B2B cần xác minh.")
            legacy_month = st.selectbox("Tháng snapshot nguồn để kiểm tra", ["2026-10", "2026-08", "2026-06"], key="supra_legacy_month")
            if st.button("Đọc và liên kết snapshot lịch sử Phú Thọ", key="supra_legacy_read"):
                try:
                    snapshot = read_legacy_month(VIEWER, SOURCE_ROOT, legacy_month)
                    scoped_export(snapshot)
                    activate_service(service.attach_legacy(ANALYST, store, snapshot), store)
                    st.rerun()
                except (ValueError, OSError, KeyError, TypeError) as exc:
                    st.error(str(exc))
            legacy = service.dataset.get("legacy_snapshot")
            if legacy:
                st.warning("HISTORICAL_SOURCE_MODEL / UNVERIFIED — không cộng vào PLAN hoặc ACTUAL; chưa xác minh customer ID, giá/chi phí và toàn bộ kỳ.")
                st.write({"tháng": legacy["month"], "cập nhật nguồn": legacy["source_updated_at"], "nguồn đủ kỳ": legacy["source_complete"],
                    "ngày quan sát Phú Thọ": legacy["observed_days"], "đơn vị nguồn": legacy["source_weight_unit"], "validator tái dùng": legacy["validator_reused"]})
                st.dataframe(frame([legacy["summary"]]), hide_index=True, width="stretch")
                st.caption("Lineage riêng Phú Thọ; không lấy cước xe tổng nhiều tỉnh làm cước riêng Phú Thọ.")
                st.dataframe(frame(legacy["lineage"]), hide_index=True, width="stretch")
                for note in legacy["limitations"]:
                    st.caption(note)
        st.markdown("**BASELINE_RECONCILIATION**")
        st.dataframe(frame([{k: row[k] for k in ("id", "status", "finding", "missing_data")} for row in findings]), hide_index=True, width="stretch")
        with st.expander("Số đã tính và nguồn của từng kiểm tra baseline"):
            finding_id = st.selectbox("Kiểm tra cần truy vết", [r["id"] for r in findings], key="supra_finding")
            st.json(json.loads(encode(next(r for r in findings if r["id"] == finding_id))))
        try:
            st.download_button("Tải P&L + phân bổ + đối soát nguồn (JSON)", scoped_export({"plan": plan, "actual": actual, "BASELINE_RECONCILIATION": findings}), "SUPRA_PNL_RECONCILIATION.json", mime="application/json")
        except ValueError as exc:
            st.error(str(exc))
    with sections[1]:
        selected = st.selectbox("Tuyến Supra", [None]+[r["route"] for r in plan["routes"]], format_func=lambda v: v or "Toàn tỉnh", key="supra_route")
        rows = [r for r in plan["routes"] if selected is None or r["route"] == selected]
        st.dataframe(frame(rows), hide_index=True, width="stretch")
        st.dataframe(frame([r for r in plan["allocations"] if selected is None or r["route"] == selected]), hide_index=True, width="stretch")
        with st.expander("Điểm giao — phân bổ PLAN, key tạm"):
            st.warning("Chưa có mã cửa hàng thật. Chi phí dưới đây chia đều trong tuyến để đối chiếu tổng, không là CTS thực tế theo cửa hàng.")
            st.dataframe(frame([r for r in service.store_plan(VIEWER, days) if selected is None or r["route"] == selected]), hide_index=True, width="stretch")
        st.caption("Chưa có đơn hàng/booking/GHN, biển số xe hay sổ phân bổ có chứng từ; không dựng P&L thực tế cấp đơn/xe từ tỷ lệ chia đều.")
        dimension = st.selectbox("Chiều truy vấn sổ chi tiết độc lập", DIMENSIONS, index=3, key="supra_dimension")
        detailed = service.query_detail(VIEWER, dimension)
        st.write("Sổ chi tiết: "+detailed["verification"]+" · không cộng vào sổ route/day")
        if detailed["groups"]:
            st.dataframe(frame(detailed["groups"]), hide_index=True, width="stretch")
    with sections[2]:
        peak = st.number_input("Hệ số peak khối lượng (PLAN)", min_value=1.0, max_value=3.0, value=1.0, step=.05, key="supra_peak")
        ecom = st.number_input("Kg Ecom thêm vào đội Việt Trì/ngày (giả định)", min_value=0, value=0, key="supra_ecom")
        st.dataframe(frame(fleet_review(service.dataset, peak, ecom)), hide_index=True, width="stretch")
        st.caption("Không coi tải danh định là tải hợp pháp hoặc hai lượt lý thuyết là quay vòng đã chứng minh. FTL NCC chưa có tải trọng/thể tích. Không tự ghép Ecom khi dư tải kg nhưng thiếu thể tích/SLA.")
        with st.expander("Kiểm tra lịch hai lượt lấy thẳng — nhập số đo trước khi kết luận"):
            cols = st.columns(4)
            timing = [col.number_input(label, min_value=0, max_value=1000, value=None, key="supra_time_"+str(i)) for i, (col, label) in enumerate(zip(cols, ("Bốc DC (phút)", "Chạy đi (phút)", "Phục vụ điểm (phút)", "Quay về (phút)")))]
            if st.button("Kiểm tra lịch Đoan Hùng (chỉ PLAN)", key="supra_check_schedule"):
                st.write(json.loads(encode(capacity_case(3220, 1, service.dataset["assumptions"]["capacity19"], 2,
                    loading=timing[0], outbound=timing[1], service=timing[2], returning=timing[3]))))
    with sections[3]:
        st.markdown("**Phương án có thể kiểm tra, chưa có quyền thực thi**")
        proposals = []
        refs = [service.dataset["refs"]["01_GIA_DINH!B13"]]
        for title in ("Qua hub so với lấy thẳng DC", "Xe 1.9T so với xe tải lớn", "Thuê tháng so với thuê chuyến", "Một chuyến lớn so với nhiều chuyến nhỏ", "Ghép Ecom so với xe chuyên Supra", "Gom ngày giao so với giao hàng ngày"):
            proposals.append(proposal(plan["summary"]["cost"], None, title=title, sources=refs,
                constraints="Không vượt tải hợp pháp; kiểm tra DC 09–17 và giờ nhận điểm; không cắt xe khi chưa đủ pilot",
                missing="Báo giá cùng phạm vi, GPS, SLA hợp đồng, thể tích, Ecom và điều khoản NCC",
                rollback="Giữ/khôi phục lịch xe và điều khoản cũ sau khi quản lý phê duyệt; không tự thực hiện", approver="Finance + Quản lý vận hành"))
        st.dataframe(frame(proposals), hide_index=True, width="stretch")
        with st.expander("Độ nhạy NCC Thanh Sơn–Thanh Thủy — giả định tuyến tính"):
            result = []
            for drops in (10, 15, 21):
                changed = deepcopy(service.dataset)
                changed["assumptions"]["ncc_drops"] = Decimal(drops)
                case = SupraService(changed).query_pnl(VIEWER, days=days, route="TSTT")
                result.append({"drops_day": drops, "pnl_month_plan": case["summary"]["profit"], "rule": "Thay đúng pool last-mile, không cộng chồng; chưa xác nhận hợp đồng tuyến tính"})
            st.dataframe(frame(result), hide_index=True)
        st.caption("Đề xuất trên là rule/template có nguồn, chưa phải kết quả LLM. Không có báo giá thì chi phí sau/tiết kiệm để trống. Tiết kiệm dự kiến và đã xác minh luôn tách riêng.")
        consent = st.checkbox("Tôi cho phép gửi số liệu PLAN Supra và mẫu nguồn tới model Cost + Auditor đã cấu hình trong Live", key="supra_ai_consent")
        if st.button("Phân tích Supra bằng model đã cấu hình", disabled=not (consent and live_ready and configs), key="supra_ai_run"):
            try:
                if "pilot_window" not in st.session_state:
                    st.session_state.pilot_window = UsageWindow()
                st.session_state.supra_ai_result = analyze_account(service, VIEWER, configs["Cost"], configs["Auditor"], activated=True, window=st.session_state.pilot_window)
            except (ValueError, ProviderError, PermissionError) as exc:
                st.error(str(exc))
        if "supra_ai_result" in st.session_state:
            result = st.session_state.supra_ai_result
            st.text(result["answer"]["summary"])
            st.write("Auditor chấp nhận phiên bản diễn giải: "+str(result["review_complete"]))
            st.caption(result["limitation"])
    with sections[4]:
        workflow = ShadowWorkflow(store)
        incidents = detect_incidents(service.dataset, plan, actual)
        daily = daily_incidents(service.dataset.get("operational_events", []))
        incidents.extend(daily["incidents"])
        st.dataframe(frame(incidents), hide_index=True, width="stretch")
        if daily["missing_data"]:
            st.dataframe(frame(daily["missing_data"]), hide_index=True)
        if st.button("Ghi nhận incident/proposal shadow (chống trùng)", key="supra_record_incidents"):
            for item in incidents:
                workflow.register(ANALYST, item, "INCIDENT")
            for item in proposals:
                workflow.register(ANALYST, item)
            st.success("Đã ghi sổ nghiệp vụ local. Chưa gửi GTalk hoặc thay đổi vận hành.")
        with st.expander("Quyền Finance/phê duyệt cho phiên local tin cậy"):
            st.caption("Chủ máy tự thiết lập quyền shadow trong RAM; không thay thế SSO/danh tính GHN và chưa dùng cho triển khai nhiều người. Quyền xem P&L không có quyền duyệt.")
            with st.form("supra_role_setup"):
                manager_name = st.text_input("Tên người chịu trách nhiệm đối soát/duyệt", key="supra_manager_name")
                new_token = st.text_input("Token quản lý mới (ít nhất 24 ký tự, RAM)", type="password", key="supra_manager_new_token")
                setup = st.form_submit_button("Thiết lập quyền shadow trong phiên local")
                if setup:
                    if len(new_token) < 24 or not manager_name.strip():
                        st.error("Cần tên người chịu trách nhiệm và token ít nhất 24 ký tự.")
                    else:
                        manager = Principal(manager_name.strip(), permissions=frozenset({"VIEW_PNL", "APPROVE", "EXECUTE_SHADOW", "IMPORT_ACTUAL", "VERIFY_FINANCE"}))
                        st.session_state.supra_gate = AccessGate({new_token: manager})
                        fragments = set(st.session_state.get("private_export_fragments", []))
                        fragments.add(new_token)
                        st.session_state.private_export_fragments = list(fragments)
                        st.success("Đã thiết lập trong RAM, chưa cấp quyền tác động vận hành thật.")
            auth_token = st.text_input("Token để dùng quyền Finance/phê duyệt", type="password", key="supra_manager_auth")
        manager = None
        if auth_token and "supra_gate" in st.session_state:
            try:
                manager = st.session_state.supra_gate.authenticate("Bearer "+auth_token)
            except PermissionError:
                st.error("Token quyền phiên không hợp lệ.")
        actual_file = st.file_uploader("ACTUAL route/day CSV (không tự VERIFIED)", type=["csv"], key="supra_actual_upload")
        st.caption("Cột: date,route,kg,pcs,drops,on_time,price_pcs,document_ref,dc,linehaul,hub,last_mile,delivery_labor,extra. Tiền VND, kg/pcs/drop đúng đơn vị; đủ sáu khoản kể cả 0. Nhập thay sổ actual của phiên bản, không cộng hai sổ.")
        if st.button("Nhập ACTUAL chưa đối soát thành phiên bản mới", disabled=manager is None or actual_file is None, key="supra_import_actual"):
            try:
                scoped_export(actual_file.getvalue().decode("utf-8-sig"))
                changed = service.import_actual(manager, read_actual_csv(actual_file.getvalue(), actual_file.name), store)
                activate_service(changed, store)
                st.rerun()
            except (ValueError, PermissionError, UnicodeError) as exc:
                st.error(str(exc))
        verification_ref = st.text_input("Biên bản Finance đối soát nguồn/giá/chi phí/SLA", key="supra_verify_ref")
        if st.button("Finance xác nhận actual trong phiên bản mới", disabled=manager is None, key="supra_verify_actual"):
            try:
                changed = service.verify_actual(manager, store, verification_ref)
                activate_service(changed, store)
                st.rerun()
            except (ValueError, PermissionError) as exc:
                st.error(str(exc))
        with st.expander("Nhập sổ chi tiết / timeline JSON có nguồn (tùy chọn)"):
            st.caption("Sổ tài chính: object revenue/pools/allocations; doanh thu từng dòng tính cước, pool hóa đơn và phân bổ phải bảo toàn tổng. Scope SUPRA/PHU_THO. Timeline: danh sách event_id, route, mốc ISO có timezone và ngưỡng nguồn. Mọi trạng thái nhập bị hạ UNVERIFIED, không lấy quyền từ file.")
            kind = st.selectbox("Loại sổ JSON", ["detail_ledger", "operational_events"], key="supra_json_kind")
            file = st.file_uploader("JSON nghiệp vụ Supra", type=["json"], key="supra_business_json")
            if st.button("Nhập JSON thành phiên bản nghiệp vụ mới", disabled=manager is None or file is None, key="supra_import_json"):
                try:
                    scoped_export(file.getvalue().decode("utf-8-sig"))
                    changed = service.import_business_json(manager, store, file.getvalue(), file.name, kind)
                    activate_service(changed, store)
                    st.rerun()
                except (ValueError, TypeError, AttributeError, PermissionError) as exc:
                    st.error("Chưa nhập: "+str(exc))
            if st.button("Finance xác nhận sổ chi tiết đã đối soát", disabled=manager is None or not service.dataset.get("detail_ledger"), key="supra_verify_detail"):
                try:
                    changed = service.verify_detail(manager, store, verification_ref)
                    activate_service(changed, store)
                    st.rerun()
                except (ValueError, PermissionError) as exc:
                    st.error(str(exc))
        ids = [e["entity"] for e in store.events() if e["kind"] == "PROPOSAL"]
        if ids:
            chosen = st.selectbox("Proposal trong sổ shadow", ids, key="supra_proposal_id")
            reason = st.text_input("Lý do quyết định/rollback", key="supra_decision_reason")
            cols = st.columns(4)
            action = next((name for col, name in zip(cols, ("Duyệt shadow", "Từ chối", "Chạy shadow", "Rollback shadow")) if col.button(name, disabled=manager is None, key="supra_"+name)), None)
            if action:
                try:
                    if action in ("Duyệt shadow", "Từ chối"):
                        workflow.decide(manager, chosen, action == "Duyệt shadow", reason)
                    elif action == "Chạy shadow":
                        workflow.execute(manager, chosen)
                    else:
                        workflow.rollback(manager, chosen, reason)
                    st.success("Đã ghi nhận shadow; không có tác động vận hành hoặc tài chính thật.")
                except (ValueError, PermissionError) as exc:
                    st.error(str(exc))
            st.write(workflow.state(VIEWER, chosen)["status"])
            with st.expander("Đối chiếu outcome ACTUAL, không tự ghi nhận tiết kiệm nhân quả"):
                base_version = st.selectbox("Phiên bản ACTUAL baseline để so", store.versions(VIEWER), key="supra_outcome_baseline")
                if st.button("Ghi nhận chênh lệch actual đã Finance đối soát", disabled=manager is None or not service.dataset.get("detail_ledger"), key="supra_outcome"):
                    try:
                        before = SupraService(store.load_snapshot(manager, base_version))
                        st.write(json.loads(encode(workflow.record_outcome(manager, chosen, before, service, verification_ref))))
                    except (ValueError, PermissionError) as exc:
                        st.error(str(exc))
            if st.button("Thử adapter GTalk mock (không gửi)", key="supra_gtalk_mock"):
                st.write(MockGTalkAdapter(store).send(ANALYST, chosen, "Supra shadow proposal chờ quản lý; chưa gửi thực tế."))
        st.caption("Audit chain local: "+("khớp" if store.verify_chain() else "LỖI toàn vẹn; dừng nghiệm thu"))
        with st.expander("Lịch sử quyết định nghiệp vụ"):
            st.dataframe(frame(store.events()), hide_index=True, width="stretch")
        with st.expander("API chỉ đọc local, token trong RAM"):
            st.caption("GET /v1/supra/pnl?mode=PLAN&days=26 hoặc /v1/supra/reconciliation. Bearer token bắt buộc; không CORS/cookie, không POST thực thi. Token không vào log/SQLite/Git.")
            if "supra_api_handle" not in st.session_state:
                if st.button("Bật API chỉ đọc tại 127.0.0.1:8502", key="supra_start_api"):
                    try:
                        token = secrets.token_urlsafe(32)
                        st.session_state.supra_api_handle = LocalAPI(service, token, VIEWER)
                        st.session_state.supra_api_token = token
                        st.session_state.private_export_fragments = list(set(st.session_state.get("private_export_fragments", [])) | {token})
                        st.rerun()
                    except (OSError, ValueError) as exc:
                        st.error("Không mở được API local; không dừng process khác. "+str(exc))
            else:
                st.text_input("API Bearer token phiên (RAM)", value=st.session_state.supra_api_token, type="password", disabled=True, key="supra_api_token_view")
                st.caption("API gắn đúng phiên bản đang xem; đổi nguồn/version sẽ dừng API, cần bật lại.")
                if st.button("Dừng API và thu hồi token phiên", key="supra_stop_api"):
                    stop_api()
                    st.rerun()
