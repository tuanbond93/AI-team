"""NCC (vehicle supplier) statement: read, normalise, store encrypted, reconcile.

The accountant's real layout is not known yet (owner 05/11/2026: sent at month end),
so columns are found by header aliases. A real file then needs only alias tweaks,
not new logic. Rows missing plate/date/amount are held, never read as 0.
"""
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, localcontext
from io import BytesIO
import hashlib
import json
import re

from .booking_vehicles import day as parse_day, plate as parse_plate

VAT_RATE = Decimal('0.08')  # owner rule: reconcile before VAT 8%
COLUMNS = {
    'plate': ['bien so', 'bien so xe', 'bien xe', 'so xe', 'bks', 'xe'],
    'day': ['ngay', 'ngay van chuyen', 'ngay chay', 'ngay giao', 'ngay xuat'],
    'trips': ['so chuyen', 'chuyen', 'so luot'],
    'km': ['km', 'so km', 'quang duong'],
    'amount': ['thanh tien', 'tien truoc vat', 'thanh tien truoc vat', 'chua vat', 'thanh tien chua vat', 'cuoc', 'tien cuoc', 'don gia x so luong'],
    'vat': ['vat', 'tien vat', 'thue gtgt', 'tien thue', 'thue vat'],
    'total': ['tong cong', 'tong tien', 'thanh tien sau vat', 'tong thanh toan', 'sau vat'],
    'note': ['ghi chu', 'tuyen', 'dien giai', 'noi dung'],
}
REQUIRED = ('plate', 'day', 'amount')


def _key(text):
    from ..intent import _plain
    return ' '.join(_plain(str(text or '')).replace('(', ' ').replace(')', ' ').replace('/', ' ').split())


def money(value):
    """Decimal VND from 1.250.000 / 1,250,000 / 1250000 / 1250000.0; None if blank or not a number."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            number = Decimal(str(value))
        except InvalidOperation:
            return None
        return number if number.is_finite() else None
    text = re.sub(r'\s|đ|vnd|₫', '', str(value).casefold())
    if not text:
        return None
    if re.fullmatch(r'-?\d{1,3}([.,]\d{3})+', text):
        text = re.sub(r'[.,]', '', text)
    elif re.fullmatch(r'-?\d+([.,]\d+)?', text):
        text = text.replace(',', '.')
    else:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _day(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return parse_day(value)


def _find_header(rows):
    """(row index, {field: column}) for the first row naming plate, day and amount."""
    for index, row in enumerate(rows[:20]):
        found = {}
        for col, cell in enumerate(row):
            name = _key(cell)
            if not name:
                continue
            # Longest matching alias wins, so "thanh tien sau vat" is total, not amount.
            hits = [(len(a), field) for field, aliases in COLUMNS.items() for a in aliases if name == a or name.startswith(a+' ')]
            if hits:
                field = max(hits)[1]
                found.setdefault(field, col)
        if all(f in found for f in REQUIRED):
            return index, found
    raise ValueError('Không tìm thấy dòng tiêu đề có đủ cột biển số, ngày và thành tiền.')


def read_rows(data, name):
    if name.lower().endswith('.csv'):
        import csv
        text = data.decode('utf-8-sig')
        return [row for row in csv.reader(text.splitlines())]
    from openpyxl import load_workbook
    book = load_workbook(BytesIO(data), read_only=True, data_only=True)
    sheet = book.worksheets[0]
    return [list(row) for row in sheet.iter_rows(values_only=True)]


def parse_statement(data, name):
    """Normalised lines + held rows. Raises ValueError if the layout is not recognisable."""
    rows = read_rows(data, name)
    header, cols = _find_header(rows)
    lines, held = [], []
    for offset, row in enumerate(rows[header+1:], start=header+2):
        get = lambda f: row[cols[f]] if f in cols and cols[f] < len(row) else None
        if not any(c not in (None, '') for c in row):
            continue
        if not parse_plate(get('plate')) and any(re.match(r'(tong|cong|tong cong)\b', _key(c)) for c in row if isinstance(c, str)):
            continue  # subtotal/total rows of the statement
        line = {'row': offset, 'plate': parse_plate(get('plate')), 'day': _day(get('day')), 'amount_vnd': money(get('amount')),
                'vat_vnd': money(get('vat')), 'total_vnd': money(get('total')), 'trips': money(get('trips')), 'km': money(get('km')),
                'note': str(get('note') or '')[:120]}
        problems = [k for k, label in (('plate', 'biển số'), ('day', 'ngày'), ('amount_vnd', 'thành tiền')) if not line[k]
                    and not (k == 'amount_vnd' and line[k] == 0)]
        if problems:
            held.append({'row': offset, 'missing': problems})
            continue
        lines.append(line)
    if not lines:
        raise ValueError('Bảng kê không có dòng hợp lệ nào (cần biển số, ngày, thành tiền).')
    return {'lines': lines, 'held': held, 'columns': sorted(cols), 'sha256': hashlib.sha256(data).hexdigest(), 'name': name[:120]}


def _encode(statement):
    def enc(v):
        return str(v) if isinstance(v, (Decimal, date)) else v
    return json.dumps({**statement, 'lines': [{k: enc(v) for k, v in l.items()} for l in statement['lines']]}, ensure_ascii=False)


def _decode(text):
    statement = json.loads(text)
    for line in statement['lines']:
        line['day'] = date.fromisoformat(line['day'])
        for k in ('amount_vnd', 'vat_vnd', 'total_vnd', 'trips', 'km'):
            line[k] = Decimal(line[k]) if line[k] is not None else None
    return statement


def save_statement(state, statement):
    """Encrypted in the vault, one file per month seen in the lines. Returns the months stored."""
    from ..telegram_secrets import save_protected
    months = defaultdict(list)
    for line in statement['lines']:
        months[line['day'].strftime('%Y-%m')].append(line)
    for month, lines in months.items():
        save_protected(state.root/'ncc'/f'{month}.dpapi', _encode({**statement, 'lines': lines}).encode())
    return sorted(months)


def load_statement(state, month):
    from ..telegram_secrets import load_protected
    path = state.root/'ncc'/f'{month}.dpapi'
    return _decode(load_protected(path).decode()) if path.exists() else None


def reconcile(statement, report, evidence, start, end):
    """Compare a statement with the code's view of the same days. Pure; returns a dict of findings."""
    lines = [l for l in statement['lines'] if start <= str(l['day']) <= end]
    out = {'lines': len(lines), 'held_rows': [h['row'] for h in statement.get('held', [])]}
    with localcontext() as ctx:
        ctx.prec = 60
        amount = sum((l['amount_vnd'] for l in lines), Decimal(0))
        vats = [l['vat_vnd'] for l in lines]
        out['amount_vnd'] = amount
        out['vat_vnd'] = sum(vats, Decimal(0)) if lines and all(v is not None for v in vats) else None
        out['expected_vat_vnd'] = (amount*VAT_RATE).quantize(Decimal(1))
        # A line's VAT should be 8% of its amount (allow 1 VND rounding per line).
        out['vat_mismatch_rows'] = [l['row'] for l in lines if l['vat_vnd'] is not None and abs(l['vat_vnd']-l['amount_vnd']*VAT_RATE) > 1]
        out['total_mismatch_rows'] = [l['row'] for l in lines if l['total_vnd'] is not None and l['vat_vnd'] is not None
                                      and abs(l['total_vnd']-l['amount_vnd']-l['vat_vnd']) > 1]
    seen, dup = {}, []
    for l in lines:
        key = (l['plate'], l['day'])
        if key in seen:
            dup.append((seen[key], l['row']))
        seen.setdefault(key, l['row'])
    out['duplicate_rows'] = dup
    # Activity seen by code: Raw S trucks by load date, Booking AG trucks by dispatch date.
    active = {(g['plate'], g['day']) for g in evidence.get('groups', []) if start <= g['day'] <= end}
    billed = {(l['plate'], str(l['day'])) for l in lines}
    known_plates = {p for p, _ in active} | {v['plate'] for v in evidence.get('inventory', [])}
    out['billed_without_activity'] = sorted(k for k in billed if k not in active and k[0] in {p for p, _ in active})
    out['unknown_plates'] = sorted({p for p, _ in billed} - known_plates)
    billed_plates = {p for p, _ in billed}
    out['activity_not_billed'] = sorted(k for k in active if k[0] in billed_plates and k not in billed)
    # Money check only where code has a vehicle/day rate: GXT trucks (rate × days on the statement plate).
    rates = {(g['vehicle'], g['day']): Decimal(g['vehicle_day_vnd']) for g in report.get('gxt', {}).get('by_group', [])} if report else {}
    # The rate counts once per truck-day; a duplicated line then shows up as an overcharge.
    by_plate = defaultdict(lambda: [Decimal(0), Decimal(0), 0])
    counted = set()
    for l in lines:
        key = (l['plate'], str(l['day']))
        rate = rates.get(key)
        if rate is not None:
            by_plate[l['plate']][0] += l['amount_vnd']
            if key not in counted:
                counted.add(key)
                by_plate[l['plate']][1] += rate
                by_plate[l['plate']][2] += 1
    out['gxt_compare'] = [{'plate': p, 'days': n, 'billed_vnd': b, 'expected_vnd': e, 'diff_vnd': b-e} for p, (b, e, n) in sorted(by_plate.items())]
    return out
