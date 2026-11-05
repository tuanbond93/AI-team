"""Workforce inputs the sources do not have: driver roster and timesheet (owner answer 25: no timekeeping yet).

Both are read by header aliases with the NCC statement reader, stored encrypted per month,
and only used when sent. Without them the workforce view stays per vehicle/day and
OT / productivity / defects stay N/A with who should send what.

Roster: day, plate, person (+ optional role) → per-person delivery wage = the GXT wage
of that vehicle/day from the /chiphi table. Two or more people on one vehicle/day are
held (OWNER J10 proposal A), never split by guess.
Timesheet: day, person, regular hours (+ OT hours, processed volume, defects) → the
formulas already in metrics.py for the legacy workforce table (OWNER J11 to confirm).
"""
from collections import defaultdict
from decimal import Decimal, localcontext
import hashlib

from .booking_quantity import business_key
from .booking_vehicles import plate as parse_plate
from .ncc_invoice import _day, money, table_rows

ROSTER = {
    'day': ['ngay', 'ngay lam', 'ngay giao'],
    'plate': ['bien so', 'bien so xe', 'bien xe', 'so xe', 'bks', 'xe'],
    'person': ['nhan vien', 'ten nhan vien', 'tai xe', 'ho ten', 'ma nhan vien', 'ma nv', 'nguoi giao'],
    'role': ['vai tro', 'chuc vu', 'vi tri'],
    'order': ['ma don', 'ma ghn', 'ma don ghn', 'ghn', 'don hang'],  # owner J10: wage goes to the person whose ID is on the order
}
TIMESHEET = {
    'day': ['ngay', 'ngay lam', 'ngay cong'],
    'person': ['nhan vien', 'ten nhan vien', 'ho ten', 'ma nhan vien', 'ma nv', 'tai xe'],
    'regular_hours': ['gio thuong', 'gio cong', 'gio lam', 'so gio', 'gio hanh chinh'],
    'ot_hours': ['gio ot', 'ot', 'tang ca', 'gio tang ca'],
    'processed': ['san luong', 'san luong xu ly', 'so don', 'so kien', 'so don xu ly'],
    'defects': ['loi', 'so loi', 'don loi', 'sai sot'],
}


def person_key(value):
    return ' '.join(str(value or '').split())[:80]


def parse_roster(data, name):
    lines, held = [], []
    for offset, get, cols in table_rows(data, name, ROSTER, ('day', 'plate', 'person'), ident='person'):
        line = {'row': offset, 'day': _day(get('day')), 'plate': parse_plate(get('plate')), 'person': person_key(get('person')),
                'role': person_key(get('role')), 'order': business_key(get('order')) if 'order' in cols else ''}
        missing = [k for k in ('day', 'plate', 'person') if not line[k]]
        (held.append({'row': offset, 'missing': missing}) if missing else lines.append(line))
    if not lines:
        raise ValueError('Danh sách tài xế–xe không có dòng hợp lệ (cần ngày, biển số, nhân viên).')
    return {'lines': lines, 'held': held, 'sha256': hashlib.sha256(data).hexdigest(), 'name': name[:120]}


def parse_timesheet(data, name):
    lines, held = [], []
    for offset, get, cols in table_rows(data, name, TIMESHEET, ('day', 'person', 'regular_hours'), ident='person'):
        line = {'row': offset, 'day': _day(get('day')), 'person': person_key(get('person')), 'regular_hours': money(get('regular_hours'))}
        for field in ('ot_hours', 'processed', 'defects'):
            line[field] = money(get(field)) if field in cols else None
        problems = [k for k in ('day', 'person') if not line[k]]
        problems += [k for k in ('regular_hours', 'ot_hours', 'processed', 'defects')
                     if (k == 'regular_hours' or k in cols) and (line[k] is None or line[k] < 0)]
        (held.append({'row': offset, 'missing': problems}) if problems else lines.append(line))
    if not lines:
        raise ValueError('Bảng chấm công không có dòng hợp lệ (cần ngày, nhân viên, giờ thường).')
    return {'lines': lines, 'held': held, 'sha256': hashlib.sha256(data).hexdigest(), 'name': name[:120]}


def per_person_wage(report, roster, scope):
    """(wages, shared, unassigned, left_vnd) from the /chiphi GXT wage of each order.

    Owner J10: an order assigned to a person ID (roster "mã đơn") pays that person. Other
    orders of a vehicle/day go to its one listed person; with two or more people and no
    order assignment, or nobody listed, they stay unpaid here (N/A) and are counted in left_vnd.
    shared / unassigned list those vehicle/days. wages + left_vnd = the GXT wage total.
    """
    by_order, by_vehicle = defaultdict(set), defaultdict(set)
    for l in roster['lines']:
        if scope['start'] <= str(l['day']) <= scope['end']:
            if l.get('order'):
                by_order[l['order']].add(l['person'])
            else:
                by_vehicle[(l['plate'], str(l['day']))].add(l['person'])
    wages, shared, unassigned, left = defaultdict(lambda: Decimal(0)), [], [], Decimal(0)
    with localcontext() as ctx:
        ctx.prec = 60
        for g in (report or {}).get('gxt', {}).get('by_group', []):
            names = by_vehicle.get((g['vehicle'], g['day']), set())
            open_wage = Decimal(0)
            for order, cost in g.get('order_costs', {}).items():
                assigned = by_order.get(business_key(order), set()) or (names if len(names) == 1 else set())
                if len(assigned) == 1:
                    wages[next(iter(assigned))] += Decimal(cost['wage_vnd'])
                else:
                    open_wage += Decimal(cost['wage_vnd'])
            if open_wage:
                left += open_wage
                (shared if names else unassigned).append((g['vehicle'], g['day']))
    return dict(wages), shared, unassigned, left


def timesheet_metrics(sheet, scope):
    """Totals and the legacy formulas. Any missing column or value in scope makes that metric None."""
    lines = [l for l in sheet['lines'] if scope['start'] <= str(l['day']) <= scope['end']]
    def total(field):
        values = [l[field] for l in lines]
        return sum(values, Decimal(0)) if values and all(v is not None for v in values) else None
    regular, ot, processed, defects = total('regular_hours'), total('ot_hours'), total('processed'), total('defects')
    hours = regular+ot if regular is not None and ot is not None else None
    return {'lines': len(lines), 'people': len({l['person'] for l in lines}), 'regular_hours': regular, 'ot_hours': ot,
            'ot_rate_pct': ot/hours*100 if hours else None, 'productivity': processed/hours if processed is not None and hours else None,
            'defect_rate_pct': defects/processed*100 if defects is not None and processed else None,
            'held_rows': [h['row'] for h in sheet.get('held', [])]}
