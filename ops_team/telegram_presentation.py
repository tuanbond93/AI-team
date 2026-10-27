"""Human-facing text only; never changes source values, verdicts or permissions."""
from datetime import datetime
import re

BOOKING_LIMIT = "Chưa đủ căn cứ tính chi phí, OT, số kiện, khối lượng, tải xe, chuyến/điểm giao hay SLA. Mã lặp có thể là dòng chi tiết hoặc lịch sử; chưa tự xóa, gộp hay chọn dòng cuối. Không cộng hoặc điền xuống các tổng gộp khi chưa rõ quy tắc."
HANDLING_LIMIT = "J/L và bốc xếp DC được đọc theo metric code và điều khoản owner; phần thiếu vẫn N/A, subtotal chỉ là phần đủ nguồn. Chưa là tiền nghiệm thu/thanh toán hoặc total cost/P&L. Chưa đủ kiện/thùng, xe/chuyến/drop, OT, tải xe hoặc SLA; không cộng hay điền xuống tổng gộp."
SOURCE_LIMIT = "Đây là bản chụp, không phải dữ liệu trực tuyến và chưa đối soát thực tế."
INPUT_QUESTIONS = {
    "Accounting": "Anh có sổ chi phí kèm chứng từ theo kỳ và kho cần đối soát chưa?",
    "Cost": "Anh muốn đánh giá kỳ nào, tại kho nào, và hiện có sổ chi phí cùng sản lượng của kỳ đó chưa?",
    "Operations": "Anh cần xem kho nào, kỳ nào và hiện có sản lượng cùng nhật ký vận hành chưa?",
    "Workforce": "Anh có số người, giờ công và sản lượng theo kho/ca trong kỳ cần đánh giá chưa?",
    "Fleet": "Anh có nhật ký chuyến xe kèm km, tải thực chở và chi phí trong kỳ cần đánh giá chưa?",
    "KPI": "Anh muốn đánh giá chỉ số nào, trong kỳ và kho nào, và có dữ liệu gốc để tính chưa?",
    "Auditor": "Anh muốn kiểm tra nhận định nào và hiện có nguồn số liệu hoặc chứng từ nào để đối chiếu?",
    "Coordinator": "Anh muốn ưu tiên vấn đề nào, tại kho nào và trong kỳ nào để bắt đầu?",
}


def guarded_no_source_text(text, metrics=None):
    """Keep general guidance; no invented sampling rates or quantitative decisions."""
    number = r"(?<!\w)(?:\d+(?:[.,]\d+)?|một|hai|ba|bốn|năm|sáu|bảy|tám|chín|mười)"
    quantity = number+r"\s*(?:%|phần trăm|triệu|tỷ|nghìn|ngàn|VND\b|USD\b|đồng\b|người\b|nhân sự\b|nhân viên\b|xe\b|chuyến\b|ca\b|giờ\b|kiện\b|kg\b|tấn\b|m[³3]\b|điểm giao\b|đơn\b|dòng\b|mã\b|kho\b|km\b|ngày\b|tuần\b|tháng\b)"
    meta = (r"(?:câu trả lời|phản hồi|nội dung trả lời).{0,80}(?:đã được|được đưa ra|ngắn gọn|quy trình trao đổi)"
            r"|(?:phù hợp|đúng)\s+(?:với\s+)?quy trình trao đổi"
            r"|(?:chưa|không) cần.{0,25}(?:điều chỉnh kỹ thuật|tìm kiếm thêm)"
            r"|(?:tôi|em) đã\s+(?:trả lời|đưa ra câu trả lời)")
    kept, blocked = [], False
    for part in re.split(r"\n+|(?<=[.!?;])\s+", str(text).strip()):
        if re.search(meta, part, re.I):
            continue
        if re.search(quantity, part, re.I):
            blocked = True
        elif part.strip():
            kept.append(human_text(part, metrics))
    warning = "Chưa có số liệu làm căn cứ nên em chưa đề xuất tỷ lệ lấy mẫu, mức tiết kiệm hay định mức nhân sự/xe."
    return " ".join(kept), [warning] if blocked else []


def human_text(text, metrics=None):
    text = str(text).strip()
    for key, metric in (metrics or {}).items():
        text = text.replace(key, metric.get("label", "chỉ số nguồn"))
    text = re.sub(r"\[\s*(?:booking\.[^\]]*)\]", "", text)
    text = re.sub(r"\b(?:booking\.[A-Za-z0-9_.]+|rowref_[0-9]+)\b", "chỉ số nguồn", text)
    text = re.sub(r"\b[a-fA-F0-9]{32,64}\b", "[xem /details]", text)
    text = text.replace("OWNER SOURCE / UNVERIFIED", "chưa đối soát").replace("OWNER SOURCE", "nguồn được cung cấp").replace("UNVERIFIED", "chưa đối soát")
    text = text.replace("unknown/missing", "chưa xác định trong nguồn")
    text = re.sub(r"(?im)^\s*(?:SHA256|Model|scope signature)\s*:.*$", "", text)
    text = re.sub(r"(?im)^(?:Coordinator|Operations|Accounting|Cost|Workforce|Fleet|KPI|Auditor)\s*[:·—-]\s*", "", text)
    return text.strip()


def guarded_booking_text(text, metrics=None):
    """Do not repeat unsupported business claims, even if a model makes them."""
    patterns = (
        (r"trùng thừa|dòng thừa|duplicate errors?|lỗi trùng", "Mã lặp chưa chứng minh lỗi trùng; cần xác nhận đó là dòng chi tiết hay cập nhật trạng thái."),
        (r"(?:đơn|mã|orders?).{0,35}(?:hợp lệ|valid\b)", "Định dạng mã đọc được chưa chứng minh đơn hợp lệ về nghiệp vụ."),
        (r"(?:xóa|xoá|dedup|loại bỏ|gộp|giữ dòng cuối).{0,45}(?:dòng|mã|booking|duplicate)", "Chưa được tự xóa, gộp hoặc giữ dòng cuối khi chưa rõ quy tắc mã lặp."),
        (r"(?:có thể|tính được|đủ cơ sở|đủ dữ liệu).{0,65}(?:khối lượng|tải trọng|chuyến xe|số xe)", "Chưa đủ khóa chuyến và quy tắc số lượng để tính khối lượng hoặc chuyến xe."),
        (r"(?:unknown|chưa rõ|chưa xác định).{0,55}(?:là|tức|đều|gồm).{0,20}(?:chưa giao|chưa hoàn tất)", "Trạng thái chưa xác định không đồng nghĩa hàng chưa giao."),
        (r"(?:delivered|đã giao).{0,55}(?:SLA|đúng hạn|đúng cam kết|hoàn tất thực tế)", "Trạng thái delivered trong nguồn chưa xác nhận giao đúng hạn, SLA hoặc hoàn tất thực tế."),
        (r"(?:SLA|giao đúng hạn).{0,45}(?:bằng|chính|là).{0,20}delivered", "Trạng thái delivered trong nguồn chưa xác nhận SLA."),
    )
    kept, warnings = [], []
    for part in re.split(r"\n+|(?<=[!?;])\s+|(?<!\d)\.\s+(?=[A-ZÀ-Ỹ])", str(text).strip()):
        if not part.strip():
            continue
        rejected = False
        for index, (pattern, warning) in enumerate(patterns):
            for match in re.finditer(pattern, part, flags=re.I):
                prefix = part[max(0, match.start()-65):match.start()]
                prefix = re.split(r"\b(?:nhưng|tuy nhiên|song|và)\b|[,;:]", prefix, flags=re.I)[-1]
                matched = match.group()
                negation_scope = prefix if index == 1 else prefix+matched
                if index == 4:
                    # 'chưa xác định' / 'chưa giao' are state names, not negation
                    # of the proposed equivalence. Look for explicit qualification.
                    if re.search(r"không\s+(?:đồng nghĩa|phải|có nghĩa)|chưa\s+(?:thể|đủ căn cứ|đủ cơ sở)", prefix+matched, re.I):
                        continue
                    if warning not in warnings:
                        warnings.append(warning)
                    rejected = True
                    continue
                if re.search(r"không|chưa|đừng|chớ", negation_scope, re.I):
                    continue
                if warning not in warnings:
                    warnings.append(warning)
                rejected = True
        if not rejected:
            value = human_text(part, metrics)
            if value:
                kept.append(value)
    return ". ".join(kept), warnings


def unique(items):
    return list(dict.fromkeys(v.strip() for v in items if isinstance(v, str) and v.strip()))


def needs_attention(text):
    """Caveats stay visible even when the short list of actions is full."""
    return bool(re.search(r"chưa|thiếu|không|mâu thuẫn|rủi ro|lưu ý|cảnh báo|cần xác minh", text, re.I))


def source_note(context):
    source = context.get("source_provenance", {})
    received = source.get("retrieved_at", "chưa xác định")
    latest = source.get("latest_dispatch_date", "chưa xác định")
    try:
        received = datetime.fromisoformat(received).strftime("%H:%M %d/%m/%Y")
    except (ValueError, TypeError):
        pass
    try:
        latest = datetime.fromisoformat(latest).strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        latest = "chưa xác định" if latest in (None, "None", "NaT") else latest
    return f"Dữ liệu nhận lúc {received}; ngày xuất mới nhất {latest}. {SOURCE_LIMIT}"


def booking_facts(context):
    metrics = context["metrics"]
    def number(key):
        value = metrics.get(key, {}).get("value")
        return f"{int(value):,}".replace(",", ".") if value is not None else "chưa rõ"
    period = metrics.get("booking.rows", {}).get("period") or "chưa xác định"
    shown_period = period[5:]+"/"+period[:4] if re.fullmatch(r"[0-9]{4}-[0-9]{2}", period) else period
    headline = f"Booking tháng {shown_period}: {number('booking.rows')} dòng ghi nhận {number('booking.unique_orders')} mã GHN khác nhau."
    points, warnings = [], []
    points.append(f"{number('booking.duplicate_rows')} dòng có mã lặp lại; chưa đủ căn cứ gọi là lỗi trùng hoặc dòng thừa.")
    points.append(f"{number('booking.conflicting_status_ids')} mã có nhiều trạng thái khác nhau; cần hiểu cách cập nhật trước khi kết luận.")
    points.append(f"{number('booking.consistent_delivered_ids')} mã chỉ có trạng thái delivered trong phạm vi đang xem; chưa phải số đơn đạt SLA.")
    for key, label in (("booking.missing_ids", "dòng thiếu mã hoặc chưa đọc được định dạng mã"),
                       ("booking.unscoped_dates", "dòng thiếu/sai ngày xuất, chưa xếp được vào kỳ"),
                       ("booking.missing_ltc_dates", "dòng trong kỳ thiếu/sai Ngày LTC")):
        if metrics.get(key, {}).get("value", 0):
            warnings.append(f"Còn {number(key)} {label}.")
    unknown = metrics.get("booking.status.unknown_missing", {}).get("value", 0)
    if unknown:
        warnings.append(f"Có {int(unknown):,} dòng chưa xác định trạng thái trong nguồn; không được coi là hàng chưa giao.".replace(",", "."))
    return headline, points, warnings


def audit_notes(result, context):
    if not result:
        return "Phần nhận định này chưa được kiểm tra chéo.", [], [], []
    audit, task = result["audit_answer"], result["task_id"]
    decisions = result["audit_decisions"]
    matched = [d for d in decisions if d["task_id"] == task]
    warnings, actions, questions = [], [], []
    if not matched:
        warnings.append("Phản hồi kiểm tra chưa khớp bản đang xét nên chưa thể chấp nhận nhận định này.")
    if len(matched) > 1:
        warnings.append("Có nhiều quyết định cho cùng bản, cần làm rõ trước khi chốt.")
    for decision in matched:
        reason, blocked = guarded_booking_text(decision["reason"], context["metrics"])
        warnings += blocked
        if reason:
            target = actions if decision["verdict"] == "accept" and result["review_complete"] else warnings
            label = {"accept": "Ý kiến chấp nhận phần diễn giải: ", "revise": "Cần sửa: ", "unresolved": "Cần làm rõ: "}[decision["verdict"]]
            target.append(label+reason)
    for decision in decisions:
        if decision["task_id"] != task:
            reason, blocked = guarded_booking_text(decision["reason"], context["metrics"])
            warnings += blocked
            if reason:
                warnings.append("Nhận xét đang trỏ sang bản khác: "+reason)
    for value in [*audit["disagreements"], *result.get("source_issues", [])]:
        text, blocked = guarded_booking_text(value, context["metrics"])
        warnings += blocked
        if text:
            warnings.append(text)
    summary, blocked = guarded_booking_text(audit["summary"], context["metrics"])
    warnings += blocked
    if summary and (not result["review_complete"] or needs_attention(summary)):
        warnings.append(summary)
    for value in [*[v["text"] for v in audit["observations"]], *audit["hypotheses"]]:
        text, blocked = guarded_booking_text(value, context["metrics"])
        warnings += blocked
        if text:
            # Auditor qualifications must not disappear behind the five-action limit.
            warnings.append(text)
    for value in audit["proposals"]:
        text, blocked = guarded_booking_text(value, context["metrics"])
        warnings += blocked
        if text:
            (warnings if needs_attention(text) else actions).append(text)
    for item in audit["requests"]:
        text, blocked = guarded_booking_text(item["question"], context["metrics"])
        warnings += blocked
        if text:
            questions.append(text)
    status = ("Phần diễn giải đã được kiểm tra chéo, chưa thay đối soát nghiệp vụ."
              if result["review_complete"] else "Phần nhận định chưa được chấp nhận; anh cần làm rõ các điểm chưa chốt bên dưới.")
    return status, unique(warnings), unique(actions), unique(questions)


def compose(headline, points, warnings, questions, footnotes):
    lines = [headline.strip()] if headline.strip() else []
    points, warnings, questions = unique(points), unique(warnings), unique(questions)
    for point in points[:5]:
        lines.append("• "+point)
    if len(points) > 5:
        lines.append("Bản tin ưu tiên 5 ý; anh có thể xem bản truy vết rút gọn qua /details.")
    if warnings:
        lines.append("Chưa chốt:")
        lines.extend("• "+value for value in warnings)
    if questions:
        lines.append("Anh xác nhận giúp:")
        lines.extend("• "+value for value in questions)
    lines.extend(unique(footnotes))
    return "\n".join(lines)


def conversation(answer, context, review, audit=None, *, report=False, extra_warnings=(), role="Coordinator"):
    metrics, booking = context.get("metrics", {}), context.get("booking", False)
    meaningful = [answer["summary"], *[v["text"] for v in answer["observations"]], *[v["question"] for v in answer["requests"]],
                  *answer["hypotheses"], *answer["proposals"], *answer["disagreements"]]
    if not any(v.strip() for v in meaningful):
        from .limits import ProviderError
        raise ProviderError("Model chưa trả nội dung/câu hỏi cụ thể; yêu cầu chưa hoàn tất.")
    guard = ((lambda text: guarded_booking_text(text, metrics)) if booking else
             (lambda text: guarded_no_source_text(text, metrics)) if not metrics else
             (lambda text: (human_text(text, metrics), [])))
    summary, warnings = guard(answer["summary"])
    points, questions = [], []
    for value in [*[v["text"] for v in answer["observations"]], *answer["proposals"]]:
        text, blocked = guard(value)
        warnings += blocked
        if text:
            (warnings if needs_attention(text) else points).append(text)
    for value in answer["hypotheses"]:
        text, blocked = guard(value)
        warnings += blocked
        if text:
            warnings.append("Cần xác minh: "+text)
    for value in answer["disagreements"]:
        text, blocked = guard(value)
        warnings += blocked
        if text:
            warnings.append(text)
    for item in answer["requests"]:
        text, blocked = guard(item["question"])
        warnings += blocked
        if text:
            questions.append(text)
    if booking:
        headline, facts, gaps = booking_facts(context)
        if report and summary and summary != headline:
            if re.search(r"chưa|không|thiếu|mâu thuẫn|rủi ro", summary, re.I):
                warnings.append(summary)
            else:
                points.insert(0, summary)
        if report or not summary:
            summary = headline
        if report:
            points = facts+points
        warnings += gaps+list(extra_warnings)
        status, audit_warnings, audit_actions, audit_questions = audit_notes(audit, context)
        warnings += audit_warnings
        points += audit_actions
        questions += audit_questions
        limit=HANDLING_LIMIT if context.get('handling_quality',{}).get('available') else BOOKING_LIMIT
        return compose(summary, points, warnings, questions, [status, limit, source_note(context), "Anh có thể gọi /details để xem nguồn và quyết định kiểm tra."])
    status = "Nhận định sơ bộ, chưa được kiểm tra chéo." if "CHƯA QUA" in review else ("Phần nhận định vẫn còn điểm chưa chốt." if "CÒN MỞ" in review else "Diễn giải đã qua kiểm tra chéo; đề xuất vẫn cần anh duyệt.")
    note = "Số kế hoạch, chưa đối soát thực tế." if "PLAN" in context.get("label", "") else ("Dữ liệu demo, không dùng cho quyết định thật." if "DEMO" in context.get("label", "") else "")
    if not metrics:
        if not summary:
            summary = "Trước hết cần thống nhất phạm vi và nguồn số liệu để đánh giá."
        if not questions:
            questions.append(INPUT_QUESTIONS.get(role, INPUT_QUESTIONS["Coordinator"]))
        note = "Em chưa có số liệu doanh nghiệp để xác minh câu hỏi này."
    if context.get('vehicle'):
        p=context['source_provenance']
        note=(context['scope']+'; nhận Booking '+p['booking_retrieved_at']+'; nhận Raw '+p['raw_retrieved_at']+
            '. Hai bản nguồn chụp riêng, chưa là dữ liệu trực tuyến hoặc xác minh thực tế. '+
            'Tỷ lệ theo bản ghi DELIVER và membership ALL CS; không phân bổ tài chính. AG upstream/NCC share N/A khi thiếu mẫu số chính AG.')
    return compose(summary, points, warnings, questions, [status, note])
