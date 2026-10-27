"""Local DPAPI onboarding and controls for the independent Telegram worker."""
from dataclasses import fields
import json
from pathlib import Path
import re
import subprocess
import time
import streamlit as st
from .demo import sample_workbook_bytes
from .telegram_bridge import TelegramAPI
from .telegram_state import BusinessState, ROOT, GROUP_ID
from .telegram_secrets import load_settings, save_settings, load_protected
from .telegram_assistant import MEMORY_HELP


def stop_telegram():
    handle = st.session_state.get("telegram_handle")
    if handle:
        handle.stop()
    st.session_state.pop("telegram_bot_token", None)


def stop_worker():
    if (ROOT/"business.sqlite3").exists():
        state = BusinessState()
        try:
            state.set_control("worker_enabled", False)
            state.set_control("schedule_enabled", False)
        finally:
            state.close()


def forget_credentials():
    stop_worker()
    for name in ("settings.dpapi", "pair.dpapi"):
        path = ROOT/name
        if path.exists():
            path.unlink()
    stop_telegram()


def parse_user_ids(text):
    if not text.strip():
        return ()
    parts = re.split(r"[,\s]+", text.strip())
    if len(parts) > 20 or any(not re.fullmatch(r"[1-9][0-9]{0,15}", p) for p in parts):
        raise ValueError("Nhập tối đa 20 User ID dạng số dương, cách nhau bằng dấu phẩy.")
    return tuple(sorted({int(p) for p in parts}))


def worker_status():
    if not (ROOT/"business.sqlite3").exists():
        return {}
    state = BusinessState()
    try:
        return {k: state.control(k) for k in ("worker_enabled", "worker_pid", "heartbeat", "paired", "schedule_enabled", "last_error")}
    finally:
        state.close()


def render_telegram(configs, ready, validation, current, previous, scope, categories):
    st.header("Telegram · Trợ lý doanh nghiệp")
    st.caption("Một bot, 8 vai trò; trí nhớ nghiệp vụ có nguồn và owner xác nhận; báo cáo theo lịch bạn duyệt.")
    st.caption("Demo/Live ở thanh bên điều khiển phân tích trên web. Worker Telegram dùng cấu hình DPAPI và nút Dừng worker riêng.")
    saved = {}
    if (ROOT/"settings.dpapi").exists():
        try:
            saved = load_settings()
        except Exception:
            st.error("Không giải mã được DPAPI bằng tài khoản Windows này; không dùng plaintext thay thế.")
    status = worker_status()
    if status.get("paired") != {"chat_id": GROUP_ID, "owner_id": saved.get("owner_id")}:
        status["paired"] = None
    running = bool(status.get("worker_enabled"))
    fragments = set(st.session_state.get("private_export_fragments", []))
    fragments.update(s for s in [saved.get("bot_token", ""), *[c.get("api_key", "") for c in saved.get("models", {}).values()]] if s)
    with st.container(border=True):
        st.subheader("1. Bot, nhóm và chủ sở hữu")
        st.markdown("Tạo bot tại [BotFather chính thức](https://t.me/BotFather) bằng `/newbot`, thêm vào nhóm làm thành viên, giữ Group Privacy bật.")
        st.text_input("Nhóm Telegram được phép", value=str(GROUP_ID), disabled=True, key="telegram_fixed_group")
        token = st.text_input("Telegram bot token (chỉ nhập tại đây)", type="password", key="telegram_bot_token", disabled=running,
            help="Lưu bằng Windows DPAPI CurrentUser. Để trống để giữ token đã mã hóa của app này.")
        owner_text = st.text_input("Telegram User ID chủ sở hữu", value=str(saved.get("owner_id") or ""), key="telegram_owner_id", disabled=running,
            help="Chưa biết ID: bật bot với ô này trống, gửi /id@ten_bot trong nhóm; bot chưa nhận giao việc. Sau đó dừng, nhập ID và bật lại.")
        members = st.text_input("User ID được hỏi/xem thêm (tùy chọn)", value=", ".join(map(str, saved.get("users", []))), key="telegram_extra_users", disabled=running)
        folder = st.text_input("Thư mục export được phép đọc (tùy chọn)", value=saved.get("allowed_folder", ""), key="telegram_allowed_folder", disabled=running,
            help="Thư mục export riêng, không đọc đệ quy. Chỉ đọc sau khi owner chọn source_id folder trên Telegram.")
        error, owner_id, users = "", None, ()
        try:
            parsed = parse_user_ids(owner_text)
            if len(parsed) > 1:
                raise ValueError("Chỉ nhập một User ID chủ sở hữu.")
            owner_id = parsed[0] if parsed else None
            users = parse_user_ids(members)
            if folder.strip() and (not Path(folder.strip()).is_absolute() or not Path(folder.strip()).is_dir()):
                raise ValueError("Thư mục export phải là đường dẫn tuyệt đối đang tồn tại.")
        except ValueError as exc:
            error = str(exc)
            st.error(error)
        st.caption("Ghép nối phải khớp nhóm, User ID owner và mã riêng. Không tự cấp owner cho người nhắn đầu tiên.")
    with st.container(border=True):
        st.subheader("2. Model và cấu hình mã hóa")
        choices = ["Chỉ onboarding và báo cáo bằng code — chưa có AI"]
        if ready:
            choices.append("Dùng model Live đang cấu hình trong phiên")
        if saved.get("models"):
            choices.append("Giữ model đã mã hóa của worker")
        ai_mode = st.selectbox("Chế độ trợ lý Telegram", choices,
            index=choices.index("Giữ model đã mã hóa của worker") if saved.get("models") else 0,
            key="telegram_ai_mode", disabled=running)
        if saved.get("models"):
            st.caption(f"Worker đã lưu {len(saved['models'])} vai trò/model bằng DPAPI; trạng thái này không xác nhận quota hoặc chất lượng phân tích.")
        google_file = st.file_uploader("Google service-account JSON (tùy chọn, giữ RAM đến khi Lưu DPAPI)", type=["json"],
            key="telegram_google_upload", disabled=running)
        st.caption("Service account là tùy chọn nếu chủ file cấp Viewer. Nếu bạn chỉ có Viewer và không cấp được quyền đó: dùng snapshot từ connector Google đã đăng nhập đúng tài khoản, qua CLI nhận stdin/RAM. Không chuyển cookie/token Google vào worker. App chưa tự tạo automation connector hoặc OAuth người dùng.")
        if not ready:
            st.info("Có thể bật onboarding chưa cần LLM key. Muốn dùng AI: chọn Live và cấu hình model ở Cấu hình Live trước khi lưu worker.")
        st.caption("Token, model key, cấu hình và giá trị bộ nhớ được mã hóa DPAPI. Chỉ dẫn/skill tùy chỉnh không được lưu; câu hỏi/payload AI và hội thoại thô vẫn chỉ ở RAM.")
        consent = st.checkbox("Tôi cho phép lưu cấu hình DPAPI và bot nhận/trả lời trong nhóm đã khóa; chỉ chia sẻ dữ liệu sau khi owner chọn nguồn, chỉ chạy lịch sau /plan và /enable",
            key="telegram_consent", disabled=running)
        if token:
            fragments.add(token)
        st.session_state.private_export_fragments = list(fragments)
        if st.button("Lưu cấu hình Telegram mã hóa", key="telegram_save", type="primary", disabled=running or not consent or bool(error) or not (token or saved.get("bot_token"))):
            try:
                selected_token = token or saved["bot_token"]
                api = TelegramAPI(selected_token)
                api.close()
                models = {}
                if ai_mode.startswith("Dùng model"):
                    models = {n: {f.name: getattr(c, f.name) for f in fields(c) if f.name != "instructions"} for n, c in configs.items()}
                elif ai_mode.startswith("Giữ model"):
                    models = saved["models"]
                google = saved.get("google_service_account", {})
                if google_file:
                    from .telegram_sheets import validate_account
                    if google_file.size > 20000:
                        raise ValueError("Credential JSON vượt giới hạn.")
                    google = validate_account(json.loads(google_file.getvalue()))
                save_settings({"bot_token": selected_token, "owner_id": owner_id, "chat_id": GROUP_ID, "users": list(users),
                    "models": models, "allowed_folder": folder.strip(), "google_service_account": google})
                if google.get("private_key"):
                    fragments.add(google["private_key"])
                    st.session_state.private_export_fragments = list(fragments)
                state = BusinessState()
                try:
                    state.set_control("schedule_enabled", False)
                    state.set_control("approved_plan", None)
                finally:
                    state.close()
                st.success("Đã lưu DPAPI; chưa gửi tin/gọi model. Khởi động worker để kiểm tra kết nối.")
            except Exception:
                st.error("Chưa lưu được DPAPI. Kiểm tra token và tài khoản Windows; không hiển thị lỗi thô.")
    with st.container(border=True):
        st.subheader("3. Khởi động và xác minh")
        left, right = st.columns(2)
        if left.button("Khởi động worker Telegram", key="telegram_start", disabled=running or not (ROOT/"settings.dpapi").exists(), width="stretch"):
            state = BusinessState()
            try:
                state.set_control("worker_enabled", True)
                launcher = Path(__file__).resolve().parents[1]/"Start-Telegram-Worker.ps1"
                result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(launcher)], cwd=launcher.parent,
                    capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
                if result.returncode:
                    state.set_control("worker_enabled", False)
                    st.error("Chưa khởi động được worker. Kiểm tra môi trường và trạng thái; stderr thô được ẩn.")
                else:
                    st.success("Đã yêu cầu chạy nền. Bấm Cập nhật trạng thái để xem kết quả kết nối.")
            except Exception:
                state.set_control("worker_enabled", False)
                st.error("Chưa khởi động được worker; cấu hình mã hóa được giữ để thử lại.")
            finally:
                state.close()
        right.button("Dừng worker và tạm dừng lịch", key="telegram_stop", on_click=stop_worker, disabled=not running, width="stretch")
        st.button("Cập nhật trạng thái Telegram", key="telegram_refresh")
        st.write(f"Worker: {'được bật' if running else 'chưa bật/đã dừng'} · PID: {status.get('worker_pid') or 'chưa có'} · Lịch: {'bật' if status.get('schedule_enabled') else 'tạm dừng'}")
        if status.get("heartbeat"):
            st.caption(f"Heartbeat: {max(0, int(time.time()-status['heartbeat']))} giây trước. Worker độc lập UI; máy ngủ/tắt/mất mạng sẽ gián đoạn. Chưa tự khởi động cùng Windows.")
        if status.get("last_error"):
            st.error(status["last_error"])
        if running and not saved.get("owner_id"):
            st.info("Gửi /id@ten_bot trong nhóm. Dừng worker → nhập ID owner → lưu → khởi động lại → ghép bằng mã.")
        if running and not status.get("paired") and saved.get("owner_id") and (ROOT/"pair.dpapi").exists():
            try:
                pair = json.loads(load_protected(ROOT/"pair.dpapi"))
                if pair["owner_id"] == saved["owner_id"] and pair["expires"] >= time.time():
                    st.write("Owner gửi lệnh dưới đây trong đúng nhóm trong 10 phút:")
                    st.code(pair["command"], language=None)
                else:
                    st.warning("Mã chưa sẵn sàng/hết hạn; dừng và bật lại worker.")
            except Exception:
                st.info("Worker đang chuẩn bị mã; cập nhật trạng thái sau vài giây.")
        if status.get("paired"):
            st.success("Đã xác minh owner/nhóm. Trên Telegram: /onboard → nhập Excel/CSV → /plan → /enable mã.")
        st.download_button("Tải Excel DEMO để thử Telegram", sample_workbook_bytes(), "DEMO_AI_TEAM.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        st.caption("DEMO dùng dữ liệu giả. Gửi file kèm /import; app không tự chia sẻ nguồn Supra vào nhóm.")
        with st.expander("Lệnh và lịch báo cáo", expanded=True):
            st.text(MEMORY_HELP)
        with st.expander("Quản lý kho bí mật"):
            st.button("Xóa kho token/model DPAPI và dừng worker", key="telegram_forget_credentials", on_click=forget_credentials)
            st.caption("Không xóa bộ nhớ nghiệp vụ/file nguồn hoặc lịch sử Telegram. Dùng /forget để quên giá trị nghiệp vụ. Worker dừng gửi sau lượt đang xử lý.")
