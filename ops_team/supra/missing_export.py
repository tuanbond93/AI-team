"""Excel workbook of everything /thieu holds, one row per source row (owner request 29/10/2026).

Local owner file only: it contains GHN/SO/DO and plates so rows can be checked
against the pinned sheets. Never sent to Telegram, a model or Git.
"""
import re
from io import BytesIO

from .booking_vehicles import RAW_HEADERS
from .missing_data import DC_ISSUES, FTL_ISSUES, POINT_ISSUES

TWO_LEG = {
    'raw_not_yet_updated': ('Raw chưa cập nhật tới ngày xuất của đợt', 'Chờ Raw cập nhật'),
    'no_raw_deliver_in_window': ('Giao thẳng GXT chưa có Raw DELIVER trong khoảng đợt', 'Kiểm Raw đã ghi giao chưa / đơn hủy, dời'),
    'raw_plate_missing': ('Raw thiếu biển S', 'Biển S thực chở'),
    'raw_truck_day_data_issue': ('Xe S/ngày có dòng Raw lỗi', 'Sửa dòng Raw'),
    'raw_mapping_flags': ('Raw ghi mô hình/biển AG khác Booking', 'Xác nhận mô hình/biển AG'),
    's_truck_shared_with_other_mode': ('Xe S chở lẫn BC/NCC/mô hình khác', 'Owner xác nhận cách tính'),
    's_truck_route_outside_gxt_policy': ('Xe S chở đơn ngoài chính sách GXT', 'Xác nhận tuyến/chính sách'),
    'mixed_ag_and_other_s_trucks': ('Đơn vừa đi xe AG vừa đi xe S khác', 'Owner xác nhận phần hai chặng'),
    'booking_ag_missing_or_several': ('Booking thiếu/nhiều biển AG trong đợt', 'Biển AG đúng'),
    'booking_mixed_mode': ('Đợt lẫn mô hình giao', 'Xác nhận mô hình giao'),
}
BOOKING_COLUMNS = [('GHN', 'order_id'), ('SO', 'source_so'), ('DO', 'source_do'), ('Ngày xuất (E)', 'dispatch_date'),
                   ('Kg cột L', 'source_ghn_weight'), ('Tải T', 'source_capacity_raw'), ('Biển AG', 'source_booking_plate'),
                   ('Tỉnh', 'source_province'), ('Huyện', 'source_district'), ('Ghi chú 2', 'source_note')]
RAW_COLUMNS = [('Raw: mã đơn', 'order_code'), ('Raw: ngày load', 'load_date'), ('Raw: mã chuyến', 'trip_code'),
               ('Raw: biển S', 'truck_plate_number'), ('Raw: gram', 'weight'), ('Raw: thành công', 'is_succeeded'),
               ('Raw: giờ kết thúc chuyến', 'time_ket_thuc_chuyen')]


def _issue_rows(report):
    """(nhóm, lý do, cần bổ sung, ngày đợt, source refs) for every held item."""
    out = []
    for item in report['dc'].get('group_evidence', []):
        for code in item.get('issues', []):
            if code == 'two_leg_unresolved' and item.get('two_leg_reason'):
                what, send = TWO_LEG.get(item['two_leg_reason'], DC_ISSUES[code])
            else:
                what, send = DC_ISSUES.get(code, (code, ''))
            out.append(('Bốc xếp DC', what, send, item.get('day', ''), item.get('sources', [])))
    for name, held, catalog in (('FTL', report['ftl'].get('held', []), FTL_ISSUES), ('Phí điểm', report['points'].get('held', []), POINT_ISSUES)):
        for item in held:
            for code in str(item.get('reason', '')).split(','):
                what, send = catalog.get(code, (code, ''))
                out.append((name, what, send, '', item.get('sources', [])))
    for item in report['gxt'].get('held', []):
        out.append(('Xe GXT/ngày', 'Xe GXT/ngày chưa tính được', 'Biển S, ngày load, kg chuyến; xe chở lẫn thì owner xác nhận', '', item.get('sources', [])))
    if report.get('unpriced_booking_sources'):
        out.append(('Giá giao', 'Tuyến/cửa hàng chưa có giá theo phạm vi', 'Mô hình giao và giá đã duyệt', '', report['unpriced_booking_sources']))
    overlay = report.get('overlay') or {}
    if overlay.get('missing_s_gxt_sources'):
        out.append(('Biển xe', 'Đơn Supra GXT thiếu biển S trên Raw', 'Biển S thực chở', '', overlay['missing_s_gxt_sources']))
    return out


def checklist_workbook(report, data):
    import pandas as pd
    booking = {r['_source']: r for r in data.rows.to_dict('records')}
    raw = {item['sheet_row']: dict(zip(RAW_HEADERS, item['values'])) for item in data.vehicle_pair['data_supra']['rows']}
    detail = []
    for group, what, send, event_day, refs in _issue_rows(report):
        for ref in dict.fromkeys(refs):
            match = re.search(r'dòng\s+(\d+)', str(ref))
            line = int(match.group(1)) if match else None
            is_raw = str(ref).startswith('Raw#')
            row = {'Nhóm': group, 'Lý do': what, 'Cần bổ sung': send, 'Ngày đợt': event_day,
                   'Tab': 'Raw' if is_raw else 'Booking', 'Dòng Sheet': line}
            source = (raw.get(line) if is_raw else booking.get(ref)) or {}
            for label, key in (RAW_COLUMNS if is_raw else BOOKING_COLUMNS):
                value = source.get(key, '')
                row[label] = '' if value is None else str(value)
            detail.append(row)
    columns = ['Nhóm', 'Lý do', 'Cần bổ sung', 'Ngày đợt', 'Tab', 'Dòng Sheet'] + [c for c, _ in BOOKING_COLUMNS] + [c for c, _ in RAW_COLUMNS]
    frame = pd.DataFrame(detail, columns=columns)
    if frame.empty:
        summary = pd.DataFrame([{'Nhóm': 'Không còn dòng giữ riêng', 'Lý do': '', 'Cần bổ sung': '', 'Số dòng': 0}])
    else:
        summary = (frame.groupby(['Nhóm', 'Lý do', 'Cần bổ sung'], sort=False).size().reset_index(name='Số dòng')
                   .sort_values('Số dòng', ascending=False))
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        summary.to_excel(writer, sheet_name='Tóm tắt', index=False)
        frame.to_excel(writer, sheet_name='Chi tiết', index=False)
        for sheet in writer.sheets.values():
            sheet.freeze_panes = 'A2'
            sheet.auto_filter.ref = sheet.dimensions
            for column in sheet.columns:
                sheet.column_dimensions[column[0].column_letter].width = min(45, max(10, *(len(str(c.value or '')) for c in column[:200])))
    return output.getvalue()
