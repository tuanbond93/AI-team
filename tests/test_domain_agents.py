import json
from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest

from ops_team.kpi_targets import KpiStore
from ops_team.supra import domain_agents as agents
from ops_team.supra.manual_cost_rules import gxt_daily_cost
from ops_team.supra.ncc_invoice import load_statement, money, parse_statement, reconcile, save_statement
from ops_team.telegram_state import BusinessState

SCOPE = {'start': '2026-10-10', 'end': '2026-10-12'}
TIERS = [{'upper_bound': '50', 'inclusive': True, 'formula': {'kind': 'FIXED', 'amount_vnd': '5000'}},
         {'upper_bound': None, 'inclusive': False, 'formula': {'kind': 'FIXED', 'amount_vnd': '9000'}}]


def gxt_report(**extra):
    groups = [dict(day='2026-10-10', vehicle='29C11111', supra_orders={'A': '20', 'B': '60'}, other_kg='20', valid=True, sources=['r1']),
              dict(day='2026-10-11', vehicle='29C22222', supra_orders={'C': '10'}, other_kg='0', valid=True, sources=['r2'])]
    gxt = gxt_daily_cost(groups, '1000000', TIERS)
    report = {'available': True, 'gxt': gxt, 'ftl': {'held': []}, 'points': {'held': [], 'counts': {'1T': 3}, 'eligible_vnd': '150000'},
              'dc': {}, 'dc_overlap_unknown_sources': [], 'upstream': {}, 'warehouse': {}, 'unpriced_booking_sources': [],
              'unpriced_ncc_routes': [], 'overlay': {'available': False}}
    report.update(extra)
    return report


EVIDENCE = {'raw_last_day': '2026-10-14', 'groups': [
    {'day': '2026-10-10', 'plate': '29C11111', 'supra_kg': '80', 'other_kg': '20', 'issues': [], 'vehicle_alias': 'V1'},
    {'day': '2026-10-11', 'plate': '29C22222', 'supra_kg': '10', 'other_kg': '0', 'issues': [], 'vehicle_alias': 'V2'},
    {'day': '2026-10-12', 'plate': '29C11111', 'supra_kg': '5', 'other_kg': '0', 'issues': [], 'vehicle_alias': 'V1'}],
    'inventory': [{'plate': '29C22222', 'alias': 'V2', 'booking_declared_capacity_kg': ['1250']}]}


def test_gxt_breakdown_sums_to_totals():
    report = gxt_report()
    by = report['gxt']['by_group']
    assert [g['orders'] for g in by] == [2, 1]
    assert sum(Decimal(g['wage_vnd']) for g in by) == Decimal(report['gxt']['wage_supra_vnd'])
    assert sum(Decimal(g['vehicle_supra_vnd']) for g in by) == Decimal(report['gxt']['vehicle_supra_vnd'])
    assert Decimal(by[0]['vehicle_supra_vnd']) == Decimal('800000')  # 80 of 100 kg


def test_workforce_lists_wage_per_truck_day_and_never_per_person():
    text = agents.workforce(gxt_report(), SCOPE, evidence=EVIDENCE)
    assert 'Lương giao GXT 10/10–12/10' in text and '3 đơn' in text and 'V1 10/10: 2 đơn' in text and '29C' not in text
    assert 'BC: 3 điểm' in text and 'từng người' in text
    held = gxt_report()
    held['gxt']['held'] = [{'reason': 'x', 'sources': ['r9']}]
    assert '1 nhóm xe/ngày chưa tính lương (N/A)' in agents.workforce(held, SCOPE)
    assert 'Chưa tính được' in agents.workforce({'available': False}, SCOPE)


def test_audit_recomputes_and_catches_a_broken_sum():
    passed, total, text = agents.audit(gxt_report(), EVIDENCE, {'batch_stamp': None}, SCOPE)
    assert passed == total and 'đạt' in text
    broken = gxt_report()
    broken['gxt']['wage_supra_vnd'] = str(Decimal(broken['gxt']['wage_supra_vnd'])+1)
    passed, total, text = agents.audit(broken, EVIDENCE, {'batch_stamp': None}, SCOPE)
    assert passed == total-1 and 'Lương giao = tổng từng xe/ngày' in text
    stale = dict(EVIDENCE, raw_last_day='2026-10-11')
    assert 'Raw mới tới 2026-10-11' in agents.audit(gxt_report(), stale, None, SCOPE)[2]


def test_kpi_uses_owner_targets_and_keeps_na(tmp_path):
    state = BusinessState(tmp_path/'v')
    try:
        store = KpiStore(state)
        store.set_target('sla', '95', 'telegram:1')
        store.set_target('sla', '90%', 'telegram:2')  # supersedes
        store.set_target('tai', '80', 'telegram:3')
        assert store.targets()['sla']['value'] == Decimal('90') and store.targets()['sla']['source'] == 'telegram:2'
        plate, kg = store.set_capacity('29C-111.11', '1.25t', 'telegram:4')
        assert (plate, kg) == ('29C11111', Decimal('1250'))
        assert store.set_capacity('29C22222', '1.250', 'x')[1] == Decimal('1250')
        for bad in (('sla', '120'), ('abc', '1'), ('tai', '-3')):
            with pytest.raises(ValueError):
                store.set_target(*bad, 'x')
        text = agents.kpi(store.targets(), SCOPE, {'on_time_rate': 0.92}, gxt_report(), {'weight_kg': None}, 3, EVIDENCE, store.capacities())
        assert 'SLA đúng hạn: 92,0% — đạt' in text
        assert 'Chi phí mỗi kg: N/A' in text and 'Chi phí mỗi đơn: N/A' in text  # total not complete -> never a partial KPI
        # load = (100 + 10 + 5) kg over 3 truck-days × 1250 kg capacity
        assert 'Tỷ lệ tải xe: 3,1% — CHƯA ĐẠT' in text and 'chưa phải cân thực tế' in text and '29C' not in text
        no_cap = agents.kpi({}, SCOPE, None, gxt_report(), None, 0, EVIDENCE, {})
        assert 'Tỷ lệ tải xe: 0,8%' in no_cap and '29C' not in no_cap  # V2 has a Booking T capacity; V1 is listed as missing below
        none = agents.kpi({}, SCOPE, None, gxt_report(), None, 0, {**EVIDENCE, 'inventory': []}, {})
        assert 'chưa có tải trọng xe V1, V2' in none and '29C' not in none
    finally:
        state.close()


def test_kpi_load_factor_needs_a_capacity():
    load, counted, missing = agents.load_factor(EVIDENCE, SCOPE, {})
    assert counted == 1 and missing == ['29C11111'] and load == Decimal(10)/Decimal(1250)*100


def statement_xlsx(rows):
    from openpyxl import Workbook
    book = Workbook()
    sheet = book.active
    sheet.append(['CÔNG TY VẬN TẢI X — BẢNG KÊ THÁNG 10/2026'])
    sheet.append([])
    sheet.append(['STT', 'Ngày vận chuyển', 'Biển số xe', 'Số chuyến', 'Thành tiền (chưa VAT)', 'Thuế GTGT (8%)', 'Thành tiền sau VAT', 'Ghi chú'])
    for row in rows:
        sheet.append(row)
    out = BytesIO()
    book.save(out)
    return out.getvalue()


def test_ncc_statement_parse_store_and_reconcile(tmp_path):
    data = statement_xlsx([
        [1, '10/10/2026', '29C-111.11', 1, '1.000.000', 80000, 1080000, ''],
        [2, '11/10/2026', '29C-222.22', 1, 1000000, 90000, 1090000, 'VAT sai'],
        [3, '11/10/2026', '29C-222.22', 1, 1000000, 80000, 1080000, 'trùng'],
        [4, '12/10/2026', '29C-999.99', 1, 500000, 40000, 540000, ''],
        [5, '13/10/2026', '', 1, 500000, 40000, 540000, 'thiếu biển'],
        ['Tổng cộng', None, None, None, 4000000, None, None, None]])
    statement = parse_statement(data, 'bang_ke.xlsx')
    assert len(statement['lines']) == 4 and statement['held'] == [{'row': 8, 'missing': ['plate']}]
    assert statement['lines'][0]['amount_vnd'] == Decimal('1000000') and statement['lines'][0]['day'] == date(2026, 10, 10)
    state = BusinessState(tmp_path/'v')
    try:
        assert save_statement(state, statement) == ['2026-10']
        assert b'29C' not in (state.root/'ncc'/'2026-10.dpapi').read_bytes()  # encrypted at rest
        loaded = load_statement(state, '2026-10')
        assert loaded['lines'][1]['vat_vnd'] == Decimal('90000') and load_statement(state, '2026-09') is None
    finally:
        state.close()
    r = reconcile(loaded, gxt_report(), EVIDENCE, '2026-10-10', '2026-10-12')
    assert r['vat_mismatch_rows'] == [5] and r['duplicate_rows'] == [(5, 6)]
    assert r['unknown_plates'] == ['29C99999'] and r['activity_not_billed'] == [('29C11111', '2026-10-12')]
    assert {x['plate']: x['diff_vnd'] for x in r['gxt_compare']} == {'29C11111': Decimal(0), '29C22222': Decimal('1000000')}
    text = agents.accounting(gxt_report(), SCOPE, loaded, r, EVIDENCE)
    assert 'VAT không đúng 8% ở dòng 5' in text and 'trùng biển số + ngày' in text and 'không có trong Booking/Raw ở bảng kê dòng 7' in text
    assert 'xe V2 (bảng kê dòng 5, 6)' in text and 'lệch 1.000.000đ' in text and 'V1 12/10' in text
    assert 'dòng thiếu biển/ngày/tiền chưa đọc được: 8' in text and '29C' not in text  # rows and V-codes, never plates


def test_ncc_layout_must_be_recognisable():
    with pytest.raises(ValueError):
        parse_statement(b'a,b,c\n1,2,3\n', 'x.csv')
    assert money('1.250.000') == Decimal('1250000') and money('1250000.5') == Decimal('1250000.5') and money('abc') is None


def test_accounting_without_statement_explains_what_is_missing():
    text = agents.accounting(gxt_report(), SCOPE)
    assert 'N/A' in text and 'chưa có chính sách doanh thu' in text and '/import ncc' in text


def test_responder_domain_commands_routing_and_guards(tmp_path, monkeypatch):
    from ops_team.telegram_assistant import AssistantResponder
    from ops_team.telegram_bridge import Incoming
    from ops_team.telegram_state import GROUP_ID
    state = BusinessState(tmp_path/'vault')
    try:
        r = AssistantResponder(state, {'owner_id': 123, 'models': {}})
        seen = []
        monkeypatch.setattr(r, '_domain', lambda command, text: seen.append((command, text)) or f'{command} ok')
        assert r.answer(Incoming(GROUP_ID, 123, 1, None, 'kpi', '')) == 'kpi ok'
        assert 'Chỉ owner' in r.answer(Incoming(GROUP_ID, 456, 2, None, 'nhansu', ''))
        r.answer(Incoming(GROUP_ID, 123, 3, None, 'ask', 'lương giao từ 10/10/2026 đến 12/10/2026'))
        assert seen[-1] == ('nhansu', '2026-10-10 2026-10-12')
        r.answer(Incoming(GROUP_ID, 123, 4, None, 'ask', 'tỷ lệ tải xe tháng 10/2026'))
        assert seen[-1][0] == 'kpi'
        guard = r.answer(Incoming(GROUP_ID, 123, 5, None, 'ask', 'đặt mục tiêu SLA 95 nhé'))
        assert '/muctieu' in guard and KpiStore(state).targets() == {}
        assert 'Đã lưu mục tiêu SLA đúng hạn: ≥ 95%' in r.answer(Incoming(GROUP_ID, 123, 6, None, 'muctieu', 'sla 95'))
        assert 'Chỉ owner' in r.answer(Incoming(GROUP_ID, 456, 7, None, 'muctieu', 'sla 50'))
        assert KpiStore(state).targets()['sla']['value'] == Decimal(95)
        assert '1250 kg' in r.answer(Incoming(GROUP_ID, 123, 8, None, 'taitrong', '29C11111 1.25t'))
    finally:
        state.close()


def test_import_ncc_by_telegram_document(tmp_path):
    from ops_team.telegram_assistant import AssistantResponder
    from ops_team.telegram_bridge import Incoming
    from ops_team.telegram_state import GROUP_ID
    data = statement_xlsx([[1, '10/10/2026', '29C-111.11', 1, 1000000, 80000, 1080000, '']])

    class Api:
        def call(self, method, **kw):
            return {'file_path': 'x'}

        def download(self, path):
            return data
    state = BusinessState(tmp_path/'vault')
    try:
        r = AssistantResponder(state, {'owner_id': 123, 'models': {}})
        doc = {'file_name': 'bang_ke.xlsx', 'file_size': len(data), 'file_id': 'f'}
        reply = r.document(Incoming(GROUP_ID, 123, 1, None, 'import', 'ncc'), doc, Api())
        assert '1 dòng hợp lệ' in reply and '10/2026' in reply and load_statement(state, '2026-10')
        assert 'Chỉ owner' in r.document(Incoming(GROUP_ID, 456, 2, None, 'import', 'ncc'), doc, Api())
    finally:
        state.close()


def _ops_data():
    from tests.test_booking_vehicles import book, parsed
    from ops_team.supra.booking_vehicles import RAW_HEADERS
    def b(order, when, province='Phú Thọ'):
        row = book(order, date=when, do='DO_'+order)
        row[9] = province
        return row
    def delivered(order, load, moment):
        r = dict(load_date=load, trip_code=1, order_code=order, weight='1000', type='DELIVER', truck_plate_number='99B12345',
                 is_succeeded='TRUE', time_ket_thuc_chuyen=moment, client_type='B2C', to_province='Phú Thọ', to_district='District 0')
        return [r.get(k, '') for k in RAW_HEADERS]
    books = [b('SYN_ON_TIME', '10/10/2026'), b('SYN_OVERDUE', '10/10/2026'), b('SYN_PENDING', '28/10/2026'),
             b('SYN_NO_RULE', '12/10/2026', 'Province Synthetic'), b('SYN_LATE', '12/10/2026')]
    raws = [delivered('SYN_ON_TIME', '2026-10-10', '2026-10-11 09:00:00'), delivered('SYN_LATE', '2026-10-12', '2026-10-15 09:00:00')]
    return parsed(books, raws)


def test_operations_volume_and_backlog_full_equals_halves():
    from collections import Counter
    from datetime import datetime
    from tests.test_booking_vehicles import CUTOFF
    from ops_team.supra.sla import classify_events, TZ
    data = _ops_data()
    now = datetime(2026, 10, 29, 10, 0, tzinfo=TZ)
    full = {'start': '2026-10-10', 'end': '2026-10-28'}
    halves = [{'start': '2026-10-10', 'end': '2026-10-19'}, {'start': '2026-10-20', 'end': '2026-10-28'}]
    status = lambda sc: Counter(e['status'] for e in classify_events(data, sc['start'], sc['end'], now)[0])
    whole = status(full)
    assert whole == Counter(on_time=1, late=1, overdue=1, pending=1, no_rule_open=1)
    assert sum((status(h) for h in halves), Counter()) == whole  # additive by E day
    # Independent count of events: distinct (GHN, E day) in range from the raw Booking rows.
    events = {(r['order_id'], str(r['dispatch_date'])) for r in data.rows.to_dict('records') if '2026-10-10' <= str(r['dispatch_date']) <= '2026-10-28'}
    text = agents.operations(data, full, now, '2026-10', CUTOFF)
    assert text.startswith(f'Vận hành 10/10–28/10: {len(events)} đợt xuất, tồn đọng 3 đợt (1 quá hạn, 1 chưa tới hạn, 1 ở tỉnh chưa có hạn SLA).')
    assert 'Sản phẩm: 50' in text and 'kg cả khoảng: 12,5 kg' in text and '• 10/10: 2 đợt, 20 sản phẩm' in text
    assert 'Quá hạn chưa có giao thành công trên Raw:' in text and 'xuất 10/10' in text
    assert 'SYN_' not in text  # rows only, never order codes
    parts = [agents.operations(data, h, now, '2026-10', CUTOFF) for h in halves]
    assert parts[0].startswith('Vận hành 10/10–19/10: 4 đợt xuất, tồn đọng 2 đợt') and parts[1].startswith('Vận hành 20/10–28/10: 1 đợt xuất, tồn đọng 1 đợt')


def test_operations_missing_quantity_is_na_not_zero():
    from datetime import datetime
    from tests.test_booking_vehicles import CUTOFF, book, parsed
    from ops_team.supra.sla import TZ
    blank = book('SYN_BLANK', date='10/10/2026', do='DO_X')
    blank[7] = ''   # cột J trống
    blank[8] = ''   # cột L trống
    data = parsed([blank, book('SYN_OK', date='10/10/2026', do='DO_Y')], [])
    text = agents.operations(data, {'start': '2026-10-10', 'end': '2026-10-10'}, datetime(2026, 10, 29, tzinfo=TZ), '2026-10', CUTOFF)
    assert 'Sản phẩm: N/A' in text and 'kg cả khoảng: N/A' in text and 'sản phẩm N/A' in text
    assert 'Sản phẩm: 0' not in text and 'Sản phẩm: 10' not in text  # a partial sum is not shown as the total


def test_operations_is_owner_only_and_routed():
    from ops_team.intent import route
    from datetime import date
    today = date(2026, 11, 5)
    assert route('tồn đọng tuần này', today) == ('vanhanh', '2026-11-02 2026-11-05')
    assert route('sản lượng hôm qua', today)[0] == 'vanhanh' and route('khối lượng hôm qua bao nhiêu', today)[0] == 'vanhanh'
    assert route('đơn chưa giao quá hạn', today)[0] == 'vanhanh' and route('SLA hôm qua', today)[0] == 'sla'


def test_vanhanh_command_owner_only(tmp_path, monkeypatch):
    from ops_team.telegram_assistant import AssistantResponder
    from ops_team.telegram_bridge import Incoming
    from ops_team.telegram_state import GROUP_ID
    state = BusinessState(tmp_path/'vault')
    try:
        r = AssistantResponder(state, {'owner_id': 123, 'models': {}})
        monkeypatch.setattr(r, '_domain', lambda command, text: f'{command} {text}')
        assert r.answer(Incoming(GROUP_ID, 123, 1, None, 'vanhanh', '2026-10-10 2026-10-12')) == 'vanhanh 2026-10-10 2026-10-12'
        assert 'Chỉ owner' in r.answer(Incoming(GROUP_ID, 456, 2, None, 'vanhanh', ''))
        assert 'owner' in r.answer(Incoming(GROUP_ID, 456, 3, None, 'ask', 'tồn đọng tuần này'))
    finally:
        state.close()


MISSING_TEXT = '\n'.join([
    'CẦN ANH BỔ SUNG — 10/10/2026–12/10/2026: 3 nhóm dữ liệu. Bổ sung xong em tính lại.',
    '• Loại tải Booking (cột T) chưa có giá FTL — Booking dòng 4. Cần: giá FTL cho loại tải này.',
    '• Thiếu kg — Booking dòng 9. Cần: CS bổ sung cột L.',
    '• Raw thiếu biển S — Raw dòng 33. Cần: biển S thực chở.'])


def test_daily_brief_ranks_actions_with_suggested_owner_and_data_needs():
    from datetime import datetime
    from tests.test_booking_vehicles import CUTOFF
    from ops_team.supra.sla import TZ
    data = _ops_data()
    now = datetime(2026, 10, 29, 10, 0, tzinfo=TZ)
    scope = {'start': '2026-10-10', 'end': '2026-10-28'}
    ops = agents.operations_stats(data, scope, now, '2026-10', CUTOFF)
    targets = {'sla': {'value': Decimal(95)}}
    kpi_result = agents.kpi_rows(targets, scope, {'on_time_rate': 0.5}, gxt_report(), None, 0, EVIDENCE, {})
    checks = [('Raw đã cập nhật tới cuối khoảng xem', False, 'Raw mới tới 2026-10-14'), ('x', True, '')]
    text = agents.daily_brief(scope, gxt_report(), (3, MISSING_TEXT), ops, kpi_result, checks, False, [('Chi phí', 'mới tính được phần đủ nguồn')])
    lines = text.split('\n')
    assert lines[0] == 'Báo cáo ngày 10/10–28/10: 6 việc cần xử lý; tổng chi phí chưa chốt (chưa có chính sách doanh thu/phân bổ); tồn đọng 3 đợt (1 quá hạn).'
    assert '1. Quyết/duyệt 1 nhóm dữ liệu đang giữ N/A (chặn tổng chi phí). Phụ trách: owner.' in lines
    assert 'Bổ sung 1 nhóm dữ liệu đang giữ N/A (chặn tổng chi phí). Phụ trách: CS (Booking).' in text
    assert 'Phụ trách: điều phối GXT (Raw).' in text and 'Kiểm tra 1 đợt quá hạn chưa có giao thành công. Phụ trách: điều phối GXT.' in text
    assert 'KPI chưa đạt: SLA đúng hạn 50,0% (mục tiêu ≥ 95%). Phụ trách: owner.' in text
    assert 'Kiểm toán chưa đạt 1 phép. Phụ trách: owner.' in text and 'Raw mới tới 2026-10-14' in text
    assert 'Bảng kê NCC tháng này' in text and '— kế toán' in text and 'Mục tiêu KPI' not in text  # a target exists
    assert 'Tải trọng của 1 xe chưa có' in text and '(J8)' in text
    assert 'SYN_' not in text and '29C' not in text


def test_daily_brief_overdue_full_equals_halves_and_no_action_when_clean():
    from datetime import datetime
    from tests.test_booking_vehicles import CUTOFF
    from ops_team.supra.sla import TZ
    data = _ops_data()
    now = datetime(2026, 10, 29, 10, 0, tzinfo=TZ)
    stats = lambda a, b: agents.operations_stats(data, {'start': a, 'end': b}, now, '2026-10', CUTOFF)
    whole, first, second = stats('2026-10-10', '2026-10-28'), stats('2026-10-10', '2026-10-19'), stats('2026-10-20', '2026-10-28')
    assert whole['status']['overdue'] == first['status']['overdue']+second['status']['overdue']
    assert whole['open'] == first['open']+second['open'] and len(whole['events']) == len(first['events'])+len(second['events'])
    clean = agents.daily_brief({'start': '2026-10-20', 'end': '2026-10-28'}, None, (0, ''), second, ([], {}), [('x', True, '')], True, [])
    assert clean.startswith('Báo cáo ngày 20/10–28/10: 0 việc cần xử lý; tổng chi phí chưa chốt (chưa tính được bảng chi phí); tồn đọng 1 đợt (0 quá hạn).')
    assert 'Việc ưu tiên' not in clean and 'Bảng kê NCC' not in clean


def test_tongquan_is_owner_only(tmp_path, monkeypatch):
    from ops_team.telegram_assistant import AssistantResponder
    from ops_team.telegram_bridge import Incoming
    from ops_team.telegram_state import GROUP_ID
    state = BusinessState(tmp_path/'vault')
    try:
        r = AssistantResponder(state, {'owner_id': 123, 'models': {}})
        monkeypatch.setattr(r, '_domain', lambda command, text: 'brief')
        assert r.answer(Incoming(GROUP_ID, 123, 1, None, 'tongquan', '')) == 'brief'
        assert 'Chỉ owner' in r.answer(Incoming(GROUP_ID, 456, 2, None, 'tongquan', ''))
    finally:
        state.close()


def test_kpi_trend_and_alert():
    assert agents.previous_scope({'start': '2026-10-16', 'end': '2026-10-20'}) == {'start': '2026-10-11', 'end': '2026-10-15'}
    assert agents.previous_scope({'start': '2026-10-10', 'end': '2026-10-10'}) == {'start': '2026-10-09', 'end': '2026-10-09'}
    targets = {'sla': {'value': Decimal(95)}}
    prev_rows = agents.kpi_rows(targets, SCOPE, {'on_time_rate': 0.9}, gxt_report(), None, 0, EVIDENCE, {})[0]
    text = agents.kpi(targets, SCOPE, {'on_time_rate': 0.8}, gxt_report(), None, 0, EVIDENCE, {},
                      previous={'scope': {'start': '2026-10-07', 'end': '2026-10-09'}, 'rows': prev_rows})
    lines = text.split('\n')
    assert lines[1] == 'Cảnh báo: SLA đúng hạn 80,0%, mục tiêu ≥ 95%; SLA đúng hạn giảm 10,0 điểm % so với kỳ trước.'
    assert '• SLA đúng hạn: 80,0% — CHƯA ĐẠT (mục tiêu ≥ 95%); so với kỳ trước giảm 10,0 điểm %' in text
    assert 'so với kỳ trước' not in next(l for l in lines if l.startswith('• Chi phí mỗi kg'))  # N/A on both sides: no trend
    assert 'Kỳ trước để so sánh: 07/10–09/10 (cùng số ngày, J7)' in text
    # J6: a KPI that still meets its target but got worse versus the previous window is also a warning.
    worse = agents.kpi(targets, SCOPE, {'on_time_rate': 0.96}, gxt_report(), None, 0, EVIDENCE, {},
                       previous={'scope': {'start': '2026-10-07', 'end': '2026-10-09'},
                                 'rows': agents.kpi_rows(targets, SCOPE, {'on_time_rate': 0.99}, gxt_report(), None, 0, EVIDENCE, {})[0]})
    assert worse.split('\n')[1] == 'Cảnh báo: SLA đúng hạn giảm 3,0 điểm % so với kỳ trước.' and '— đạt' in worse
    better = agents.kpi(targets, SCOPE, {'on_time_rate': 0.99}, gxt_report(), None, 0, EVIDENCE, {},
                        previous={'scope': {'start': '2026-10-07', 'end': '2026-10-09'},
                                  'rows': agents.kpi_rows(targets, SCOPE, {'on_time_rate': 0.96}, gxt_report(), None, 0, EVIDENCE, {})[0]})
    assert 'Cảnh báo' not in better and 'tăng 3,0 điểm %' in better
    na_prev = agents.kpi(targets, SCOPE, {'on_time_rate': 0.8}, gxt_report(), None, 0, EVIDENCE, {},
                         previous={'scope': {'start': '2026-09-28', 'end': '2026-09-30'}, 'reason': 'kỳ trước nằm ngoài tháng/kỳ dữ liệu đang chọn'})
    assert 'so với kỳ trước' not in na_prev and 'chưa so được vì kỳ trước nằm ngoài tháng' in na_prev
    ok = agents.kpi(targets, SCOPE, {'on_time_rate': 0.97}, gxt_report(), None, 0, EVIDENCE, {})
    assert 'Cảnh báo' not in ok and '— đạt' in ok


def test_sla_rate_whole_equals_combined_halves_counts():
    from datetime import datetime
    from ops_team.supra.sla import sla_summary, TZ
    data = _ops_data()
    now = datetime(2026, 10, 29, 10, 0, tzinfo=TZ)
    whole = sla_summary(data, '2026-10-10', '2026-10-28', now)
    halves = [sla_summary(data, '2026-10-10', '2026-10-19', now), sla_summary(data, '2026-10-20', '2026-10-28', now)]
    on_time, late = sum(h['on_time'] for h in halves), sum(h['late'] for h in halves)
    assert (whole['on_time'], whole['late']) == (on_time, late) == (1, 1)
    assert whole['on_time_rate'] == on_time/(on_time+late) == 0.5


def complete_report():
    """A synthetic cost table where every in-scope part is complete, so projected totals exist."""
    report = gxt_report(overlay={'available': True, 'revenue': {'complete': True, 'component_vnd': '5000000', 'eligible_vnd': '5000000'},
                                 'known_cost_subtotal': '3000000', 'missing_s_gxt_rows': 0, 'scope_exclusions': {}},
                        dc={'component_vnd': '100'}, warehouse={'shared_pool_vnd': '200'})
    report['points'] = {'held': [], 'complete': True, 'counts': {}, 'eligible_vnd': None}
    return report


def test_accounting_shows_revenue_and_gross_profit_only_when_complete():
    from ops_team.supra.manual_cost_overlay import projected_totals
    total, gp, blockers = projected_totals(complete_report())
    assert total == '3000000' and not blockers
    assert Decimal(gp) == Decimal('5000000')-Decimal('3000000')  # independent: revenue - cost
    text = agents.accounting(complete_report(), SCOPE, evidence=EVIDENCE)
    assert 'Chi phí dự kiến 10/10–12/10: 3.000.000đ trước VAT, VAT 8% là 240.000đ, sau VAT 3.240.000đ.' in text
    assert 'Doanh thu dự kiến 5.000.000đ trước VAT; lãi gộp dự kiến 2.000.000đ' in text
    partial = agents.accounting(gxt_report(), SCOPE)
    assert 'Doanh thu dự kiến' not in partial and 'Doanh thu và lãi gộp dự kiến cũng N/A' in partial


def test_templates_are_readable_by_the_importers():
    from pathlib import Path
    from ops_team.supra.km_log import parse_log
    root = Path(__file__).resolve().parents[1]/'docs'/'templates'
    statement = parse_statement((root/'bang_ke_ncc_mau.csv').read_bytes(), 'bang_ke_ncc_mau.csv')
    log = parse_log((root/'nhat_trinh_km_mau.csv').read_bytes(), 'nhat_trinh_km_mau.csv')
    assert len(statement['lines']) == 1 and statement['lines'][0]['vat_vnd'] == Decimal(80000)
    assert len(log['lines']) == 1 and log['lines'][0]['empty_km'] == Decimal(15) and log['lines'][0]['wait_hours'] == Decimal('1.5')


def km_csv(rows, headers='Ngày,Biển số xe,Km,Km rỗng,Giờ chờ'):
    return ('\n'.join([headers]+rows)).encode()


def test_km_log_checks_and_full_equals_halves(tmp_path):
    from ops_team.supra.km_log import parse_log, summary
    log = parse_log(km_csv(['10/10/2026,29C-111.11,100,10,1', '11/10/2026,29C-222.22,50,5,0.5', '12/10/2026,29C-111.11,30,40,0',
                            '12/10/2026,,20,0,0', '12/10/2026,29C-333.33,abc,0,0', '13/10/2026,29C-111.11,25,5,2']), 'km.csv')
    assert [h['row'] for h in log['held']] == [4, 5, 6] and log['held'][0]['missing'] == ['empty_km']  # empty > km is held, not clipped
    whole = summary(log, {'start': '2026-10-10', 'end': '2026-10-13'}, EVIDENCE)
    halves = [summary(log, {'start': '2026-10-10', 'end': '2026-10-11'}, EVIDENCE), summary(log, {'start': '2026-10-12', 'end': '2026-10-13'}, EVIDENCE)]
    assert whole['km'] == sum(h['km'] for h in halves) == Decimal(175)
    assert whole['empty_km'] == sum(h['empty_km'] for h in halves) == Decimal(20) and whole['wait_hours'] == Decimal('3.5')
    assert whole['logged_without_raw_rows'] == [7] and whole['raw_without_log'] == [('29C11111', '2026-10-12')]
    text = agents.km_text(whole, EVIDENCE)
    assert 'tổng 175 km' in text and 'nhật trình dòng 7' in text and 'V1 12/10' in text and '29C' not in text and 'biểu giá km' in text
    no_empty = summary(parse_log(km_csv(['10/10/2026,29C-111.11,100'], 'Ngày,Biển số xe,Km'), 'k.csv'), SCOPE, EVIDENCE)
    assert no_empty['km'] == Decimal(100) and no_empty['empty_km'] is None  # absent column = N/A
    state = BusinessState(tmp_path/'v')
    try:
        save_statement(state, log, 'km')
        loaded = load_statement(state, '2026-10', 'km')
        assert loaded['lines'][0]['empty_km'] == Decimal(10) and load_statement(state, '2026-10') is None  # separate from NCC statements
    finally:
        state.close()


def test_import_km_by_telegram_document_owner_only(tmp_path):
    from ops_team.telegram_assistant import AssistantResponder
    from ops_team.telegram_bridge import Incoming
    from ops_team.telegram_state import GROUP_ID
    data = km_csv(['10/10/2026,29C-111.11,100,10,1'])

    class Api:
        def call(self, method, **kw):
            return {'file_path': 'x'}

        def download(self, path):
            return data
    state = BusinessState(tmp_path/'vault')
    try:
        r = AssistantResponder(state, {'owner_id': 123, 'models': {}})
        doc = {'file_name': 'nhat_trinh.csv', 'file_size': len(data), 'file_id': 'f'}
        assert 'Chỉ owner' in r.document(Incoming(GROUP_ID, 456, 1, None, 'import', 'km'), doc, Api())
        reply = r.document(Incoming(GROUP_ID, 123, 2, None, 'import', 'km'), doc, Api())
        assert 'nhật trình km' in reply and '1 dòng hợp lệ' in reply and load_statement(state, '2026-10', 'km')
    finally:
        state.close()


def test_per_person_wage_conserves_vehicle_day_wages_and_full_equals_halves():
    from ops_team.supra.workforce_data import parse_roster, per_person_wage
    report = gxt_report()
    roster = parse_roster(('Ngày,Biển số xe,Nhân viên\n10/10/2026,29C-111.11,NV A\n11/10/2026,29C-222.22,NV B\n'
                           '11/10/2026,29C-222.22,NV C\n12/10/2026,,NV D\n').encode(), 'r.csv')
    assert roster['held'] == [{'row': 5, 'missing': ['plate']}]
    wages, shared, unassigned, left = per_person_wage(report, roster, SCOPE)
    by = {(g['vehicle'], g['day']): Decimal(g['wage_vnd']) for g in report['gxt']['by_group']}
    assert wages == {'NV A': by[('29C11111', '2026-10-10')]} and shared == [('29C22222', '2026-10-11')] and unassigned == []
    assert left == by[('29C22222', '2026-10-11')]
    assert sum(wages.values())+left == Decimal(report['gxt']['wage_supra_vnd'])  # nothing lost or doubled
    halves = [per_person_wage(report, roster, {'start': '2026-10-10', 'end': '2026-10-10'})[0],
              per_person_wage(report, roster, {'start': '2026-10-11', 'end': '2026-10-12'})[0]]
    assert sum((sum(h.values(), Decimal(0)) for h in halves), Decimal(0)) == sum(wages.values())
    text = agents.workforce(report, SCOPE, evidence=EVIDENCE, roster=roster)
    assert '• NV A:' in text and '1 xe/ngày có từ 2 người trở lên mà đơn chưa gán ID người (J10): V2 11/10' in text
    assert 'Lương chưa gán được người:' in text
    assert 'OT, năng suất, tỷ lệ lỗi: N/A' in text and '29C' not in text
    assert 'Theo từng người: N/A' in agents.workforce(report, SCOPE, evidence=EVIDENCE)


def test_timesheet_metrics_use_existing_formulas_and_keep_na():
    from ops_team.supra.workforce_data import parse_timesheet, timesheet_metrics
    sheet = parse_timesheet(('Ngày,Nhân viên,Giờ thường,Giờ OT,Sản lượng xử lý,Số lỗi\n10/10/2026,NV A,8,2,100,1\n'
                             '11/10/2026,NV B,8,0,60,2\n12/10/2026,NV A,-1,0,10,0\n').encode(), 't.csv')
    assert sheet['held'] == [{'row': 4, 'missing': ['regular_hours']}]
    m = timesheet_metrics(sheet, SCOPE)
    assert m['ot_rate_pct'] == Decimal(2)/Decimal(18)*100 and m['productivity'] == Decimal(160)/Decimal(18)
    assert m['defect_rate_pct'] == Decimal(3)/Decimal(160)*100 and m['people'] == 2
    halves = [timesheet_metrics(sheet, {'start': '2026-10-10', 'end': '2026-10-10'}), timesheet_metrics(sheet, {'start': '2026-10-11', 'end': '2026-10-12'})]
    assert sum(h['ot_hours'] for h in halves) == m['ot_hours'] and sum(h['regular_hours'] for h in halves) == m['regular_hours']
    bare = timesheet_metrics(parse_timesheet(b'Ngay,Nhan vien,Gio thuong\n10/10/2026,NV A,8\n', 't.csv'), SCOPE)
    assert bare['regular_hours'] == Decimal(8) and bare['ot_rate_pct'] is None and bare['defect_rate_pct'] is None  # absent column = N/A
    text = agents.workforce(gxt_report(), SCOPE, evidence=EVIDENCE, timesheet=sheet)
    assert 'OT 11,1%' in text and 'tỷ lệ lỗi 1,9%' in text and 'J11' in text and 'Dòng chấm công thiếu/lỗi chưa đọc: 4' in text


def test_fleet_view_and_routing():
    from datetime import date
    from ops_team.intent import route
    from ops_team.supra.km_log import parse_log, summary
    log = parse_log(km_csv(['10/10/2026,29C-111.11,100,10,1', '11/10/2026,29C-222.22,50,5,0.5']), 'km.csv')
    text = agents.fleet(SCOPE, EVIDENCE, {'29C11111': Decimal(1250)}, summary(log, SCOPE, EVIDENCE), gxt_report())
    assert text.startswith('Đội xe 10/10–12/10: 3 lượt xe/ngày trên Raw, 2 xe; tỷ lệ tải (hàng ÷ tải trọng) 3,1% trên 3 lượt có tải trọng.')
    assert 'tổng km 150; km rỗng 15 km; giờ chờ 1,5 giờ' in text and 'km rỗng 10' not in text and '29C' not in text
    # J2: trip = vehicle/day. 2 GXT vehicle-days at 1.000.000đ; Supra share 800.000 + 1.000.000 (see gxt_report).
    assert 'Chi phí/chuyến (xe/ngày, J2): xe GXT 1.000.000đ/xe-ngày tiền thuê cả xe, phần Supra 900.000đ/xe-ngày (2 xe/ngày); FTL N/A.' in text
    bare = agents.fleet(SCOPE, {**EVIDENCE, 'inventory': []}, {}, None)
    assert 'tỷ lệ tải (hàng ÷ tải trọng) N/A' in bare and 'Km, km rỗng, giờ chờ: N/A' in bare and 'Chi phí/chuyến (xe/ngày): N/A' in bare
    today = date(2026, 11, 5)
    assert route('đội xe tuần này', today)[0] == 'doixe' and route('giờ chờ xe hôm qua', today)[0] == 'doixe'
    assert route('xe ngày 15/10', today)[0] == 'xe' and route('tỷ lệ tải xe tháng 10', today)[0] == 'kpi'


def test_workforce_templates_and_imports_owner_only(tmp_path):
    from pathlib import Path
    from ops_team.supra.workforce_data import parse_roster, parse_timesheet
    from ops_team.telegram_assistant import AssistantResponder
    from ops_team.telegram_bridge import Incoming
    from ops_team.telegram_state import GROUP_ID
    root = Path(__file__).resolve().parents[1]/'docs'/'templates'
    assert len(parse_roster((root/'tai_xe_xe_mau.csv').read_bytes(), 'x.csv')['lines']) == 1
    assert parse_timesheet((root/'cham_cong_mau.csv').read_bytes(), 'x.csv')['lines'][0]['ot_hours'] == Decimal('1.5')
    data = (root/'tai_xe_xe_mau.csv').read_bytes()

    class Api:
        def call(self, method, **kw):
            return {'file_path': 'x'}

        def download(self, path):
            return data
    state = BusinessState(tmp_path/'vault')
    try:
        r = AssistantResponder(state, {'owner_id': 123, 'models': {}})
        doc = {'file_name': 'tai_xe.csv', 'file_size': len(data), 'file_id': 'f'}
        assert 'Chỉ owner' in r.document(Incoming(GROUP_ID, 456, 1, None, 'import', 'taixe'), doc, Api())
        assert 'danh sách tài xế–xe' in r.document(Incoming(GROUP_ID, 123, 2, None, 'import', 'taixe'), doc, Api())
        assert load_statement(state, '2026-11', 'taixe')['lines'][0]['person'] == 'NV MẪU 01'
    finally:
        state.close()


def test_timesheet_round_trips_through_the_vault(tmp_path):
    from ops_team.supra.workforce_data import parse_timesheet, timesheet_metrics
    sheet = parse_timesheet(b'Ngay,Nhan vien,Gio thuong,Gio OT\n10/10/2026,NV A,8,2\n', 't.csv')
    state = BusinessState(tmp_path/'v')
    try:
        save_statement(state, sheet, 'chamcong')
        loaded = load_statement(state, '2026-10', 'chamcong')
        assert timesheet_metrics(loaded, SCOPE)['ot_rate_pct'] == Decimal(20)
    finally:
        state.close()


def test_audit_document_checks_count_only_when_documents_exist(tmp_path):
    passed, counted, text = agents.audit(gxt_report(), EVIDENCE, None, SCOPE)
    assert passed == counted and 'Chưa kiểm được (N/A): Đối soát bảng kê NCC' in text and 'Đối chiếu nhật trình km' in text
    data = statement_xlsx([[1, '10/10/2026', '29C-111.11', 1, 1000000, 80000, 1080000, ''],
                           [2, '11/10/2026', '29C-222.22', 1, 1000000, 90000, 1090000, ''],
                           [3, '11/10/2026', '29C-222.22', 1, 1000000, 80000, 1080000, '']])
    statement = parse_statement(data, 'b.xlsx')
    rec = reconcile(statement, gxt_report(), EVIDENCE, SCOPE['start'], SCOPE['end'])
    from ops_team.supra.km_log import parse_log, summary
    km = summary(parse_log(km_csv(['10/10/2026,29C-111.11,100,10,1', '11/10/2026,29C-333.33,50,5,0']), 'k.csv'), SCOPE, EVIDENCE)
    passed2, counted2, text2 = agents.audit(gxt_report(), EVIDENCE, None, SCOPE, reconciliation=rec, km=km)
    assert counted2 == counted+5 and passed2 == passed+2  # VAT, duplicate, GXT money fail; activity + km pass
    assert 'VAT đúng 8%' in text2 and 'dòng 5' in text2 and 'dòng 5 và 6' in text2 and 'V2 lệch 1.000.000đ' in text2
    assert 'N/A' not in text2.split('Chưa đạt')[0] and '29C' not in text2


def test_ai_roles_answer_with_code_agents_when_no_model(tmp_path, monkeypatch):
    from ops_team.telegram_assistant import AssistantResponder, ROLE_TO_CODE
    from ops_team.telegram_bridge import Incoming
    from ops_team.telegram_state import GROUP_ID
    state = BusinessState(tmp_path/'vault')
    try:
        r = AssistantResponder(state, {'owner_id': 123, 'models': {}})
        monkeypatch.setattr(r, '_domain', lambda command, text: 'code:'+command)
        for role, target in (('accounting', 'ketoan'), ('operations', 'vanhanh'), ('workforce', 'nhansu'), ('fleet', 'doixe'),
                             ('auditor', 'kiemtoan'), ('coordinator', 'tongquan')):
            assert ROLE_TO_CODE[role] == target
            assert r.answer(Incoming(GROUP_ID, 123, 1, None, role, 'bất kỳ')) == 'code:'+target
            assert 'Chỉ owner' in r.answer(Incoming(GROUP_ID, 456, 2, None, role, ''))
    finally:
        state.close()



def test_wage_follows_the_person_id_assigned_to_each_order():
    """Owner J10: two people on one vehicle/day; each order's wage goes to whoever is assigned that order."""
    from ops_team.supra.workforce_data import parse_roster, per_person_wage
    report = gxt_report()  # 29C11111 on 10/10 carries orders A (20 kg) and B (60 kg)
    order_wage = {k: Decimal(v['wage_vnd']) for g in report['gxt']['by_group'] for k, v in g['order_costs'].items()}
    assert order_wage == {'A': Decimal(5000), 'B': Decimal(9000), 'C': Decimal(5000)}
    roster = parse_roster(('Ngày,Biển số xe,Nhân viên,Mã đơn\n10/10/2026,29C-111.11,NV A,A\n10/10/2026,29C-111.11,NV B,B\n'
                           '11/10/2026,29C-222.22,NV C,\n').encode(), 'r.csv')
    wages, shared, unassigned, left = per_person_wage(report, roster, SCOPE)
    assert wages == {'NV A': Decimal(5000), 'NV B': Decimal(9000), 'NV C': Decimal(5000)} and left == 0 and not shared and not unassigned
    vehicle = sum(Decimal(v['vehicle_vnd']) for g in report['gxt']['by_group'] for v in g['order_costs'].values())
    assert vehicle == Decimal(report['gxt']['vehicle_supra_vnd'])  # per-order vehicle shares add up to the Supra share
