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
import re

from .booking_vehicles import plate as parse_plate
from .ncc_invoice import _day, _find_header, _key, money, read_rows

ROSTER = {
    'day': ['ngay', 'ngay lam', 'ngay giao'],
    'plate': ['bien so', 'bien so xe', 'bien xe', 'so xe', 'bks', 'xe'],
    'person': ['nhan vien', 'ten nhan vien', 'tai xe', 'ho ten', 'ma nhan vien', 'ma nv', 'nguoi giao'],
    'role': ['vai tro', 'chuc vu', 'vi tri'],
}
TIMESHEET = {
    'day': ['ngay', 'ngay lam', 'ngay cong'],
    'person': ['nhan vien', 'ten nhan vien', 'ho ten', 'ma nhan vien', 'ma nv', 'tai xe'],
    'regular_hours': ['gio thuong', 'gio cong', 'gio lam', 'so gio', 'gio hanh chinh'],
    'ot_hours': ['gio ot', 'ot', 'tang ca', 'gio tang ca'],
    'processed': ['san luong', 'san luong xu ly', 'so don', 'so kien', 'so don xu ly'],
    'defects': ['loi', 'so loi', 'don loi', 'sai sot'],
}


def _rows(data, name, columns, required):
    rows = read_rows(data, name)
    header, cols = _find_header(rows, columns, required)
    for offset, row in enumerate(rows[header+1:], start=header+2):
        if not any(c not in (None, '') for c in row):
            continue
        get = lambda f: row[cols[f]] if f in cols and cols[f] < len(row) else None
        if any(re.match(r'(tong|cong)\b', _key(c)) for c in row if isinstance(c, str)) and not _key(get('person') or ''):
            continue
        yield offset, get, cols


def person_key(value):
    return ' '.join(str(value or '').split())[:80]


def parse_roster(data, name):
    lines, held = [], []
    for offset, get, cols in _rows(data, name, ROSTER, ('day', 'plate', 'person')):
        line = {'row': offset, 'day': _day(get('day')), 'plate': parse_plate(get('plate')), 'person': person_key(get('person')),
                'role': person_key(get('role'))}
        missing = [k for k in ('day', 'plate', 'person') if not line[k]]
        (held.append({'row': offset, 'missing': missing}) if missing else lines.append(line))
    if not lines:
        raise ValueError('Danh sách tài xế–xe không có dòng hợp lệ (cần ngày, biển số, nhân viên).')
    return {'lines': lines, 'held': held, 'sha256': hashlib.sha256(data).hexdigest(), 'name': name[:120]}


def parse_timesheet(data, name):
    lines, held = [], []
    for offset, get, cols in _rows(data, name, TIMESHEET, ('day', 'person', 'regular_hours')):
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
    """{person: wage} from the /chiphi GXT wage per vehicle/day; vehicle/days with 0 or 2+ people are listed, not split."""
    people = defaultdict(set)
    for l in roster['lines']:
        if scope['start'] <= str(l['day']) <= scope['end']:
            people[(l['plate'], str(l['day']))].add(l['person'])
    wages, shared, unassigned = defaultdict(lambda: Decimal(0)), [], []
    with localcontext() as ctx:
        ctx.prec = 60
        for g in (report or {}).get('gxt', {}).get('by_group', []):
            names = people.get((g['vehicle'], g['day']), set())
            if len(names) == 1:
                wages[next(iter(names))] += Decimal(g['wage_vnd'])
            elif names:
                shared.append((g['vehicle'], g['day']))
            else:
                unassigned.append((g['vehicle'], g['day']))
    return dict(wages), shared, unassigned


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
