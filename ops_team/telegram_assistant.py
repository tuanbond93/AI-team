"""Owner-controlled onboarding, sourced business memory and daily report plans."""
from copy import deepcopy
from dataclasses import fields
from datetime import datetime, timedelta
import hashlib
import json
import time
import threading
from zoneinfo import ZoneInfo
from .providers import ModelConfig
from .limits import RequestBudget, TelegramBudget, UsageWindow, ProviderError, retry_seconds
from .metrics import compute, reconcile
from .reporting import fmt
from .telegram_agent import TelegramAgent, warehouse_context
from .telegram_bridge import Incoming
from .telegram_state import REQUIRED, FIELDS, LABELS, GROUP_ID, contains_secret
from .telegram_sources import import_file, load_source, read_export_folder

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
MEMORY_HELP = """TRỢ LÝ AI TEAM — ONBOARDING VÀ TRÍ NHỚ NGHIỆP VỤ
/onboard — điền lần lượt thông tin còn thiếu
/set customer <phạm vi> — owner xác nhận thông tin (xem các trường ở /memory)
/memory — xem giá trị, nguồn, người xác nhận và phiên bản
/forget <trường|all> — xóa bộ nhớ nghiệp vụ và tạm dừng lịch
/source — xem nguồn đã nhận; gửi Excel/CSV kèm /import hoặc /import costs
/import chief — nhập workbook canonical tài chính; vẫn cần owner chọn source_id
/chief câu hỏi — thẩm định bốn phần + Auditor độc lập
/set source_id sheets — dùng Google Sheets đã cấp quyền chỉ đọc
/set source_id snapshot — dùng snapshot connector Google đã được cấp quyền, không chuyển cookie/token Google vào worker
/set source_id browser_snapshot — CSV xuất đúng tab bằng tài khoản Viewer; import xong mới chạy CLI gửi lịch
/set sheets_id <link/ID>; /set sheets_tab <tên>; /set sheets_gid <số>
/set sheets_ranges <JSON bảng → 'Tên tab'!A1:Z10000> — khóa range trong đúng tab
/set source_id <mã nguồn> — chọn file đã nhập làm nguồn báo cáo
/template — cấu trúc file cần gửi
/plan — xem kế hoạch báo cáo cụ thể và mã bật lịch
/enable <mã> — owner xác nhận bật đúng kế hoạch
/pause — tạm dừng lịch tự động
/run — owner yêu cầu báo cáo ngay từ nguồn mới nhất
/id — xem User ID của chính bạn
/status — trạng thái bot, lịch và AI
/details — owner xem chi tiết nguồn/kiểm tra còn trong RAM của phiên
/ask hoặc /cost, /operations, /fleet… — hỏi AI nếu đã cấu hình model
/vehicles hoặc /xe [YYYY-MM-DD] [YYYY-MM-DD] [Vn] — xem xe/chia sẻ từ paired source, 0 HTTP
/chiphi hoặc /costs [YYYY-MM-DD] [YYYY-MM-DD] — xem dự kiến chi phí theo nguồn đã duyệt
/sla [YYYY-MM-DD] [YYYY-MM-DD] — tỷ lệ giao đúng hạn D+1/D+2 theo Raw
/thieu [YYYY-MM-DD] [YYYY-MM-DD] — danh sách dữ liệu cần bổ sung: trường nào, ngày nào, dòng nào trong Sheet
/reset — xóa hội thoại RAM của bạn trong chủ đề

Thông tin ghi nhớ do owner cung cấp/xác nhận; chưa là dữ liệu đã đối soát độc lập. Bot không thực thi chi tiền, điều xe hay thay đổi nhân sự.
"""


class DurableBudget(TelegramBudget):
    def __init__(self, state):
        self.state = state
        saved = state.control("provider_usage", {})
        today = datetime.now(TZ).date().isoformat()
        same = saved.get("day") == today
        now, tick = time.time(), time.monotonic()
        window = UsageWindow([(tick-(now-t), n) for t, n in saved.get("window", []) if 0 <= now-t < 60])
        super().__init__(max_calls=60, max_tokens=120000, window=window)
        self.attempts = saved.get("attempts", 0) if same else 0
        self.reserved_tokens = saved.get("tokens", 0) if same else 0
        self.day = today
        cooldown = state.control("provider_cooldown", {}) or {}
        remaining = retry_seconds(cooldown.get("until", 0)-now) if isinstance(cooldown.get("until"), (int, float)) else None
        if cooldown.get("status") == 429 and remaining is not None and remaining > 0:
            self.halted, self.halt_status, self.retry_until = True, 429, tick+remaining
            self.retry_failures = min(5, max(1, int(cooldown.get("failures", 1))))
            self.retry_confirmed = cooldown.get("confirmed") is True

    def persist_cooldown(self):
        value = ({"status": 429, "until": time.time()+max(0, self.retry_until-self.clock()),
                  "failures": self.retry_failures, "confirmed": self.retry_confirmed}
                 if self.halt_status == 429 and self.retry_until is not None else None)
        self.state.set_control("provider_cooldown", value)

    def acquire(self, tokens, deadline):
        today = datetime.now(TZ).date().isoformat()
        if self.day != today:
            self.day, self.attempts, self.reserved_tokens = today, 0, 0
        super().acquire(tokens, deadline)
        tick, now = self.clock(), time.time()
        self.state.set_control("provider_usage", {"day": self.day, "attempts": self.attempts, "tokens": self.reserved_tokens,
            "window": [(now-(tick-t), n) for t, n in self.window.events]})


class AssistantResponder:
    def __init__(self, state, settings):
        self.state, self.settings = state, deepcopy(settings)
        self.owner = settings["owner_id"]
        self._drafts = {}
        self._agent = None
        self._report_details = None
        self._code_details = {}
        self._last_report_record = None
        self._report_origin = None
        self._trace_local = set()
        self.cancel = threading.Event()
        self._known = [settings.get("bot_token", "")]
        self._known += [c.get("api_key", "") for c in settings.get("models", {}).values()]
        self._known.append(settings.get("google_service_account", {}).get("private_key", ""))

    def safe_to_send(self, text, extra_secrets=()):
        return isinstance(text, str) and not contains_secret(text, [*self._known, *extra_secrets])

    def _facts(self):
        return {k: r["value"] for k, r in self.state.facts().items()}

    def _effective_facts(self):
        facts, assumptions = self._facts(), []
        try:
            booking = set(json.loads(facts.get("sheets_ranges", "{}"))) == {"supra_booking"}
        except ValueError:
            booking = False
        if booking:
            defaults = {"period": datetime.now(TZ).strftime("%Y-%m"), "report_time": "13:00",
                        "timezone": "Asia/Ho_Chi_Minh", "warehouses": facts.get("sheets_tab", "")}
            defaults["cutoff"] = facts.get("report_time") or defaults["report_time"]
            for key, value in defaults.items():
                if not facts.get(key) and value:
                    facts[key] = value
                    assumptions.append(f"period=CURRENT_MONTH (tháng hiện tại, đang {value})" if key == "period" else f"{key}={value}")
        return facts, assumptions, booking

    def _onboard(self):
        facts = self._facts()
        missing = [k for k in REQUIRED if not facts.get(k)]
        if not missing:
            return "Đã có các thông tin cơ bản do owner xác nhận. Tiếp theo: gửi nguồn Excel/CSV, chọn source_id rồi /plan."
        key = missing[0]
        self.state.set_control("pending_field", key)
        return (f"Còn thiếu {len(missing)} mục. Mời owner cung cấp: {LABELS[key]}.\n"
                f"Dùng /set {key} <giá trị> để xác nhận, hoặc trả lời tin này để xem lại trước khi /confirm.")

    def answer(self, request):
        command, text = request.command, request.text
        owner = request.user_id == self.owner
        if not self.safe_to_send(text):
            return "Nội dung có dấu hiệu chứa bí mật; không lưu hoặc gửi tới model."
        if command in ("help", "start"):
            return MEMORY_HELP+"\n"+self._onboard()
        if command == "id":
            return f"User ID của bạn: {request.user_id}. Nhóm: {request.chat_id}."
        if command == "status":
            ai = "AI đã cấu hình; chỉ gọi khi bạn hỏi hoặc lịch đã duyệt" if self.settings.get("models") else "CHƯA CÓ AI/MODEL — onboarding và báo cáo số liệu bằng code vẫn dùng được"
            return f"Nhóm cố định: {GROUP_ID}. Owner ID: {self.owner}.\n{ai}\nLịch: {'ĐANG BẬT' if self.state.control('schedule_enabled', False) else 'TẠM DỪNG'}.\nNguồn được đọc mới mỗi lần báo cáo. Máy ngủ/tắt hoặc mất mạng thì worker không hoạt động."
        owner_commands = {"set", "forget", "onboard", "confirm", "plan", "enable", "pause", "stop", "run", "import", "details", "chiphi", "costs", "thieu", "missing", "sla"}
        if command in owner_commands and not owner:
            if command == "details":
                return "Chỉ chủ sở hữu đã xác minh được xem chi tiết truy vết."
            return "Chỉ owner đã xác minh được sửa bộ nhớ, nguồn hoặc lịch."
        try:
            if command == "details":
                trace_key = (request.chat_id, request.topic_id, request.user_id)
                code_record = self._code_details.get(trace_key)
                if code_record:
                    if code_record.get('kind')=='manual_cost':
                        from .supra.manual_cost_report import render_cost_report
                        mode=text.strip().casefold()
                        if mode not in ('','audit'):return 'Dùng /details hoặc /details audit.'
                        return render_cost_report(code_record['report'],details=True,audit=mode=='audit',answered_text=code_record['response'])
                    if code_record.get('kind')=='vehicles':
                        from .supra.vehicle_report import vehicle_view
                        mode=text.strip().casefold()
                        if mode not in ('','audit'):return 'Dùng /details để xem cách bổ sung, hoặc /details audit để xem truy vết kỹ thuật.'
                        return vehicle_view(code_record['evidence'],code_record['filter'],details=True,audit=mode=='audit',
                            source_pin=code_record.get('source_pin'),answered_text=code_record.get('response'),
                            scope_quality=code_record.get('scope_quality'),cost_record=code_record.get('cost_record'),
                            forecast_record=code_record.get('forecast_record'))
                    return TelegramAgent.detail_text(code_record)
                if self._agent and self._agent._records.get((request.chat_id, request.topic_id, request.user_id)):
                    return self._agent.details(request)
                if trace_key in self._trace_local:
                    return "Em chưa giữ chi tiết câu trả lời trong chủ đề này. Anh có thể gửi yêu cầu mới để tạo bản thẩm định hiện tại."
                return self._report_details or "Em chưa giữ chi tiết kiểm tra trong RAM của phiên này. Tin gửi lịch từ tiến trình khác không có audit ở đây; anh có thể dùng /run để tạo bản cập nhật trong cuộc trò chuyện."
            if command == "reset":
                key = (request.chat_id, request.topic_id, request.user_id)
                self._trace_local.add(key)
                removed = self._code_details.pop(key, None)
                if self._agent:
                    prior = self._agent._records.pop(key, None)
                    self._agent._history.pop(key, None)
                    if self._agent.last_presentation is prior:
                        self._agent.last_presentation = None
                    removed = removed or prior
                if self._report_origin == key or (self._report_origin is None and owner and request.topic_id is None) or self._last_report_record is removed:
                    self._report_details = self._last_report_record = None
                    self._report_origin = None
                return "Đã xóa ngữ cảnh và chi tiết thẩm định của bạn trong chủ đề này khỏi RAM. Tin đã gửi trên Telegram vẫn ở Telegram."
            if command == "onboard":
                return self._onboard()
            if command == "set":
                key, _, value = text.partition(" ")
                if not self.safe_to_send(value):
                    return "Không lưu bí mật vào bộ nhớ nghiệp vụ."
                if key == "source_id" and not self.state.source(value.strip()) and value.strip() not in ("folder", "sheets", "snapshot", "browser_snapshot"):
                    return "Mã nguồn chưa tồn tại. Gửi file trước rồi dùng /source."
                if key == "source_id" and value.strip() == "folder" and not self.settings.get("allowed_folder"):
                    return "Owner cần cho phép thư mục export tại giao diện local trước."
                self.state.remember(key, value, request.user_id, request.message_id, request.chat_id)
                self._report_details = None
                self._code_details.clear()
                self._last_report_record = self._report_origin = None
                if self._agent:
                    self._agent.clear()
                    self._agent = None
                self.state.set_control("pending_field", None)
                return f"Đã ghi {key}, nguồn tin #{request.message_id}, owner {self.owner} xác nhận; chưa đối soát độc lập. Lịch được tạm dừng khi cấu hình đổi.\n"+self._onboard()
            if command == "memory":
                facts = self.state.facts()
                lines = ["BỘ NHỚ NGHIỆP VỤ — OWNER CUNG CẤP/XÁC NHẬN; CHƯA ĐỐI SOÁT ĐỘC LẬP"]
                for key in FIELDS:
                    if key in facts:
                        row = facts[key]
                        origin = "chủ máy cấu hình tại local" if row["source_kind"] == "LOCAL_OWNER_SETUP" else f"nhóm {row['chat_id']}; tin #{row['message_id']}"
                        lines.append(f"{key} = {row['value']} [v{row['version']}; user {row['actor']}; {origin}]")
                lines.append("Còn thiếu: "+", ".join(k for k in REQUIRED if k not in facts))
                lines.append("Sửa: /set tên_trường giá_trị. Quên: /forget tên_trường hoặc /forget all.")
                return "\n".join(lines)
            if command == "forget":
                self.state.forget(text.strip(), request.user_id, request.message_id)
                self._drafts.clear()
                self._report_details = None
                self._code_details.clear()
                self._last_report_record = self._report_origin = None
                if self._agent:
                    self._agent.clear()
                    self._agent = None
                return "Đã xóa giá trị và lịch sử giá trị trong bộ nhớ nghiệp vụ; giữ audit hành động quên. Lịch đã tạm dừng. File nguồn DPAPI và tin/file đã gửi trên Telegram không bị xóa bởi /forget."
            if command == "confirm":
                draft = self._drafts.pop(request.user_id, None)
                if not draft:
                    return "Chưa có bản nháp RAM để xác nhận. Dùng /onboard hoặc /set."
                key, value, original_id = draft
                self.state.remember(key, value, request.user_id, original_id, request.chat_id, request.message_id)
                self.state.set_control("pending_field", None)
                return f"Đã xác nhận {key} từ tin #{original_id}.\n"+self._onboard()
            if command == "source":
                rows = self.state.source()
                return ("NGUỒN ĐÃ NHẬN — chưa tự chứng nhận Finance\n"+
                    "\n".join(f"{r['id']} · {r['name']} · SHA256 {r['digest']} · người gửi {r['actor']} / tin #{r['message_id']}" for r in rows)+
                    "\nChọn: /set source_id mã_nguồn. Thư mục local được phép: "+("có; /set source_id folder" if self.settings.get("allowed_folder") else "chưa cấu hình")+
                    "\nGoogle: source_id sheets dùng credential read-only đã nhập local; source_id snapshot dùng lượt đọc connector đã cấp quyền. Snapshot local: "+
                    ("đã có; vẫn phải khớp file/tab/gid/range đã khóa" if (self.state.root/"snapshot-latest.dpapi").exists() else "chưa có") )
            if command == "template":
                from .schema import SPECS
                return "XLSX: tên sheet và cột đúng hợp đồng dưới đây. CSV: UTF-8, gửi kèm /import tên_bảng. Không tự quy đổi đơn vị/đoán mapping.\n"+"\n".join(k+": "+", ".join(s.columns) for k, s in SPECS.items())
            if command == "plan":
                text, code, missing = self.plan()
                return text+("\nChưa bật: bổ sung phần còn thiếu." if missing else "\nBật đúng kế hoạch: /enable "+code)
            if command == "enable":
                plan, code, missing = self.plan()
                if missing or text.strip() != code:
                    return "Kế hoạch đã đổi, chưa đủ dữ liệu hoặc mã chưa đúng. Dùng /plan và kiểm tra lại."
                self.state.set_control("approved_plan", code)
                self.state.set_control("schedule_enabled", True)
                return "Đã bật lịch theo kế hoạch vừa xác nhận. Worker chỉ chạy khi máy bật và có mạng; không gửi bù nhiều kỳ bị lỡ."
            if command in ("pause", "stop"):
                self.state.set_control("schedule_enabled", False)
                return "Đã tạm dừng lịch báo cáo. Bot vẫn nhận lệnh; dừng worker hoàn toàn bằng nút Dừng worker trong app."
            if command == "run":
                key = (request.chat_id, request.topic_id, request.user_id)
                self._trace_local.add(key)
                self._code_details.pop(key, None)
                if self._agent:
                    self._agent._records.pop(key, None)
                text = self.report()
                if self._last_report_record:
                    self._code_details[key] = self._last_report_record
                    self._report_origin = key
                return text
            if command in ('chiphi','costs','thieu','missing'):
                from .supra.manual_cost_report import manual_cost_report,render_cost_report
                from .supra.booking import BookingData
                key=(request.chat_id,request.topic_id,request.user_id)
                self._trace_local.add(key);self._code_details.pop(key,None)
                if self._agent:self._agent._records.pop(key,None)
                data,source,facts,period,warehouses,cutoff,_=self._scoped()
                if not isinstance(data,BookingData):return 'Chi phí: chưa có paired booking được duyệt.'
                evidence=self._vehicle_evidence(data,period,cutoff)
                missing=command in ('thieu','missing')
                report=manual_cost_report(self.state,data,evidence,source,facts,self.owner,text) if evidence.get('available') else {'available':False}
                if missing:
                    from .supra.missing_data import missing_checklist,booking_checklist
                    if report.get('available'):return missing_checklist(report)[1]
                    if report.get('reason') in (None,'NOT_CONFIGURED'):
                        return booking_checklist(self._booking_context(data,period,warehouses,cutoff,source))[1]
                    return {'RANGE_REQUIRED':'Chưa lập được danh sách: dùng /thieu YYYY-MM-DD YYYY-MM-DD trong kỳ đã chọn.',
                        'OUTSIDE_POLICY_WINDOW':'Chưa lập được danh sách: khoảng ngày nằm ngoài chính sách chi phí đã duyệt.'}.get(report['reason'],
                        'Chưa lập được danh sách: chính sách chi phí đã cài không còn khớp nguồn hiện tại; cần nạp lại cho đúng bản nguồn.')
                if not evidence.get('available'):return 'Chi phí: chưa đủ hai nguồn đã duyệt.'
                result=render_cost_report(report)
                self._code_details[key]={'kind':'manual_cost','report':report,'response':result}
                if len(self._code_details)>100:self._code_details.pop(next(iter(self._code_details)))
                return result
            if command=='sla':
                from .supra.booking import BookingData
                from .supra.sla import sla_summary,render_sla
                from .supra.vehicle_report import vehicle_scope
                data,source,facts,period,warehouses,cutoff,_=self._scoped()
                if not isinstance(data,BookingData) or not getattr(data,'vehicle_pair',None):return 'SLA: N/A — cần paired Booking + Raw đã duyệt.'
                evidence=self._vehicle_evidence(data,period,cutoff)
                if not evidence.get('available'):return 'SLA: N/A — chưa đủ hai nguồn đã duyệt.'
                scope=vehicle_scope(evidence,text)
                if not scope['start'] or not scope['end']:return 'SLA: chưa xác định khoảng ngày; dùng /sla YYYY-MM-DD YYYY-MM-DD.'
                return render_sla(sla_summary(data,scope['start'],scope['end'],datetime.now(TZ)),scope['start'],scope['end'])
            if command in ('xe','vehicles'):
                from .supra.vehicle_report import vehicle_view,vehicle_scope_quality
                from .supra.vehicle_cost import vehicle_cost_status
                from .telegram_vehicle_forecast import manual_forecast
                key=(request.chat_id,request.topic_id,request.user_id)
                self._trace_local.add(key)
                self._code_details.pop(key,None)
                if self._agent:self._agent._records.pop(key,None)
                data,source,facts,period,warehouses,cutoff,_=self._scoped()
                from .supra.booking import BookingData
                if not isinstance(data,BookingData):return 'Xe: N/A — nguồn hiện tại không phải paired booking.'
                evidence=self._vehicle_evidence(data,period,cutoff)
                quality=vehicle_scope_quality(data,evidence,text)
                from .telegram_rule_layer import load_rules
                rules,rules_sha=load_rules(self.state,data,source,facts,self.owner) if evidence.get('available') else (None,None)
                if rules:
                    from .supra.vehicle_report import vehicle_scope
                    from .supra.vehicle_cost import vehicle_cost_by_day
                    scope=vehicle_scope(evidence,text)
                    if not scope['start'] or not scope['end'] or not rules['window']['start']<=scope['start']<=scope['end']<=rules['window']['end']:
                        return 'Quy tắc đã duyệt không áp dụng cho khoảng ngày này; chưa tính.'
                    from .telegram_secrets import load_protected
                    from .telegram_manual_cost_policy import FILENAME as COST_POLICY_FILENAME
                    cost_policy=json.loads(load_protected(self.state.root/COST_POLICY_FILENAME))
                    cost=vehicle_cost_by_day(data,evidence,text,rules,cost_policy);cost['rules_sha256']=rules_sha
                    legacy=vehicle_cost_status(data,evidence,text)
                    cost['legacy_warnings']={'counters':legacy.get('counters',{}),'issue_sources':legacy.get('issue_sources',[])}
                else:cost=vehicle_cost_status(data,evidence,text)
                forecast=manual_forecast(self.state,data,evidence,source,facts,self.owner,text,rules=rules) if evidence.get('available') else None
                result=vehicle_view(evidence,text,scope_quality=quality,cost_record=cost,forecast_record=forecast)
                raw=data.vehicle_pair['data_supra'] if data.vehicle_pair else None
                self._code_details[key]={'kind':'vehicles','evidence':evidence,'filter':text,'response':result,'scope_quality':quality,'cost_record':cost,'forecast_record':forecast,
                    'source_pin':{k:raw[k] for k in ('spreadsheet_id','tab','gid')} if raw else None}
                if len(self._code_details)>100:self._code_details.pop(next(iter(self._code_details)))
                return result
            if command == "import":
                return "Gửi file .xlsx/.csv kèm caption /import, hoặc /import costs cho CSV chi phí. Không nhận file thực thi hoặc macro."
            pending = self.state.control("pending_field")
            if command == "ask" and owner and pending and not self._facts().get("source_id"):
                from .telegram_state import validate_fact
                value = validate_fact(pending, text)
                self._drafts[request.user_id] = (pending, value, request.message_id)
                return f"Xem lại {pending}: {value}\nĐây là thông tin bạn cung cấp, chưa đối soát độc lập. Gửi /confirm để lưu, hoặc /set {pending} <giá trị khác>."
            from .chief.agent import is_evaluation, finance_available
            chief_request = command == "chief" or (command == "ask" and is_evaluation(text) and bool(self._facts().get("source_id")))
            key = (request.chat_id, request.topic_id, request.user_id)
            self._trace_local.add(key)
            self._code_details.pop(key, None)
            prepared = self._chief_source() if chief_request else None
            if prepared and (not finance_available(prepared[0]) or not self.settings.get("models")):
                return self._source_code_report(request, prepared)
            if not self.settings.get("models"):
                return "CHƯA CÓ AI/MODEL. Tôi đang dùng quy trình onboarding và tính toán bằng code. Dùng /onboard, /memory, /source hoặc /plan; chủ máy có thể cấu hình model trong AI Team."
            try:
                agent = self._get_agent(fleet=True) if command=='fleet' else self._get_agent()
            except (ValueError, ProviderError):
                if prepared:
                    return self._source_code_report(request, prepared, "Cấu hình AI chưa sẵn sàng; Chief AI/Auditor chưa chạy cho yêu cầu này (0 HTTP).")
                raise
            agent._records.pop(key, None)
            return agent.answer(request)
        except (ValueError, ProviderError) as exc:
            return "Chưa hoàn tất: "+str(exc)
        except Exception:
            return "Chưa hoàn tất xử lý. Kiểm tra nguồn/cấu hình ở AI Team; không lưu nội dung lỗi thô."

    def document(self, request, document, api):
        if request.user_id != self.owner:
            return "Chỉ owner được nhập hoặc thay nguồn dữ liệu."
        try:
            name, size = document.get("file_name", ""), document.get("file_size", 0)
            if not self.safe_to_send(name):
                return "Tên file chứa nội dung riêng; đã chặn nhập."
            if not isinstance(size, int) or size <= 0 or size > 20*1024*1024 or not name.lower().endswith((".xlsx", ".csv")):
                return "Chỉ nhận .xlsx hoặc .csv tối đa 20 MB."
            info = api.call("getFile", file_id=document["file_id"])
            data = api.download(info["file_path"])
            facts = self._facts()
            identifier, checked = import_file(self.state, request, name, data, request.text.strip(),
                json.loads(facts.get("mapping", "{}")) or None, facts.get("numeric_format", "machine"))
            from .chief.ingestion import FinancialData
            if isinstance(checked, FinancialData):
                from .chief.engine import evaluate
                result = evaluate(checked.tables)
                return (f"Đã nhận nguồn tài chính mã hóa DPAPI: {identifier}; {sum(checked.raw_counts.values())} dòng nguồn, {len(result['quarantined_sources'])} tham chiếu cách ly. "
                    "Chưa đối soát; thiếu dữ liệu sẽ hiện PARTIAL/N/A. Chưa đổi nguồn hoặc bật lịch. "
                    f"Chủ sở hữu chọn bằng /set source_id {identifier}, rồi xem /plan.")
            missing = [k for k, n in checked.raw_counts.items() if not n]
            return (f"Đã kiểm tra và lưu nguồn mã hóa DPAPI: {identifier}. Dòng hợp lệ: {sum(len(df) for df in checked.tables.values())}. "
                    f"Bảng còn thiếu: {', '.join(missing) or 'không'}. Chưa đối soát Finance.\n"
                    f"Owner chọn chia sẻ nguồn này cho báo cáo/model bằng /set source_id {identifier}, rồi /plan.")
        except (ValueError, KeyError):
            return "File chưa qua kiểm tra định dạng, cột, đơn vị hoặc dữ liệu. Nguồn cũ được giữ. Dùng /template và đối chiếu Excel mẫu trong app."

    def _load(self):
        facts = self._facts()
        if facts.get("source_id") in ("snapshot", "browser_snapshot"):
            from .telegram_snapshot import load_snapshot
            data, source = load_snapshot(self.state, facts)
            if facts["source_id"] == "browser_snapshot" and source["name"] != "GOOGLE_BROWSER_VIEWER_SNAPSHOT":
                raise ValueError("Nguồn chưa phải CSV browser Viewer export đã khóa.")
            return data, source
        if facts.get("source_id") == "sheets":
            from .telegram_sheets import read_sheets
            return read_sheets(facts, self.settings.get("google_service_account", {}),state=self.state)
        if facts.get("source_id") == "folder":
            return read_export_folder(self.settings.get("allowed_folder", ""), facts)
        if not facts.get("source_id"):
            raise ValueError("Chưa có nguồn được owner chọn. Gửi Excel/CSV và xác nhận source_id.")
        return load_source(self.state, facts["source_id"], facts)

    def plan(self):
        from .supra.booking import BookingData, booking_context
        from .chief.ingestion import FinancialData
        facts, assumptions, booking_source = self._effective_facts()
        required = ("period", "warehouses", "cutoff", "report_time", "timezone") if booking_source else REQUIRED
        missing = [k for k in required if not facts.get(k)]
        data_gaps = []
        if not facts.get("source_id"):
            missing.append("source_id")
        else:
            try:
                validation, source_record = self._load()
                if facts.get("source_id") in ("snapshot", "browser_snapshot") and time.time()-source_record["created"] > 900:
                    missing.append("snapshot Google connector cần lượt đọc mới trong 15 phút")
                if 'vehicle_raw_created' in source_record and time.time()-source_record['vehicle_raw_created']>900:
                    missing.append("paired Raw cần lượt đọc mới trong 15 phút; refresh Booking không làm Raw mới")
                booking = isinstance(validation, BookingData)
                financial = isinstance(validation, FinancialData)
                data_gaps = (["Chief Auditor: các phần thiếu/cách ly sẽ là PARTIAL, không điền 0"] if financial else
                    ["booking: chưa có khóa trip/drop, quy tắc PCS/lot/xe, chi phí, OT, SLA"] if booking else [i["table"] for i in validation.issues if i["code"] == "MISSING_TABLE"])
                if not booking and not financial and any(i["code"] != "MISSING_TABLE" for i in validation.issues):
                    missing.append("nguồn còn dòng lỗi/cách ly")
                if facts.get("period") and facts.get("warehouses"):
                    scoped, source, _, period, warehouses, cutoff, _ = self._scoped()
                    observed = ({"rows": len(self._financial_result(scoped, period, warehouses, cutoff)["orders"]), "missing_warehouses": []} if financial else
                        {"rows": booking_context(scoped, period, warehouses, cutoff, source)["rows"], "missing_warehouses": []} if booking else self._observed(scoped, period, warehouses))
                    if not observed["rows"]:
                        missing.append("không có dòng nghiệp vụ trong kỳ/kho/cutoff đã chọn")
                    if observed["missing_warehouses"]:
                        missing.append("kho chưa có dòng: "+", ".join(observed["missing_warehouses"]))
            except Exception:
                missing.append("nguồn chưa đọc/kiểm tra được")
        code = self._plan_code(facts, assumptions)
        source_name = {"browser_snapshot": "CSV Google Sheets xuất bằng quyền Viewer", "snapshot": "Bản đọc Google được cấp quyền",
                       "sheets": "Google Sheets chỉ đọc", "folder": "Thư mục export đã chọn"}.get(facts.get("source_id"), "File đã chọn")
        model_names = ", ".join(sorted({c.get("provider", "")+"/"+c.get("model", "") for c in self.settings.get("models", {}).values()}))
        lines = ["KẾ HOẠCH BÁO CÁO CHỜ ANH DUYỆT", f"Nguồn: {source_name}; tab {facts.get('sheets_tab', 'theo file')}; phạm vi {facts.get('warehouses', 'chưa có')}.",
            (f"Kỳ tháng hiện tại (đang {facts.get('period', 'chưa có')})" if "period" not in self._facts() else f"Kỳ cố định {facts.get('period', 'chưa có')}")+", theo Ngày xuất nguồn, không phải ngày hoàn tất thực tế." if booking_source else f"Kỳ {facts.get('period', 'chưa có')}; phạm vi {facts.get('customer', 'chưa có')}.",
            f"Gửi vào nhóm này mỗi ngày {facts.get('report_time', 'chưa có')} giờ Việt Nam; chốt dữ liệu {facts.get('cutoff', 'chưa có')}."]
        if facts.get("sheets_id"):
            lines.append(f"File: https://docs.google.com/spreadsheets/d/{facts['sheets_id']}/edit#gid={facts.get('sheets_gid', '')}")
        lines.append("Tính được: dòng booking, mã GHN khác nhau, dòng có mã lặp/thiếu, trạng thái mâu thuẫn/nhất quán và ngày dữ liệu; mã lặp chưa chứng minh lỗi, delivered chưa phải SLA." if booking_source else "Tính chỉ số đã đủ dữ liệu; cảnh báo thiếu/cũ/lỗi, không điền 0 thay thiếu.")
        if facts.get("source_id") == "browser_snapshot":
            lines.append("Phải xuất lại đúng tab và kiểm tra bản mới trong 15 phút trước khi gửi; cần lịch đọc/xuất đã cấu hình. Không gửi trước từ bản cũ.")
        lines.append("AI: "+(model_names+"; booking Free Pilot tối đa 1.500 token đầu ra/lần." if model_names else "chưa cấu hình; chỉ tính bằng code."))
        lines.append("Ngân sách worker: tối đa 60 lượt gọi và 120.000 token dự phòng/ngày, 7.500 token/phút; không bảo đảm quota/hóa đơn ngoài worker.")
        if data_gaps:
            lines.append("Chưa tính/thiếu: "+"; ".join(data_gaps))
        if booking_source:
            lines.append("Checklist bổ sung, không chặn booking cơ bản: "+(", ".join(LABELS[k] for k in ("customer", "goals", "contacts", "kpi_formulas") if not facts.get(k)) or "đã khai báo; vẫn cần đối soát"))
        lines.append("GIẢ ĐỊNH CHỜ OWNER /enable XÁC NHẬN: "+("; ".join(assumptions) or "không"))
        lines.append("Còn thiếu để bật: "+(", ".join(missing) or "không; nguồn vẫn chưa được đối soát độc lập"))
        text = "\n".join(lines)
        return text, code, missing

    def _plan_code(self, facts=None, assumptions=None):
        if facts is None:
            facts, assumptions, _ = self._effective_facts()
        facts, assumptions = dict(facts), list(assumptions)
        if "period" not in self._facts() and any(a.startswith("period=CURRENT_MONTH") for a in assumptions):
            facts["period"] = "CURRENT_MONTH"
            assumptions = ["period=CURRENT_MONTH" if a.startswith("period=CURRENT_MONTH") else a for a in assumptions]
        descriptor = {"group": GROUP_ID, "owner": self.owner, "facts": facts, "assumptions": assumptions,
            "worker_preset": "booking-free-1500/source-catalog-v1" if any(c.get("free_pilot") for c in self.settings.get("models", {}).values()) else "configured-output-limit",
            "folder": self.settings.get("allowed_folder", "") if facts.get("source_id") == "folder" else "",
            "model_profiles": {n: {k: c.get(k) for k in ("provider", "model", "structured_output", "max_output_tokens")} for n, c in self.settings.get("models", {}).items()}}
        if facts.get('source_id') in ('snapshot','browser_snapshot'):
            from .telegram_vehicle_snapshot import vehicle_plan_identity
            identity=vehicle_plan_identity(self.state)
            if identity is not None:descriptor['vehicle_source_approval']=identity
        return hashlib.sha256(json.dumps(descriptor, sort_keys=True).encode()).hexdigest()[:12]

    def _scoped(self):
        import pandas as pd
        validation, source = self._load()
        facts, _, _ = self._effective_facts()
        period = facts.get("period")
        warehouses = [s.strip() for s in facts.get("warehouses", "").split(",") if s.strip()]
        if not period or not warehouses:
            raise ValueError("Còn thiếu period hoặc warehouses; dùng /onboard.")
        now = datetime.now(TZ)
        hour, minute = map(int, facts.get("cutoff", "23:59").split(":"))
        cutoff = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if cutoff > now:
            cutoff -= timedelta(days=1)
        from .supra.booking import BookingData
        from .chief.ingestion import FinancialData
        if isinstance(validation, (BookingData, FinancialData)):
            return validation, source, facts, period, warehouses, cutoff, 0
        validation = deepcopy(validation)
        removed = 0
        for kind, frame in validation.tables.items():
            date_column = next((c for c in ("service_date", "date") if c in frame), None)
            if date_column and not frame.empty:
                dates = pd.to_datetime(frame[date_column], errors="coerce")
                keep = dates.dt.date <= cutoff.date()
                removed += int((~keep).sum())
                validation.tables[kind] = frame.loc[keep].copy()
        return validation, source, facts, period, warehouses, cutoff, removed

    def _observed(self, validation, period, warehouses):
        import pandas as pd
        dates, rows, present, by_table = [], 0, set(), {}
        for kind in ("costs", "operations", "workforce", "fleet"):
            frame = validation.tables[kind]
            scoped = frame[frame.period.eq(period) & frame.warehouse.isin(warehouses)]
            rows += len(scoped)
            present.update(scoped.warehouse.dropna().tolist())
            date_column = "service_date" if kind == "costs" else "date"
            if date_column in scoped:
                table_dates = [d.date() for d in pd.to_datetime(scoped[date_column], errors="coerce").dropna()]
                dates += table_dates
                by_table[kind] = {"rows": len(scoped), "latest": max(table_dates) if table_dates else None}
        return {"rows": rows, "earliest": min(dates) if dates else None, "latest": max(dates) if dates else None,
                "by_table": by_table, "missing_warehouses": sorted(set(warehouses)-present)}

    def _is_demo(self, validation, source):
        return "demo" in source.get("name", "").casefold() or any(
            frame[col].astype(str).str.startswith("DEMO-").any()
            for frame in validation.tables.values() for col in ("record_id", "trip_id") if col in frame)

    def report(self):
        self._report_details = None
        self._last_report_record = None
        self._report_origin = None
        try:
            validation, source, facts, period, warehouses, cutoff, removed = self._scoped()
            from .supra.booking import BookingData
            from .chief.ingestion import FinancialData
            if isinstance(validation, FinancialData):
                return self._financial_report(validation, source, period, warehouses, cutoff)
            if isinstance(validation, BookingData):
                return self._booking_report(validation, source, facts, period, warehouses, cutoff)
            observed = self._observed(validation, period, warehouses)
            if not observed["rows"]:
                return "CHƯA CÓ BÁO CÁO: không có dòng nghiệp vụ hợp lệ trong kỳ/kho/cutoff đã chọn. Kiểm tra /memory và /source; không gọi AI."
            if observed["missing_warehouses"]:
                return "CHƯA CÓ BÁO CÁO ĐỦ PHẠM VI: kho chưa có dòng nghiệp vụ: "+", ".join(observed["missing_warehouses"])+". Không coi phần thiếu là 0; không gọi AI."
            metrics = compute(validation, period, warehouses)
            lines = ["BÁO CÁO AI TEAM — SỐ NGUỒN CHƯA ĐỐI SOÁT ĐỘC LẬP",
                f"{facts.get('customer', 'chưa xác nhận phạm vi khách hàng')} · {period} · {', '.join(warehouses)}",
                f"Cutoff: {cutoff.isoformat(timespec='minutes')}; lũy kế kỳ, loại {removed} dòng sau ngày cutoff.",
                f"Nguồn SHA256: {source['digest']}"]
            if self._is_demo(validation, source):
                lines.insert(0, "DỮ LIỆU DEMO — KHÔNG DÙNG QUYẾT ĐỊNH THẬT")
            lines.append("Khách hàng là nhãn owner cung cấp; canonical chưa có customer_id, chưa xác minh nguồn không lẫn khách hàng khác.")
            lines.append(f"Ngày quan sát trong phạm vi: {observed['earliest']} → {observed['latest']}; {observed['rows']} dòng nghiệp vụ.")
            for table, item in observed["by_table"].items():
                stale = item["latest"] is None or (cutoff.date()-item["latest"]).days > 1
                lines.append(f"{table}: {item['rows']} dòng; ngày mới nhất {item['latest']}"+(" — THIẾU/CŨ" if stale else ""))
            missing = [i for i in validation.issues if i["code"] == "MISSING_TABLE"]
            blocked = [i for i in validation.issues if i["code"] != "MISSING_TABLE"]
            if blocked:
                return "CHƯA CÓ BÁO CÁO HỢP LỆ: nguồn có dòng lỗi/cách ly. Kiểm tra /source và /template; không gọi AI."
            if time.time()-source["created"] > 86400:
                lines.append("CẢNH BÁO: file nguồn không được cập nhật trong 24 giờ; đây là ảnh chụp cũ.")
            if observed["latest"] is None or (cutoff.date()-observed["latest"]).days > 1:
                lines.append("CẢNH BÁO DỮ LIỆU CŨ: ngày nghiệp vụ mới nhất thiếu hoặc cách ngày cutoff trên 1 ngày; upload file mới không làm dữ liệu cũ thành dữ liệu mới.")
            lines.append("Giờ cập nhật/cutoff chưa được xác minh từ nguồn chỉ có ngày; không coi ngày upload là ngày phát sinh.")
            if missing:
                lines.append("Thiếu bảng: "+", ".join(i["table"] for i in missing))
            for key in ("cost", "budget", "volume", "cost_per_unit", "sla", "ot_rate", "load_weight", "empty_rate"):
                metric = metrics[key]
                lines.append(f"{metric.label}: {fmt(metric.value, metric.unit)} · {metric.status}")
            mismatches = [r for r in reconcile(validation, period, warehouses) if r.get("difference") not in (0, None)]
            lines.append(f"Đối soát theo code: {len(mismatches)} lệch cần kiểm tra; không thay nghiệm thu Finance.")
            lines.append("Mục tiêu owner cung cấp: "+facts.get("goals", "chưa có")+". Đầu mối: "+facts.get("contacts", "chưa có"))
            if self.settings.get("models") and any(m.value is not None for m in metrics.values()):
                agent = self._get_agent()
                extra = agent.answer(Incoming(GROUP_ID, self.owner, 0, None, "team", "Phân tích báo cáo kỳ theo nguồn đã chọn, nêu thiếu dữ liệu, rủi ro và hành động cần quản lý duyệt."))
                lines.append(extra)
            else:
                lines.append("CHƯA CÓ DIỄN GIẢI AI. Số liệu trên tính bằng code; chưa được Auditor model xét.")
            return "\n".join(lines)
        except Exception:
            return "CHƯA CÓ BÁO CÁO: nguồn/kỳ/kho chưa đầy đủ hoặc chưa đọc được. Dùng /onboard → /source → /plan. Không dựng số liệu thay thế."

    def _get_agent(self,*,fleet=False):
        configs = {n: ModelConfig(**c) for n, c in self.settings.get("models", {}).items()}
        context = None
        if self._facts().get("source_id"):
            validation, source, facts, period, warehouses, cutoff, _ = self._scoped()
            from .supra.booking import BookingData, booking_context
            from .chief.ingestion import FinancialData
            signature = hashlib.sha256((json.dumps(facts, sort_keys=True)+cutoff.isoformat()).encode()).hexdigest()[:12]
            if isinstance(validation, FinancialData):
                from .chief.agent import metric_context
                result = self._financial_result(validation, period, warehouses, cutoff)
                context = {"label": validation.label+"; SHA256 "+source["digest"]+"; phạm vi "+signature,
                    "scope": result["scope"], "metrics": metric_context(result), "chief_result": result}
            elif isinstance(validation, BookingData):
                context = self._booking_context(validation, period, warehouses, cutoff, source)
                if not context["rows"]:
                    raise ValueError("Chưa có booking trong kỳ/cutoff đã chọn.")
                context["label"] += "; phạm vi "+signature
                if fleet and validation.vehicle_pair:
                    from .supra.vehicle_report import fleet_context
                    context=fleet_context(self._vehicle_evidence(validation,period,cutoff))
                    context['label']+='; phạm vi '+signature
            else:
                if any(i["code"] != "MISSING_TABLE" for i in validation.issues):
                    raise ValueError("Nguồn còn lỗi/cách ly; chưa gửi số liệu tới model.")
                context = warehouse_context(validation, period, None, warehouses, None,
                    ("DỮ LIỆU DEMO — KHÔNG DÙNG QUYẾT ĐỊNH THẬT; " if self._is_demo(validation, source) else "OWNER PROVIDED — CHƯA ĐỐI SOÁT ĐỘC LẬP; ")+
                    "SHA256 "+source["digest"]+"; phạm vi "+signature)
            context["business_facts"] = {k: facts[k] for k in ("customer", "goals", "kpi_formulas") if k in facts}
        label = context["label"] if context else "no-source"
        if context and context.get('vehicle'):label+='; pair '+json.dumps(context['cache_identity'])
        if context and context.get('handling_quality',{}).get('available'):
            label += '; handling-catalog '+context['handling_quality']['catalog_sha256']
            if context['handling_quality'].get('grain')=='GHN_BOOKING_E_DAY':
                label += '; owner-rules '+str(context['handling_quality'].get('rules_sha256'))+' '+context['handling_quality']['reason']
        if self._agent is None or getattr(self, "_agent_label", None) != label:
            if self._agent:
                self._agent.clear()
            self._agent = TelegramAgent(configs, context, max_questions=1000000)
            self._agent._budget = DurableBudget(self.state)
            self._agent._budget.sleep = self._agent._wait
            self._agent_label = label
        self._agent.cancel = self.cancel
        if context and context.get("booking"):
            self._agent._context["source_provenance"] = context["source_provenance"]
        self._agent.should_stop = lambda: not self.state.control("worker_enabled", False)
        return self._agent

    def _vehicle_evidence(self,data,period,cutoff):
        from .supra.booking_vehicles import vehicle_evidence
        pair=data.vehicle_pair
        if not pair:return vehicle_evidence(data,period,cutoff)
        key=(pair['booking_digest'],pair['raw_digest'],pair['vehicle_catalog_sha256'],period,str(cutoff.date()))
        if getattr(self,'_vehicle_cache_key',None)!=key:
            self._vehicle_cache=vehicle_evidence(data,period,cutoff);self._vehicle_cache_key=key
        return self._vehicle_cache

    def _rule_handling(self,data,source,facts,period,cutoff):
        """DC handling from the active owner rule layer, so /run, /chief and model context match /chiphi.

        Returns None when no layer is installed (legacy engine unchanged). An
        installed but stale/invalid layer holds the component as N/A.
        """
        if not getattr(data,'vehicle_pair',None) or not getattr(data,'handling_catalog',None):return None
        evidence=self._vehicle_evidence(data,period,cutoff)
        if not evidence.get('available'):return None
        from .telegram_rule_layer import FILENAME as RULES_FILENAME,load_rules
        if not (self.state.root/RULES_FILENAME).exists():return None
        from .supra.booking_handling import handling_summary
        stale=getattr(self,'_raw_stale',False)
        try:
            if stale:raise ValueError('Raw stale: two-leg evidence cannot be used')
            rules,rules_sha=load_rules(self.state,data,source,facts,self.owner)
            from .telegram_secrets import load_protected
            from .telegram_manual_cost_policy import FILENAME as COST_POLICY_FILENAME
            from .supra.vehicle_cost import booking_rule_handling
            policy=json.loads(load_protected(self.state.root/COST_POLICY_FILENAME))
            result=booking_rule_handling(data,evidence,period,cutoff,rules,rules_sha,policy)
        except (ValueError,OSError,KeyError,TypeError):
            result=None;rules_sha=None
        if result is None:
            # Fail closed: never present the legacy GHN-wide figure as the owner-rule result.
            dates=data.rows.dispatch_date
            scoped=data.rows[dates.map(lambda d:d is not None and d.strftime('%Y-%m')==period and d<=cutoff.date())]
            result=handling_summary(data,scoped,period,cutoff)
            if not result['available']:return result
            result.update(complete=False,component_vnd=None,eligible_vnd=None,eligible_kg=None,eligible_transfer_ghn=None,
                direct_excluded_ghn=None,unresolved_ghn=None,reason='RAW_STALE' if stale else 'OWNER_RULES_INVALID_OR_STALE',grain='GHN_BOOKING_E_DAY',rules_sha256=rules_sha,
                rate_buckets=[{**b,'eligible_ghn':0,'eligible_kg':None,'eligible_vnd':None} for b in result['rate_buckets']])
        return result

    def _booking_context(self,data,period,warehouses,cutoff,source):
        from .supra.booking import booking_context
        facts,_,_=self._effective_facts()
        return booking_context(data,period,warehouses,cutoff,source,handling=self._rule_handling(data,source,facts,period,cutoff))

    def _booking_report(self, data, source, facts, period, warehouses, cutoff):
        self._report_details = None
        context = self._booking_context(data, period, warehouses, cutoff, source)
        if source.get("name") in ("GOOGLE_CONNECTOR_SNAPSHOT", "GOOGLE_BROWSER_VIEWER_SNAPSHOT") and time.time()-source["created"] > 900:
            return "Em chưa lập được báo cáo mới vì bản nhận dữ liệu đã quá 15 phút. Anh cần đọc lại đúng nguồn; em chưa gọi AI hoặc dùng số cũ thay bản mới."
        if not context["rows"]:
            return "Em chưa lập được báo cáo booking: không có dòng đã biết ngày xuất trong kỳ và thời điểm chốt đã chọn. Anh kiểm tra lại kỳ, tab/DC và ngày nguồn; em chưa gọi AI."
        warnings = []
        _, assumptions, _ = self._effective_facts()
        if assumptions:
            readable = {"period": "kỳ", "warehouses": "DC", "cutoff": "giờ chốt", "report_time": "giờ gửi", "timezone": "múi giờ"}
            values = []
            for value in assumptions:
                if value.startswith("period=CURRENT_MONTH"):
                    values.append("kỳ tháng hiện tại")
                else:
                    key, _, text = value.partition("=")
                    values.append(readable.get(key, key)+" "+text)
            warnings.append("Đang dùng giả định theo kế hoạch: "+"; ".join(values)+".")
        if context["latest"] is None or (cutoff.date()-context["latest"]).days > 1:
            warnings.append("Ngày xuất mới nhất cách ngày chốt hơn một ngày; bản vừa nhận không có nghĩa dữ liệu nghiệp vụ đã mới.")
        return self._plain_booking_report(context, warnings)

    def _plain_booking_report(self, context, warnings):
        from .chief.report import booking_review
        from .supra.vehicle_report import booking_intake
        from .chief.agent import NO_AI_NOTE
        result = booking_review(context, warnings)
        record = TelegramAgent.chief_record(result, context)
        self._last_report_record = record
        self._report_details = TelegramAgent.detail_text(record)
        return booking_intake(result)

    def _chief_source(self):
        from .supra.booking import BookingData, booking_context
        from .chief.ingestion import FinancialData
        from .chief.report import booking_review
        from .chief.agent import metric_context
        data, source, facts, period, warehouses, cutoff, _ = self._scoped()
        if isinstance(data, BookingData):
            context = self._booking_context(data, period, warehouses, cutoff, source)
            warnings = []
            if time.time()-source.get("created", time.time()) > 900:
                warnings.append("Bản chụp nguồn đã quá 15 phút; cần đọc lại nguồn trước báo cáo mới, không coi đây là dữ liệu trực tuyến.")
            if context["latest"] is None or (cutoff.date()-context["latest"]).days > 1:
                warnings.append("Ngày xuất mới nhất cách ngày chốt hơn một ngày; bản nhận mới không chứng minh nghiệp vụ đã mới.")
            return booking_review(context, warnings), context
        if isinstance(data, FinancialData):
            result = self._financial_result(data, period, warehouses, cutoff)
            return result, dict(label=data.label+"; SHA256 "+source["digest"], scope=result["scope"], metrics=metric_context(result))

    def _source_code_report(self, request, prepared, notice=""):
        from .chief.agent import NO_AI_NOTE
        from .chief.report import render
        result, context = prepared
        notice = notice or NO_AI_NOTE
        record = TelegramAgent.chief_record(result, context, notice=notice)
        key = (request.chat_id, request.topic_id, request.user_id)
        self._code_details[key] = record
        if len(self._code_details) > 100:
            self._code_details.pop(next(iter(self._code_details)))
        if 'booking_rows' in result:
            from .supra.vehicle_report import booking_intake
            return booking_intake(result)
        return render(result, telegram=True, audit_text=notice)

    @staticmethod
    def _financial_result(data, period, warehouses, cutoff):
        from .chief.engine import evaluate
        result = evaluate(data.tables, period=period, warehouses=warehouses, cutoff=cutoff.date())
        result["data_label"] = data.label
        return result

    def _financial_report(self, data, source, period, warehouses, cutoff):
        from .chief.report import render
        from .chief.agent import NO_AI_NOTE
        result = self._financial_result(data, period, warehouses, cutoff)
        self._report_details = None
        notice = NO_AI_NOTE
        if self.settings.get("models"):
            try:
                agent = self._get_agent()
                agent.last_presentation = None
                text = agent.answer(Incoming(GROUP_ID, self.owner, 0, None, "chief", "Thẩm định P&L theo số code, chỉ rõ phần chưa được và ba quyết định cần chốt."))
                if agent.last_presentation:
                    self._last_report_record = agent.last_presentation
                    self._report_details = agent.detail_text(agent.last_presentation)
                    return text
                notice = "AI chưa hoàn tất; giữ số/bằng chứng code, chưa có quyết định Auditor cho báo cáo này."
            except (ValueError, ProviderError, TypeError):
                notice = "Cấu hình AI chưa sẵn sàng; Chief AI/Auditor chưa chạy cho báo cáo này (0 HTTP)."
        record = TelegramAgent.chief_record(result, dict(label=data.label, scope=result["scope"], metrics={}), notice=notice)
        self._last_report_record = record
        self._report_details = TelegramAgent.detail_text(record)
        return render(result, telegram=True, audit_text=notice)

    def tick(self, send, now=None, *, external_snapshot=False):
        if not self.state.control("schedule_enabled", False):
            return {"state": "SCHEDULE_PAUSED"}
        facts, _, _ = self._effective_facts()
        if not facts.get("report_time"):
            self.state.set_control("schedule_enabled", False)
            return
        now = now or datetime.now(TZ)
        hour, minute = map(int, facts["report_time"].split(":"))
        due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now < due:
            return {"state": "NOT_DUE"}
        code = self.state.control("approved_plan")
        if not code:
            self.state.set_control("schedule_enabled", False)
            return
        if code != self._plan_code():
            self.state.set_control("schedule_enabled", False)
            self.state.set_control("last_error", "Kỳ/phạm vi hoặc giả định kế hoạch đã đổi; owner cần xem /plan và /enable lại.")
            return {"state": "PLAN_CHANGED"}
        raw_stale = False
        if facts.get("source_id") in ("snapshot", "browser_snapshot"):
            try:
                _, source = self._load()
                browser = source.get("name") == "GOOGLE_BROWSER_VIEWER_SNAPSHOT"
                if browser and not external_snapshot:
                    return {"state": "AWAITING_BROWSER_EXPORT_PIPELINE"}
                if now.timestamp()-source["created"] > 900 and (external_snapshot or (now-due).total_seconds() <= 600):
                    return {"state": "AWAITING_FRESH_SOURCE"}
                # Owner rule 27/10/2026: stale Raw does not block; Booking part is sent, Raw-based parts N/A.
                raw_stale = 'vehicle_raw_created' in source and now.timestamp()-source['vehicle_raw_created']>900
            except Exception:
                return {"state": "SOURCE_UNAVAILABLE"}
        if external_snapshot:
            _, current_code, missing = self.plan()
            blocking = [m for m in missing if not (m.startswith("không có dòng nghiệp vụ") or m.startswith("kho chưa có dòng") or m.startswith("paired Raw cần lượt đọc mới"))]
            if blocking or current_code != code:
                return {"state": "PLAN_OR_SCOPE_NOT_READY"}
        job = f"daily:{due.date().isoformat()}:{hour:02}{minute:02}:{code}"
        if not self.state.claim(job):
            return {"state": "DUPLICATE_JOB", "job": job}
        if (now-due).total_seconds() > 3600:
            self.state.delivery(job, "SKIPPED")
            return {"state": "MISSED_WINDOW", "job": job}
        self._raw_stale = raw_stale
        try:
            report = self.report()
        finally:
            self._raw_stale = False
        if not self.safe_to_send(report):
            report = "Đã chặn báo cáo chứa nội dung riêng của phiên."
        try:
            delivered = send(Incoming(GROUP_ID, self.owner, 0, None, "run", ""), report, job)
            self.state.delivery(job, "SKIPPED" if delivered is False else "SENT", report)
            return {"state": "STOPPED" if delivered is False else "SENT", "job": job}
        except Exception:
            self.state.delivery(job, "UNKNOWN")
            raise

    def clear(self):
        self._vehicle_cache = None
        self._vehicle_cache_key = None
        self._drafts.clear()
        self._report_details = None
        self._code_details.clear()
        self._last_report_record = None
        self._report_origin = None
        self._trace_local.clear()
        if self._agent:
            self._agent.clear()
        self.settings.clear()
        self._known.clear()
