"""Owner-set KPI targets and truck capacities, inside the vault.

Only the owner sets these with an explicit command (/muctieu, /taitrong); free text
never does. Each value keeps its source message and time; a new value supersedes
the old one instead of overwriting it, so history stays auditable.
"""
from decimal import Decimal, InvalidOperation
import re
import time

from .supra.booking_vehicles import plate as parse_plate

TARGETS = {
    'sla': ('SLA đúng hạn', '%', 'min'),
    'chiphi_kg': ('Chi phí mỗi kg', 'đ/kg', 'max'),
    'chiphi_don': ('Chi phí mỗi đơn', 'đ/đơn', 'max'),
    'tai': ('Tỷ lệ tải xe', '%', 'min'),
}
ALIASES = {'sla': 'sla', 'dunghan': 'sla', 'chiphi_kg': 'chiphi_kg', 'kg': 'chiphi_kg', 'cpkg': 'chiphi_kg',
           'chiphi_don': 'chiphi_don', 'don': 'chiphi_don', 'cpdon': 'chiphi_don', 'tai': 'tai', 'taixe': 'tai', 'load': 'tai'}


def parse_number(text):
    cleaned = str(text).strip().replace('%', '').replace('đ', '').replace(' ', '')
    if cleaned.count('.') > 1 or (cleaned.count('.') == 1 and len(cleaned.split('.')[1]) == 3):
        cleaned = cleaned.replace('.', '')  # 1.500 = 1500 (VN thousands)
    cleaned = cleaned.replace(',', '.')
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError('Giá trị phải là số.') from None
    if not value.is_finite() or value <= 0:
        raise ValueError('Giá trị phải là số dương.')
    return value


class KpiStore:
    def __init__(self, state, clock=time.time):
        self.state, self.clock = state, clock
        with state._lock, state.db:
            state.db.execute("""CREATE TABLE IF NOT EXISTS kpi_targets(id INTEGER PRIMARY KEY, key TEXT NOT NULL, value TEXT NOT NULL,
                source TEXT NOT NULL, created REAL NOT NULL, valid_to REAL)""")

    def set(self, key, value, source):
        now = self.clock()
        with self.state._lock, self.state.db:
            self.state.db.execute("UPDATE kpi_targets SET valid_to=? WHERE key=? AND valid_to IS NULL", (now, key))
            self.state.db.execute("INSERT INTO kpi_targets(key,value,source,created) VALUES (?,?,?,?)", (key, str(value), source, now))

    def current(self):
        with self.state._lock:
            rows = self.state.db.execute("SELECT key,value,source,created FROM kpi_targets WHERE valid_to IS NULL").fetchall()
        return {r[0]: {'value': Decimal(r[1]), 'source': r[2], 'created': r[3]} for r in rows}

    def targets(self):
        return {k: v for k, v in self.current().items() if k in TARGETS}

    def capacities(self):
        return {k[4:]: v['value'] for k, v in self.current().items() if k.startswith('cap:')}

    def set_target(self, name, value_text, source):
        key = ALIASES.get(name.casefold())
        if not key:
            raise ValueError('Chọn một trong: sla, chiphi_kg, chiphi_don, tai.')
        value = parse_number(value_text)
        if TARGETS[key][1] == '%' and value > 100:
            raise ValueError('Tỷ lệ phải từ 0 đến 100%.')
        self.set(key, value, source)
        return key, value

    def set_capacity(self, plate_text, kg_text, source):
        plate = parse_plate(plate_text)
        if not plate:
            raise ValueError('Biển số chưa đúng dạng, ví dụ 29C12345.')
        m = re.fullmatch(r'([0-9][0-9.,]*)(t|tan|tấn|kg)?', str(kg_text).casefold().replace(' ', ''))
        if not m:
            raise ValueError('Tải trọng ghi dạng 1250, 1250kg hoặc 1.25t.')
        tons = m[2] in ('t', 'tan', 'tấn')
        if tons:  # 1.25t / 1,25t: decimal tons, never a thousands group
            try:
                value = Decimal(m[1].replace(',', '.'))*1000
            except InvalidOperation:
                raise ValueError('Tải trọng không hợp lệ.') from None
        else:
            value = parse_number(m[1])
        self.set('cap:'+plate, value, source)
        return plate, value
