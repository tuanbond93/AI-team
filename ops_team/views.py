"""Vietnamese presentation helpers; runtime session state is never persisted."""
import pandas as pd
import streamlit as st
from .schema import SPECS, AGENT_NAMES, DOMAINS
from .providers import ENDPOINTS, ModelConfig, FREE_MODELS, probe_model, validate_free_config, ProviderError
from .limits import UsageWindow
from .reporting import fmt, csv_bytes

UNIT_CONSTANT = "Hằng số đã xác nhận: "


def coverage(validation):
    rows = []
    for kind, data in validation.tables.items():
        for period, part in data.groupby("period"):
            column = "date" if "date" in part else "service_date" if "service_date" in part else None
            dates = part[column].dropna() if column else pd.Series(dtype=str)
            rows.append({"Nhóm": SPECS[kind].label, "Kỳ": period, "Số dòng": len(part),
                "Kho": ", ".join(sorted(part.warehouse.unique())), "Từ ngày": dates.min() if len(dates) else "Không có ngày chi tiết",
                "Đến ngày": dates.max() if len(dates) else "Không có ngày chi tiết", "Số ngày có bản ghi": dates.nunique(),
                "Ghi chú": "Phạm vi ngày quan sát; chưa xác nhận đủ tháng"})
    return pd.DataFrame(rows)


def issues_view(validation):
    if not validation.issues:
        st.success("Không phát hiện lỗi theo các quy tắc đang hỗ trợ. Cần xác nhận độ đầy đủ với hệ thống gốc.")
        return
    df = pd.DataFrame(validation.issues).rename(columns={"table": "Bảng", "code": "Mã kiểm tra", "source": "File / sheet / dòng",
        "column": "Cột", "detail": "Vấn đề", "action": "Cách xử lý đề xuất", "severity": "Mức độ"})
    st.dataframe(df, hide_index=True, width="stretch")


def evidence_view(metrics, before=None, key_prefix="evidence"):
    options = {m.id: m for m in metrics.values()}
    selected = st.selectbox("Chỉ số cần kiểm tra bằng chứng", list(options), format_func=lambda k: options[k].label, key=key_prefix)
    item = options[selected]
    st.write(f"**{item.label}** · {fmt(item.value, item.unit)} · {item.status}")
    st.caption(f"Kỳ {item.period or 'chưa chọn'} · {item.scope}")
    st.code(item.formula, language=None)
    source = pd.DataFrame({"File / hash / sheet / dòng": item.sources})
    st.caption(f"{len(source):,} tham chiếu nguồn. Hiển thị 200 dòng đầu; tải để xem đầy đủ.")
    st.dataframe(source.head(200), hide_index=True, width="stretch")
    st.download_button("Tải bằng chứng chỉ số (CSV)", csv_bytes(source),
        f"bang_chung_{selected}.csv", mime="text/csv", key=key_prefix+"_download")


def live_settings():
    st.header("Cấu hình model theo phiên")
    pilot = st.checkbox("FREE PILOT — dùng chung một khóa Groq", key="free_pilot")
    st.caption("Khóa, chỉ dẫn và skill chỉ trong RAM; mất khi xóa phiên hoặc khởi động lại server.")
    profiles = {}
    confirmed = False
    if pilot:
        st.info("Groq Free Plan thử nghiệm: app chỉ cho provider/model trong danh sách và ngân sách theo lượt. App không đọc billing, không bảo đảm hóa đơn 0 hoặc hạn mức cả ngày. Không tự đổi provider/model hay chuyển Demo khi lỗi.")
        st.markdown("[Tạo khóa Groq](https://console.groq.com/keys) · [Kiểm tra Limits / gói tài khoản](https://console.groq.com/settings/limits) · [Hạn mức công bố](https://console.groq.com/docs/rate-limits)")
        confirmed = st.checkbox("Tôi đã kiểm tra tài khoản Groq đang ở Free Plan, chưa bật billing", key="free_account_confirmed")
    groups = (("FREE PILOT / cả 8 vai trò", "free"),) if pilot else (("Điều phối", "coord"), ("Chuyên viên", "specialist"), ("Kiểm toán độc lập", "audit"))
    for title, profile in groups:
        with st.expander("Profile " + title, expanded=True):
            columns = st.columns(2)
            provider = columns[0].selectbox("Provider", ["Groq"] if pilot else list(ENDPOINTS), key="group_provider_"+profile)
            model = columns[1].selectbox("Tên model", FREE_MODELS, key="group_model_"+profile) if pilot else columns[1].text_input("Tên model", key="group_model_"+profile, placeholder="Định danh model có quyền truy cập")
            secret = st.text_input("API key (RAM)", type="password", key="group_key_"+profile) if provider != "Ollama (local)" else ""
            output = st.selectbox("Định dạng đầu ra", ["json_schema"] if pilot else ["json_schema", "json_object"], key="group_format_"+profile,
                help="JSON Schema kiểm soát cấu trúc. Chỉ chọn JSON object khi model/provider không hỗ trợ schema; không tự đổi khi lỗi.")
            tokens = st.number_input("Giới hạn token đầu ra", min_value=1500 if pilot else 256, max_value=4000 if pilot else 16000, value=3000, step=256, key="group_tokens_"+profile)
            profiles[profile] = ModelConfig(provider, model, secret, structured_output=output, max_output_tokens=int(tokens), free_pilot=pilot, free_account_confirmed=confirmed)
            st.caption("Kiểm tra cấu hình local không gọi HTTP. Nút dưới gửi duy nhất dữ liệu giả cố định rất nhỏ; không gửi file, câu hỏi hay chỉ dẫn của phiên.")
            if st.button("Kiểm tra kết nối/model bằng dữ liệu giả", key="probe_"+profile,
                         disabled=not model or (provider != "Ollama (local)" and not secret) or (pilot and not confirmed)):
                if "pilot_window" not in st.session_state:
                    st.session_state.pilot_window = UsageWindow()
                with st.spinner("Đang kiểm tra HTTP bằng dữ liệu giả..."):
                    outcome = probe_model(profiles[profile], window=st.session_state.pilot_window)
                if outcome["ok"]:
                    st.success("Kết nối + JSON hợp lệ. Model thực tế: " + outcome["model"])
                    st.write({"usage": outcome["usage"], "HTTP attempts": outcome["attempts"]})
                    st.caption("Probe không xác nhận chất lượng phân tích, gói miễn phí hay quota còn lại cho cả đội.")
                else:
                    st.error(outcome["error"])
    if pilot:
        profiles = {name: profiles["free"] for name in ("coord", "specialist", "audit")}
    configs, readiness = {}, []
    for name in AGENT_NAMES:
        group = "coord" if name == "Coordinator" else "audit" if name == "Auditor" else "specialist"
        config = profiles[group]
        with st.expander(name + " — " + DOMAINS[name]):
            override = st.checkbox("Ghi đè provider/model riêng", key="override_"+name)
            if override:
                provider = st.selectbox("Provider", ["Groq"] if pilot else list(ENDPOINTS), key=("free_provider_" if pilot else "provider_")+name)
                model = st.selectbox("Tên model", FREE_MODELS, key="free_model_"+name) if pilot else st.text_input("Tên model", key="model_"+name)
                key = st.text_input("API key (RAM)", type="password", key=("free_secret_" if pilot else "secret_")+name) if provider != "Ollama (local)" else ""
                if pilot and not key:
                    key = config.api_key
                    st.caption("Để trống khóa riêng sẽ dùng khóa Groq chung.")
                output = st.selectbox("Định dạng đầu ra", ["json_schema"] if pilot else ["json_schema", "json_object"], key=("free_format_" if pilot else "format_")+name)
                config = ModelConfig(provider, model, key, structured_output=output, max_output_tokens=config.max_output_tokens, free_pilot=pilot, free_account_confirmed=confirmed)
            instructions = st.text_area("Chỉ dẫn bổ sung (RAM)", key="instructions_"+name, max_chars=16000)
            skill = st.file_uploader("Skill Markdown UTF-8 (tối đa 32 KB)", type=["md", "txt"], key="skill_"+name)
            skill_text, skill_ok = "", True
            if skill:
                try:
                    if skill.size > 32768:
                        raise ValueError()
                    skill_text = skill.getvalue().decode("utf-8-sig")
                except (ValueError, UnicodeDecodeError):
                    skill_ok = False
                    st.error("Skill không hợp lệ: cần UTF-8 và không vượt 32 KB. Bỏ file hoặc sửa để chạy Live.")
            config = ModelConfig(config.provider, config.model.strip(), config.api_key, "\n".join(s for s in [instructions, skill_text] if s),
                config.structured_output, config.max_output_tokens, pilot, confirmed)
            configs[name] = config
            ready = bool(config.model and (config.api_key.strip() or config.provider == "Ollama (local)")) and skill_ok
            if pilot:
                try:
                    validate_free_config(config)
                except ProviderError:
                    ready = False
            readiness.append({"Agent": name, "Provider": config.provider, "Model yêu cầu": config.model or "Chưa nhập",
                "Đầu ra": config.structured_output, "Trạng thái cấu hình": "Sẵn sàng" if ready else "Còn thiếu / skill lỗi", "_ready": ready})
    st.markdown("### Kiểm tra sẵn sàng")
    st.dataframe(pd.DataFrame([{k: v for k, v in r.items() if k != "_ready"} for r in readiness]), hide_index=True, width="stretch")
    st.caption("Bảng trên chỉ kiểm tra cấu hình local. Probe là HTTP riêng với dữ liệu giả. Chỉ lúc chủ động chạy Live mới gửi câu hỏi, chỉ dẫn/skill và dữ liệu tổng hợp tới provider.")
    return configs, all(r["_ready"] for r in readiness)


def answer_view(exchange, metrics):
    st.caption(f"{exchange.task_id} · phiên bản {exchange.version} · nhiệm vụ {exchange.logical_id} · thay {exchange.replaces or '—'}")
    left, right = st.columns(2)
    left.write("**Kiểm tra nguồn:** " + exchange.source_status)
    right.write("**Kiểm toán nội dung:** " + exchange.review_status)
    st.caption(f"{exchange.provider} / {exchange.model or 'chưa chạy thành công'} · vòng {exchange.round}")
    if exchange.usage:
        st.caption("Token API báo: " + ", ".join(f"{k}: {v}" for k, v in exchange.usage.items()))
    if exchange.error:
        st.error(exchange.error)
    if exchange.answer:
        st.text(exchange.answer["summary"])
        for label, field in (("Nhận định có nguồn (diễn giải chưa xác nhận)", "observations"),
                             ("Giả thuyết", "hypotheses"), ("Đề xuất chờ duyệt", "proposals"), ("Bất đồng còn mở", "disagreements")):
            values = exchange.answer.get(field, [])
            if values:
                st.markdown("**"+label+"**")
            for item in values:
                if isinstance(item, dict):
                    st.text("• " + item["text"])
                    st.caption("Bằng chứng: " + ", ".join(metrics[k].label if k in metrics else k for k in item["evidence_ids"]))
                else:
                    st.text("• " + item)
    with st.expander("Chi tiết nhiệm vụ / dữ liệu kỹ thuật"):
        st.text(exchange.question)
        st.caption(exchange.scope + " · " + exchange.expected)
        st.json(exchange.answer, expanded=False)
