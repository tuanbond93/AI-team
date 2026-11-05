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
    {'day': '2026-10-10', 'plate': '29C11111', 'supra_kg': '80', 'other_kg': '20', 'issues': []},
    {'day': '2026-10-11', 'plate': '29C22222', 'supra_kg': '10', 'other_kg': '0', 'issues': []},
    {'day': '2026-10-12', 'plate': '29C11111', 'supra_kg': '5', 'other_kg': '0', 'issues': []}],
    'inventory': [{'plate': '29C22222', 'booking_declared_capacity_kg': ['1250']}]}


def test_gxt_breakdown_sums_to_totals():
    report = gxt_report()
    by = report['gxt']['by_group']
    assert [g['orders'] for g in by] == [2, 1]
    assert sum(Decimal(g['wage_vnd']) for g in by) == Decimal(report['gxt']['wage_supra_vnd'])
    assert sum(Decimal(g['vehicle_supra_vnd']) for g in by) == Decimal(report['gxt']['vehicle_supra_vnd'])
    assert Decimal(by[0]['vehicle_supra_vnd']) == Decimal('800000')  # 80 of 100 kg


def test_workforce_lists_wage_per_truck_day_and_never_per_person():
    text = agents.workforce(gxt_report(), SCOPE)
    assert 'Lương giao GXT 10/10–12/10' in text and '3 đơn' in text and '29C11111 10/10: 2 đơn' in text
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
        assert 'Tỷ lệ tải xe: 3,1% — CHƯA ĐẠT' in text and 'chưa phải cân thực tế' in text
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
    text = agents.accounting(gxt_report(), SCOPE, loaded, r)
    assert 'VAT không đúng 8% ở dòng 5' in text and 'trùng biển số + ngày' in text and '29C99999' in text
    assert 'lệch 1.000.000đ' in text and 'dòng thiếu biển/ngày/tiền chưa đọc được: 8' in text


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
