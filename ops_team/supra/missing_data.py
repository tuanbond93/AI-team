"""Plain-language list of what the owner must supply; reads code results only.

Never changes values or rules. Shows sheet row numbers and dates, not GHN,
store or plate identifiers, so the owner can find the rows in the pinned tab.
"""
import re
from collections import defaultdict

# issue code -> (what is missing, what to send)
DC_ISSUES = {
    'missing_weight': ('Thiếu tổng kg (cột L) của đợt GHN/ngày', 'tổng kg đúng của đợt đó; không lấy kg ngày khác'),
    'invalid_weight': ('Kg cột L sai định dạng', 'số kg đúng (chỉ số, dấu phẩy thập phân)'),
    'conflicting_weight': ('Cùng GHN cùng ngày có nhiều tổng kg khác nhau (cột L)', 'tổng kg đúng, hoặc xác nhận các phần kg được cộng'),
    'unknown_routing': ('Cửa hàng/huyện chưa có trong danh mục điều kiện bốc xếp (cột H/N/O)', 'điều kiện phí của địa bàn/cửa hàng này'),
    'mixed_routing': ('Một đợt có cả dòng tính bốc xếp và dòng đi thẳng', 'xác nhận đợt này tính bốc xếp hay không'),
    'unknown_rate': ('Ngày xuất (cột E) chưa có đơn giá bốc xếp hiệu lực', 'đơn giá áp cho ngày đó'),
    'mixed_rate': ('GHN qua hai mức giá bốc xếp', 'kg tách theo từng ngày/mức giá'),
    'partial_scope': ('GHN có dòng ngoài kỳ hoặc thiếu ngày xuất', 'ngày xuất (cột E) đúng của các dòng'),
    'two_leg_unresolved': ('Chưa khớp được xe Raw (S) với xe Booking (AG) cùng ngày', 'biển S thực chở hoặc xác nhận giao thẳng/hai chặng'),
}
FTL_ISSUES = {
    'unpriced_class': ('Loại tải Booking (cột T) chưa có giá FTL', 'giá FTL cho loại tải này'),
    'mixed_or_unknown_class': ('Cùng xe/ngày có cột T trống hoặc nhiều loại tải', 'loại tải đúng hoặc số lượt theo từng loại'),
    'missing_or_invalid_class': ('Cột T trống hoặc sai', 'loại tải Booking đúng'),
    'missing_key': ('Thiếu ngày, biển AG hoặc mã GHN', 'giá trị còn trống'),
    'missing_or_conflicting_weight': ('Kg cột L trống hoặc mâu thuẫn', 'tổng kg đúng của GHN'),
    'invalid_weight': ('Kg cột L sai định dạng', 'số kg đúng'),
    'partial_or_conflicting_scope': ('GHN nằm trên nhiều xe/ngày', 'xác nhận GHN đi xe/ngày nào'),
    'ambiguous_route': ('Tuyến chưa rõ thuộc FTL', 'xác nhận mô hình giao của tuyến'),
    'above_capacity_no_trip_algorithm': ('Kg vượt tải nhưng chưa có cách tính số chuyến', 'số chuyến thực tế hoặc quy tắc chia chuyến'),
}
POINT_ISSUES = {
    'missing_date_or_store': ('Thiếu ngày xuất hoặc mã cửa hàng', 'giá trị còn trống'),
    'ambiguous_route_or_rate': ('Cửa hàng/ngày có nhiều loại phí điểm hoặc chưa có giá', 'loại phí điểm đúng'),
    'unscoped_missing_booking_date': ('Thiếu ngày xuất (cột E)', 'ngày xuất đúng'),
}
MAX_ITEMS = 10
MAX_ROWS = 6


def _row(ref):
    """'Prefix#hash dòng 12' -> ('Raw' or 'Booking', 12); unknown formats stay hidden."""
    match = re.search(r'dòng\s+(\d+)', str(ref))
    if not match:
        return None
    return ('Raw' if str(ref).startswith('Raw#') else 'Booking', int(match.group(1)))


def _rows_text(refs):
    rows = defaultdict(set)
    for ref in refs:
        parsed = _row(ref)
        if parsed:
            rows[parsed[0]].add(parsed[1])
    parts = []
    for tab in sorted(rows):
        numbers = sorted(rows[tab])
        shown = ', '.join(str(n) for n in numbers[:MAX_ROWS])
        more = f' (+{len(numbers)-MAX_ROWS} dòng)' if len(numbers) > MAX_ROWS else ''
        parts.append(f'{tab} dòng {shown}{more}')
    return '; '.join(parts) or 'xem /details audit'


def _vi_day(value):
    y, m, d = str(value).split('-')
    return f'{d}/{m}/{y}'


def _collect(items, held, catalog, key='reason'):
    groups = defaultdict(lambda: {'days': set(), 'sources': []})
    for entry in held:
        codes = entry.get('issues') or str(entry.get(key, '')).split(',')
        for code in codes:
            if code in catalog:
                g = groups[code]
                if entry.get('day'):
                    g['days'].add(entry['day'])
                g['sources'] += entry.get('sources', [])
    for code, g in groups.items():
        what, send = catalog[code]
        days = sorted(g['days'])
        when = ('ngày ' + ', '.join(_vi_day(d) for d in days[:4]) + (' …' if len(days) > 4 else '') + ': ') if days else ''
        items.append(f'• {what} — {when}{_rows_text(g["sources"])}. Cần: {send}.')


def missing_checklist(report):
    """Return (count_of_groups, text). count 0 means nothing held in this scope."""
    if not report.get('available'):
        return None, 'Chưa lập được danh sách: chính sách chi phí hoặc nguồn chưa được nạp và đối chiếu đúng kỳ.'
    items = []
    _collect(items, report['dc'].get('group_evidence', []), DC_ISSUES)
    _collect(items, report['ftl'].get('held', []), FTL_ISSUES)
    _collect(items, report['points'].get('held', []), POINT_ISSUES)
    if report['unpriced_booking_sources']:
        items.append(f'• Tuyến/cửa hàng chưa có giá giao theo phạm vi — {_rows_text(report["unpriced_booking_sources"])}. Cần: mô hình giao và giá đã duyệt.')
    for route in report.get('unpriced_ncc_routes', []):
        items.append(f'• Cước NCC chưa có cho {route["district"]} ({route["orders"]} GHN). Cần: biểu giá NCC hoặc nhật trình km.')
    g = report['gxt']
    if g.get('held'):
        labels = {'direct_vehicle_mismatch': 'giao thẳng chưa khớp xe S với AG', 'raw_group_quality': 'Raw lỗi (thiếu chuyến, kg hoặc trùng dòng)',
                  'mixed_or_unknown_role': 'xe chở lẫn đơn BC/FTL/không rõ mô hình', 'duplicate_order_or_unconfirmed_route_or_weight': 'đơn chưa rõ tuyến/kg',
                  'source_weight_mismatch': 'kg Supra không khớp tổng xe', 'opening_ftl_shared_vehicle_day': 'đi chung đơn khai trương',
                  'multiple_booking_vehicles': 'một GHN nhiều xe AG', 'missing_booking_vehicle': 'thiếu biển AG',
                  'unknown_or_conflicting_mode': 'mô hình giao chưa rõ'}
        why = ', '.join(f'{n} nhóm {labels.get(k, k)}' for k, n in sorted(g.get('issue_counts', {}).items(), key=lambda x: -x[1]) if n)
        items.append(f'• {len(g["held"])} xe GXT/ngày chưa tính được' + (f' ({why})' if why else '') +
                     f' — {_rows_text(sum((h.get("sources", []) for h in g["held"]), []))}. Cần: biển S, ngày load, kg chuyến; xe chở lẫn BC/GXT thì owner xác nhận cách tính.')
    quality = g.get('unassigned_quality') or {}
    overlay = report.get('overlay') or {}
    if quality.get('missing_plate') and not overlay.get('missing_s_gxt_sources'):
        items.append(f'• {quality["missing_plate"]} dòng Raw DELIVER thiếu/sai biển xe. Cần: biển S thực chở; không suy từ biển AG.')
    if quality.get('representative'):
        items.append(f'• {quality["representative"]} dòng Raw DELIVER dùng biển đại diện, chưa biết xe thật. Cần: biển S thực chở.')
    if overlay.get('missing_s_gxt_sources'):
        total = f' Toàn Raw có {quality["missing_plate"]} dòng DELIVER thiếu biển, gồm cả đơn không thuộc chi phí GXT Supra.' if quality.get('missing_plate') else ''
        items.append(f'• Đơn Supra GXT thiếu biển S trên Raw — {_rows_text(overlay["missing_s_gxt_sources"])}. Cần: biển S thực chở; không suy từ biển AG.{total}')
    if report.get('opening_unpriced_events'):
        items.append(f'• {report["opening_unpriced_events"]} đợt khai trương (FTL theo loại tải Booking) chưa có km thực tế và đơn giá. Cần: nhật trình km và giá FTL đã duyệt cho đơn đó.')
    remote = report.get('remote')
    if remote and remote.get('km_extra_vnd') is None:
        items.append('• Phí km NCC vùng xa chưa có. Cần: nhật trình km và đơn giá km theo hợp đồng.')
    start, end = report['scope']['start'], report['scope']['end']
    span = _vi_day(start) + ('' if start == end else '–' + _vi_day(end))
    if not items:
        return 0, (f'Dữ liệu {span}: không còn nhóm nào bị giữ riêng trong các khoản đã có quy tắc. '
                   'Tổng chi phí/lãi lỗ vẫn chưa chốt vì VAT, phụ phí và các khoản chưa có quy tắc chưa tính.')
    head = (f'CẦN ANH BỔ SUNG — {span}: {len(items)} nhóm dữ liệu. Bổ sung xong em tính lại; trong lúc chờ các khoản này giữ N/A, không tính là 0.')
    shown = items[:MAX_ITEMS]
    tail = [f'Còn {len(items)-MAX_ITEMS} nhóm nữa; /details audit xem đủ.'] if len(items) > MAX_ITEMS else []
    return len(items), '\n'.join([head, *shown, *tail, 'Số dòng là dòng trong tab Google Sheets đã ghim; sửa nguồn xong cần xuất lại đúng tab.'])


def booking_checklist(context):
    """Fallback for a booking-only source without an approved cost policy."""
    items = []
    weight = context.get('weight_quality', {})
    if weight.get('available') and not weight.get('complete') and context.get('weight_issue_sources'):
        items.append(f'• Kg cột L thiếu, lỗi hoặc mâu thuẫn — {_rows_text(context["weight_issue_sources"])}. Cần: tổng kg đúng của từng GHN.')
    handling = context.get('handling_quality', {})
    if handling.get('available') and not handling.get('complete') and context.get('handling_issue_sources'):
        items.append(f'• Bốc xếp DC chưa chốt — {_rows_text(context["handling_issue_sources"])}. Cần: kg, ngày xuất hoặc điều khoản phí của các dòng này.')
    quantity = context.get('quantity_quality', {})
    if quantity.get('available') and not quantity.get('complete'):
        items.append('• Số sản phẩm (cột J) hoặc mã GHN/SO/DO thiếu, lỗi hoặc lặp. Cần: sửa các dòng đó trong nguồn; /details xem số dòng.')
    if not items:
        return 0, 'Chưa thấy dòng thiếu trong các chỉ số đã có quy tắc. Chi phí theo xe/điểm/FTL cần chính sách chi phí đã duyệt mới lập được danh sách.'
    return len(items), '\n'.join([f'CẦN ANH BỔ SUNG: {len(items)} nhóm dữ liệu. Trong lúc chờ các khoản này giữ N/A, không tính là 0.', *items,
        'Số dòng là dòng trong tab Google Sheets đã ghim; sửa nguồn xong cần xuất lại đúng tab.'])
