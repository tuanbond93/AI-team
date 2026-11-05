"""NCC trip log (nhật trình km): read, check, store encrypted, summarise.

Owner answer 17: the NCC sends it monthly; no real sample yet. Columns are found by
header aliases like the NCC statement (one reader, ``ncc_invoice._find_header``).
Required: plate, day, km. Optional: empty km, waiting hours, trips, route, note.
Bad rows are held, never read as 0. Money by km stays N/A until the owner sends the
km tariff (question 17); empty-km rate and waiting hours are shown with the
definitions still to confirm (OWNER J4).
"""
from decimal import Decimal, localcontext

from .booking_vehicles import plate as parse_plate
from .ncc_invoice import _day, _find_header, _key, money, read_rows

COLUMNS = {
    'plate': ['bien so', 'bien so xe', 'bien xe', 'so xe', 'bks', 'xe'],
    'day': ['ngay', 'ngay chay', 'ngay van chuyen', 'ngay giao'],
    'km': ['km', 'so km', 'tong km', 'km chay', 'km thuc te', 'quang duong'],
    'empty_km': ['km rong', 'km chay rong', 'so km rong'],
    'wait_hours': ['gio cho', 'thoi gian cho', 'so gio cho', 'cho xe'],
    'trips': ['so chuyen', 'chuyen', 'so luot'],
    'route': ['tuyen', 'tuyen duong', 'lo trinh', 'noi giao'],
    'note': ['ghi chu', 'dien giai'],
}
REQUIRED = ('plate', 'day', 'km')
TEMPLATE_HEADERS = ['Ngày', 'Biển số xe', 'Tuyến', 'Số chuyến', 'Km', 'Km rỗng', 'Giờ chờ', 'Ghi chú']


def parse_log(data, name):
    import hashlib
    import re
    rows = read_rows(data, name)
    header, cols = _find_header(rows, COLUMNS, REQUIRED)
    lines, held = [], []
    for offset, row in enumerate(rows[header+1:], start=header+2):
        get = lambda f: row[cols[f]] if f in cols and cols[f] < len(row) else None
        if not any(c not in (None, '') for c in row):
            continue
        if not parse_plate(get('plate')) and any(re.match(r'(tong|cong)\b', _key(c)) for c in row if isinstance(c, str)):
            continue
        line = {'row': offset, 'plate': parse_plate(get('plate')), 'day': _day(get('day')), 'km': money(get('km')),
                'empty_km': money(get('empty_km')) if 'empty_km' in cols else None,
                'wait_hours': money(get('wait_hours')) if 'wait_hours' in cols else None,
                'trips': money(get('trips')) if 'trips' in cols else None, 'route': str(get('route') or '')[:80]}
        problems = [k for k, v in (('plate', line['plate']), ('day', line['day'])) if not v]
        if line['km'] is None or line['km'] < 0:
            problems.append('km')
        if line['empty_km'] is not None and (line['empty_km'] < 0 or (line['km'] is not None and line['empty_km'] > line['km'])):
            problems.append('empty_km')
        if line['wait_hours'] is not None and line['wait_hours'] < 0:
            problems.append('wait_hours')
        if problems:
            held.append({'row': offset, 'missing': problems})
            continue
        lines.append(line)
    if not lines:
        raise ValueError('Nhật trình không có dòng hợp lệ nào (cần biển số, ngày, km).')
    return {'lines': lines, 'held': held, 'columns': sorted(cols), 'sha256': hashlib.sha256(data).hexdigest(), 'name': name[:120]}


def summary(log, scope, evidence):
    """Totals for the scope. A column that is absent or blank on any line makes its total None (N/A), not 0."""
    lines = [l for l in log['lines'] if scope['start'] <= str(l['day']) <= scope['end']]
    def total(field):
        values = [l[field] for l in lines]
        if not values or any(v is None for v in values):
            return None
        with localcontext() as ctx:
            ctx.prec = 60
            return sum(values, Decimal(0))
    km, empty, wait = total('km'), total('empty_km'), total('wait_hours')
    active = {(g['plate'], g['day']) for g in evidence.get('groups', []) if scope['start'] <= g['day'] <= scope['end']}
    logged = {(l['plate'], str(l['day'])) for l in lines}
    logged_plates = {p for p, _ in logged}
    return {'lines': len(lines), 'held_rows': [h['row'] for h in log.get('held', [])], 'km': km, 'empty_km': empty, 'wait_hours': wait,
            'empty_rate_pct': (empty/km*100 if empty is not None and km else None),
            'logged_without_raw_rows': sorted(l['row'] for l in lines if (l['plate'], str(l['day'])) not in active
                                              and l['plate'] in {p for p, _ in active}),
            'raw_without_log': sorted(k for k in active if k[0] in logged_plates and k not in logged)}
