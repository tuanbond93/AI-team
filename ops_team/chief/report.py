"""Four business sections; evidence and incomplete values remain explicit."""
import re
from decimal import Decimal
from .engine import issue

METRICS = (
    ("kg", "Khối lượng B2B", "kg"), ("pieces", "Số kiện B2B", "kiện"), ("cartons", "Số thùng B2B", "thùng"),
    ("product_units", "Số sản phẩm bên trong thùng", "sản phẩm"),
    ("revenue", "Doanh thu", "VND"), ("stage1", "Chặng 1 — xe trung chuyển", "VND"),
    ("stage2", "Chặng 2 — kho", "VND"), ("stage3", "Chặng 3 — xe giao + lương", "VND"),
    ("totalCost", "Tổng cost-to-serve", "VND"), ("gp", "Lợi nhuận gộp", "VND"),
    ("marginPct", "Biên GP", "%"), ("revPerPcs", "Doanh thu/kiện", "VND/kiện"),
    ("costPerPcs", "Chi phí/kiện", "VND/kiện"), ("gpPerPcs", "GP/kiện", "VND/kiện"),
)


def display(value, unit=""):
    if value is None:
        return "N/A — chưa đủ dữ liệu" if unit != "%" else "N/A — thiếu dữ liệu hoặc doanh thu bằng 0"
    value = Decimal(str(value))
    digits = 0 if unit in ("VND", "kiện", "thùng", "sản phẩm", "dòng", "mã GHN", "đợt GHN/ngày") else 3 if unit=='kg' else 2
    if unit=='VND theo điều khoản':
        digits=max(3,-value.as_tuple().exponent)
    return f"{value:,.{digits}f}".replace(",", "_").replace(".", ",").replace("_", ".")


def priorities(result):
    if result.get("intake_priorities"):
        return result["intake_priorities"][:3]
    choices = []
    for item in sorted(result["issues"], key=lambda i: {"CRITICAL": 0, "WARNING": 1, "IMPROVEMENT": 2}[i["severity"]]):
        action = item["actions"][0]["action"]
        if action not in choices:
            choices.append(action)
    for fallback in ("Chốt phạm vi/kỳ và người chịu trách nhiệm cung cấp nguồn còn thiếu.",
                     "Xác nhận đủ doanh thu, ba chặng chi phí và mẫu số B2B/B2C trước khi duyệt P&L.",
                     "Chốt kỳ vọng biên lợi nhuận và thời hạn đối soát chứng từ."):
        if fallback not in choices:
            choices.append(fallback)
    return choices[:3]


def metric_rows(result):
    rows = []
    totals = result["totals"]
    for key, label, unit in METRICS:
        value = totals.get(key)
        if key=='kg' and result.get('weight_quality',{}).get('available'):
            label='Khối lượng nguồn — toàn phạm vi, một tổng/GHN'
        comment = "Chưa đủ dữ liệu" if value is None else "Phần tính được, chưa đối soát" if not result["financial_complete"] else "Tính từ nguồn khai báo, chưa đối soát"
        if key in ("stage1", "stage2", "stage3") and value is not None and totals.get("revenue"):
            comment += "; "+display(value/totals["revenue"]*100, "%")+"% doanh thu"
        rows.append({"Chỉ tiêu": label, "Giá trị tính toán": display(value, unit), "Đơn vị": unit, "Đánh giá sơ bộ": comment})
    rows += result.get("booking_rows", [])
    return rows


def booking_review(context, extra_warnings=()):
    metrics = context["metrics"]
    refs = sorted({s for m in metrics.values() for s in m.get("sources", [])})
    rows = []
    for key, label in (("booking.rows", "Dòng booking có ngày trong kỳ"), ("booking.unique_orders", "Mã GHN khác nhau đọc được"),
                       ("booking.duplicate_rows", "Dòng có mã lặp ngoài lần đầu"), ("booking.conflicting_status_ids", "Mã có nhiều trạng thái"),
                       ("booking.consistent_delivered_ids", "Mã chỉ có trạng thái delivered"), ("booking.missing_ids", "Dòng thiếu/sai mã"),
                       ("booking.unscoped_dates", "Dòng chưa xếp kỳ vì thiếu/sai ngày")):
        m = metrics.get(key, {})
        rows.append({"Chỉ tiêu": label, "Giá trị tính toán": display(m.get("value"), m.get("unit", "dòng")), "Đơn vị": m.get("unit", "dòng"),
            "Đánh giá sơ bộ": "Số nguồn; mã lặp chưa chứng minh lỗi/đơn hợp lệ, delivered chưa phải SLA"})
    issues = [issue("MISSING_FINANCE", "Booking chưa có trọng lượng kèm đơn vị, số kiện/thùng theo grain, cước hiệu lực và chi phí đủ ba chặng.", refs,
        action="Cung cấp bảng doanh thu/cước theo tỉnh và ngày hiệu lực, khối lượng kèm g/kg, số kiện/thùng đúng grain và chi phí đủ ba chặng; chủ sở hữu xác nhận phạm vi trước khi dùng."),
        issue("BOOKING_GRAIN", "Mã GHN lặp có thể là dòng chi tiết/lịch sử. Không tự xóa, gộp hoặc chọn dòng cuối; không suy kg/chuyến/SLA từ số đếm. Chưa đủ căn cứ tính chi phí, OT, số kiện, khối lượng, tải xe, chuyến/điểm giao hay SLA. Không cộng hoặc điền xuống các tổng gộp khi chưa rõ quy tắc.", refs,
        action="Xác nhận grain của từng dòng và khóa đơn/chuyến với đầu mối vận hành.")]
    for warning in extra_warnings:
        issues.append(issue("SOURCE_CAVEAT", warning, refs))
    quality=context.get('quantity_quality',{})
    if quality.get('available'):
        for key in ('eligible_product_units','missing_quantity_rows','invalid_quantity_rows','missing_key_rows',
                    'repeated_key_rows','conflicting_key_rows','quarantined_rows'):
            m=metrics['booking.'+key]
            rows.append({'Chỉ tiêu':m['label'],'Giá trị tính toán':display(m['value'],m['unit']),
                'Đơn vị':m['unit'],'Đánh giá sơ bộ':('Chỉ phần đủ khóa; không dùng làm tổng toàn phạm vi' if key=='eligible_product_units' and not quality.get('complete')
                    else 'SO/DO ở nhiều GHN: cảnh báo kiểm tra; chưa tự loại dòng' if key=='conflicting_key_rows'
                    else 'Đơn vị sản phẩm, không phải kiện/thùng; nguồn chưa đối soát')})
        if not quality.get('complete'):
            issues.append(issue('PRODUCT_UNITS_PARTIAL',
                'Tổng sản phẩm toàn phạm vi N/A: có Qty thiếu/lỗi hoặc khóa GHN+SO+DO thiếu/trùng. Giữ nguyên dòng, cách ly cả mã GHN bị ảnh hưởng; phần đủ khóa không đại diện toàn phạm vi.',refs,
                action='Đối chiếu các dòng Qty thiếu/lỗi và khóa GHN+SO+DO trùng với đầu mối cung cấp; chưa xóa hoặc chọn dòng cuối.'))
        if quality.get('conflicting_key_rows'):
            issues.append(issue('DO_MULTIPLE_GHN',
                f"Có {quality['conflicting_key_rows']} dòng SO/DO xuất hiện ở nhiều GHN trong nguồn. Đây là cảnh báo để xác nhận các đợt giao; chưa tự loại dòng theo quy tắc ngoài xác nhận của owner.",refs,
                action='Xác nhận SO/DO ở nhiều GHN là các phần giao riêng; giữ chi tiết ngày, Qty và dòng nguồn để đối chiếu.'))
    unknown = metrics.get("booking.status.unknown_missing", {}).get("value", 0)
    if unknown:
        issues.append(issue("UNKNOWN_STATUS", f"Có {unknown} dòng chưa xác định trạng thái; không đồng nghĩa hàng chưa giao.", refs))
    result = {"profile": "Chief Auditor & Operational Financial Advisor", "totals": {key: None for key, _, _ in METRICS},
        "orders": [], "trips": [], "allocations": [], "checks": [], "issues": issues, "sources": refs, "financial_complete": False,
        "status": "VÀNG", "verification": "UNVERIFIED", "modes": ["BOOKING SNAPSHOT"], "scope": context["scope"], "booking_rows": rows,
        "source_provenance": context.get("source_provenance", {})}
    result["lead"] = "Chưa thể tính P&L từ booking hiện tại: nguồn mới có mã đơn, ngày và trạng thái; chưa có đủ doanh thu/cước, sản lượng đúng đơn vị và chi phí phục vụ."
    result["intake_priorities"] = [
        "Bổ sung bảng đơn: ngày/kho/tỉnh, khối lượng kèm g/kg, số kiện/thùng và xác nhận mỗi dòng đại diện gì.",
        "Bổ sung doanh thu hoặc cước theo tỉnh/nấc kg có ngày hiệu lực, cùng biểu lương giao tính theo từng đơn.",
        "Nối đơn với chuyến/chặng/ngày/tuyến; bổ sung giá xe, tải xe, pool kho, chi phí ba chặng và mẫu số san sẻ B2B/B2C; chốt người và hạn đối soát."]
    result["intake_question"] = "Bước tiếp theo: anh có thể gửi trước bảng cước/doanh thu đang áp dụng, kèm ngày hiệu lực và đơn vị tính, để đối chiếu với booking không?"
    result['totals']['product_units']=metrics.get('booking.product_units',{}).get('value')
    if quality.get('available'):
        result['quantity_quality']=quality
        result['lead']=('Đã có số sản phẩm trong thùng theo Qty(Units), cộng các DO trong kỳ theo GHN. ' if quality.get('complete')
            else 'Đã đọc Qty(Units) theo đơn vị sản phẩm; tổng toàn phạm vi chưa chốt do dòng thiếu/lỗi hoặc khóa chưa rõ. ')+\
            'Chưa có đủ kg, số kiện/thùng và chi phí phục vụ; doanh thu/P&L tiếp tục N/A, chưa đối soát tài chính.'
        result['intake_priorities']=[
            'Đối chiếu Qty thiếu/lỗi, khóa GHN+SO+DO trùng và các cảnh báo SO/DO ở nhiều GHN; giữ riêng ngày và DO của từng đợt giao.',
            'Bổ sung khối lượng theo đơn kèm g/kg và phân biệt sản phẩm, kiện, thùng; không dùng hệ số quy đổi ước tính.',
            'Bổ sung khóa chuyến/chặng, chứng từ chi phí và biểu lương có hiệu lực, cùng phạm vi B2B/B2C để đối chiếu trước P&L.']
        result['intake_question']='Bước tiếp theo: anh có thể cung cấp khối lượng theo đơn kèm đơn vị g/kg và chứng từ chi phí để đối chiếu với các DO này không?'
    weight_quality=context.get('weight_quality',{})
    if weight_quality.get('available'):
        result['weight_quality']=weight_quality
        result['totals']['kg']=metrics.get('booking.weight_kg',{}).get('value')
        for key in ('eligible_weight_kg','weight_eligible_ghn','weight_missing_ghn','weight_invalid_ghn',
                    'weight_conflicting_ghn','weight_partial_scope_ghn','weight_missing_id_rows'):
            m=metrics['booking.'+key]
            rows.append({'Chỉ tiêu':m['label'],'Giá trị tính toán':display(m['value'],m['unit']),
                'Đơn vị':m['unit'],'Đánh giá sơ bộ':('Chỉ phần GHN đủ nguồn; không phải tổng kg toàn phạm vi' if key=='eligible_weight_kg'
                    else 'L là tổng kg/GHN; không cộng theo DO, không xác nhận giao thành công')})
        issues[0]['fact']='Booking chưa có đủ doanh thu và chi phí phục vụ ba chặng; sản phẩm J và tổng kg/GHN L là hai chỉ tiêu nguồn độc lập, chưa đối soát tài chính.'
        issues[1]['fact']='GHN có nhiều DO/ngày có thể là các đợt giao. Qty J cộng theo DO trong phạm vi; L là tổng kg cả GHN tính một lần. Không cộng L theo DO hoặc phân bổ tổng L cho một phần kỳ/cutoff. Chưa đủ căn cứ tính kiện/thùng/CBM, xe/chuyến/drop/chi phí/OT/tải hoặc SLA.'
        if not weight_quality.get('complete'):
            issues.append(issue('GHN_WEIGHT_PARTIAL',
                'Tổng kg toàn phạm vi N/A: có GHN thiếu L, L lỗi, nhiều tổng L khác nhau hoặc DO ngoài kỳ/cutoff/thiếu ngày. Subtotal chỉ gồm GHN đủ nguồn; không dùng làm tổng, không chọn first/last/max/sum hay điền xuống L.',
                context.get('weight_issue_sources',refs),
                action='Đối chiếu tổng kg theo từng GHN bị thiếu/lỗi/mâu thuẫn và xác nhận đủ phạm vi các DO bằng nguồn cân/đóng hàng; chưa sửa Google Sheets hoặc tự phân bổ.'))
        result['lead']=('Đã có tổng kg nguồn theo GHN, tính L một lần và tách riêng số sản phẩm J. ' if weight_quality.get('complete') else
            'Đã đọc tổng kg/GHN từ L; tổng kg toàn phạm vi chưa chốt, phần GHN đủ nguồn được trình bày riêng. ')+\
            'Sản phẩm J giữ quy tắc riêng; kiện/thùng, doanh thu và P&L tiếp tục N/A, chưa đối soát tài chính.'
        result['intake_priorities']=[
            'Đối chiếu các GHN có L thiếu/lỗi, nhiều tổng hoặc DO chưa đủ kỳ/cutoff; giữ nguyên dòng và không phân bổ tổng kg cho từng DO.',
            'Đối chiếu độc lập Qty J và khóa GHN+SO+DO; không suy sản phẩm từ kg hoặc dùng lỗi J để loại tổng L đã đủ.',
            'Bổ sung chứng từ chi phí, khóa chuyến/chặng và biểu lương hiệu lực để đối chiếu trước P&L; không suy số kiện/thùng hoặc tải xe từ L.']
        result['intake_question']='Bước tiếp theo: anh có thể đối chiếu các GHN có L thiếu/lỗi hoặc nhiều tổng khác nhau bằng nguồn cân/đóng hàng không?'
    handling=context.get('handling_quality',{})
    if handling.get('available'):
        result['handling_component']=handling
        result['handling_rate_buckets']=context['handling_rate_buckets']
        for key,m in metrics.items():
            if key.startswith('booking.dc_handling_'):
                rows.append({'Chỉ tiêu':m['label'],'Giá trị tính toán':display(m['value'],m['unit']),
                    'Đơn vị':m['unit'],'Đánh giá sơ bộ':('Subtotal đủ điều kiện, không phải tổng toàn component' if 'eligible' in key or '_rate_' in key
                        else 'Tính theo điều khoản owner; chưa nghiệm thu/thanh toán, không phải total cost hoặc P&L')})
        labels={'partial_scope':'GHN thiếu phạm vi ngày/kỳ/cutoff','unknown_routing':'GHN điều kiện phí chưa khớp',
            'mixed_routing':'GHN có các điều kiện phí khác nhau','unknown_rate':'GHN chưa có một giá hiệu lực',
            'mixed_rate':'GHN qua nhiều mức giá E','missing_weight':'GHN thuộc diện phí thiếu tổng L',
            'invalid_weight':'GHN thuộc diện phí có L lỗi','conflicting_weight':'GHN thuộc diện phí có tổng L mâu thuẫn',
            'two_leg_unresolved':'GHN chưa rõ liên kết hai chặng AG/S'}
        unit='đợt GHN/ngày' if handling.get('grain')=='GHN_BOOKING_E_DAY' else 'mã GHN'
        for k,label in labels.items():
            if k not in handling['counters']:continue
            rows.append({'Chỉ tiêu':label,'Giá trị tính toán':display(handling['counters'][k],unit),
                'Đơn vị':unit,'Đánh giá sơ bộ':'Các loại lỗi có thể trùng một nhóm; không cộng các counter để suy số đơn'})
        if handling['missing_id_rows']:
            rows.append({'Chỉ tiêu':'Dòng thiếu GHN cho bốc xếp','Giá trị tính toán':display(handling['missing_id_rows'],'dòng'),
                'Đơn vị':'dòng','Đánh giá sơ bộ':'Chưa thể gán vào một nhóm, không điền chi phí 0'})
        if not handling['complete']:
            issues.append(issue('DC_HANDLING_PARTIAL',
                'Component bốc xếp DC toàn phạm vi N/A: còn nhóm chưa chốt điều kiện phí/giá/phạm vi hoặc tổng L. Subtotal chỉ là phần đủ diện phí. Điều kiện phí độc lập xe vật lý: TP Lai Châu đi thẳng vẫn tính; nhóm không thuộc diện phí không bị ảnh hưởng bởi L lỗi. Không chia kg để chọn giá hoặc tính các phí giao theo điểm/gói.',
                context.get('handling_issue_sources',refs),
                action='Đối chiếu các tổng L thiếu/lỗi/mâu thuẫn hoặc GHN qua nhiều mức giá/routing; chỉ tách kg khi có chứng cứ. Không tự sửa Google hoặc lấy subtotal làm tổng.'))
        result['lead']+=' Bốc xếp DC được tính riêng theo điều khoản đủ diện phí owner, độc lập cơ chế xe; chưa là tiền nghiệm thu/thanh toán hoặc tổng chi phí.'
        result['intake_priorities']=[
            'Đối chiếu các GHN bốc xếp đang giữ riêng: L thiếu/lỗi/mâu thuẫn, nhiều mức giá hoặc routing; không tự phân bổ kg giữa các DO.',
            'Giữ subtotal theo từng mức giá riêng; kiểm bảng kê bốc xếp theo điều khoản, không tự đổi điều kiện phí theo nhãn xe vật lý.',
            'Bổ sung các khoản vận tải/giao/kho khác và doanh thu có căn cứ trước khi tính total cost/GP; chưa tự áp phí theo điểm/gói.']
        result['intake_question']='Bước tiếp theo: anh có thể cung cấp bằng chứng tổng L hoặc kg tách của các GHN bốc xếp đang giữ riêng để đối chiếu không?'
    return result


def _safe_line(value):
    return str(value).replace("|", "/").replace("\n", " ").replace("\r", " ")


def render(result, *, telegram=False, audit_text=""):
    totals = result["totals"]
    lines = ["1. Báo cáo nhanh", "Sản lượng: "+display(totals.get("kg"), "kg")+" kg / "+display(totals.get("pieces"), "kiện")+" kiện.",
        "Tổng doanh thu B2B: "+display(totals.get("revenue"), "VND")+" VND.",
        "GP%: "+display(totals.get("marginPct"), "%")+"%.",
        "Trạng thái: "+result["status"]+" — "+("đủ thành phần theo khai báo" if result["financial_complete"] else "PARTIAL / chưa đủ dữ liệu")+"; "+", ".join(result["modes"])+"; chưa đối soát độc lập.",
        "Phạm vi: "+result["scope"]]
    if result.get("lead"):
        lines.insert(0, result["lead"])
    if 'product_units' in totals:
        lines.insert(2,'Số sản phẩm bên trong thùng: '+display(totals.get('product_units'),'sản phẩm')+' sản phẩm; tách riêng kiện/thùng.')
    if result.get("data_label"):
        label = result["data_label"]
        lines.insert(0, "DỮ LIỆU GIẢ / DEMO — không dùng quyết định thật; UNVERIFIED." if any(marker in label.upper() for marker in ("SYNTHETIC", "DEMO")) else label)
    if result.get("covered_subset", {}).get("population_complete") is False:
        lines.append("Phạm vi B2B có facts cách ly; các mã tính riêng còn lại chỉ là subset, không dùng làm tổng doanh thu/GP.")
    if result.get("trips"):
        lines.append("Chi phí xe dựa lượt ước tính theo tải; chưa chứng minh lượt thực, vòng quay khả thi hoặc chứng từ thanh toán.")
    if "source_provenance" in result:
        from ..telegram_presentation import source_note
        lines.append(source_note({"source_provenance": result["source_provenance"]}))
    lines += ["", "2. Bảng số liệu đã tính"]
    rows = metric_rows(result)
    if telegram:
        lines += [f"• {r['Chỉ tiêu']}: {r['Giá trị tính toán']} {r['Đơn vị']} — {r['Đánh giá sơ bộ']}" for r in rows]
    else:
        lines += ["| Chỉ tiêu | Giá trị tính toán | Đơn vị | Đánh giá sơ bộ |", "|---|---:|---|---|"]
        lines += ["| "+" | ".join(_safe_line(v) for v in r.values())+" |" for r in rows]
    lines += ["", "3. Các điểm CHƯA ĐƯỢC"]
    if not result["issues"]:
        lines.append("Chưa phát hiện vi phạm các kiểm tra đã triển khai trong phạm vi khai báo; chưa thay đối soát chứng từ hoặc xác nhận vận hành.")
    for item in result["issues"]:
        sources = [re.sub(r"#[a-f0-9]{12}", "", ref) for ref in item["sources"][:2]]
        lines += ["["+item["severity"]+"] "+item["fact"],
            "Bằng chứng: "+("; ".join(sources) if sources else "chưa có nguồn cho trường còn thiếu")+(f"; mẫu 2/{len(item['sources'])} tham chiếu, /details xem truy vết" if len(item["sources"]) > 2 else ""),
            "Nguyên nhân: "+item["cause"], "Ảnh hưởng: "+item["impact"]]
        lines += ["Giả thuyết cần kiểm chứng: "+h for h in item["hypotheses"]]
        lines += ["Câu hỏi kiểm chứng: "+q for q in item["verification_questions"]]
        for action in item["actions"]:
            lines.append("Đề xuất: "+action["action"]+" Người làm: "+action["owner"]+". Hạn: "+action["deadline"]+".")
    if audit_text:
        lines.append(audit_text)
    else:
        lines.append("Bộ tính xác định bằng code; phần nhận định chưa được Auditor AI kiểm tra chéo.")
    lines += ["", "4. Đúng 3 việc cần chủ sở hữu chốt"]
    lines += [f"{index}. {action}" for index, action in enumerate(priorities(result), 1)]
    if result.get("intake_question"):
        lines += ["", result["intake_question"]]
    if not telegram:
        headings = {"1. Báo cáo nhanh", "2. Bảng số liệu đã tính", "3. Các điểm CHƯA ĐƯỢC", "4. Đúng 3 việc cần chủ sở hữu chốt"}
        markdown = []
        for line in lines:
            if line in headings:
                markdown += ["", "### "+line, ""]
            elif not line or line.startswith("|") or re.match(r"^[1-3]\. ", line):
                markdown.append(line)
            else:
                markdown.append(line+"  ")
        return "\n".join(markdown).strip()
    return "\n".join(lines)
