"""Delivery SLA by owner rule (28/10/2026; delivery time = time_ket_thuc_chuyen, owner 29/10/2026); code-only, no inference from Booking status.

Deadline: Booking E date + D days, until 23:59 Asia/Ho_Chi_Minh. D=1 for
Phú Thọ, D=2 for Sơn La, Điện Biên, Lai Châu; other provinces have no rule.
Actual: latest ``time_ket_thuc_chuyen`` of Raw DELIVER rows with ``is_succeeded`` true
for that GHN, loaded from the E day until the GHN's next E day. Missing or
unreadable evidence is held (N/A), never counted on time or late.

If ``time_ket_thuc_chuyen`` is a batch stamp rather than a delivery time (one exact
moment shared by succeeded rows of several load days and a large share of the
source), the whole SLA is N/A until the owner names the delivery-time column.
"""
from collections import defaultdict
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .booking_handling import geography
from .booking_quantity import business_key, cell
from .booking_vehicles import RAW_HEADERS, day

TZ = ZoneInfo('Asia/Ho_Chi_Minh')
SLA_DAYS = {'phú thọ': 1, 'sơn la': 2, 'điện biên': 2, 'lai châu': 2}
TRUE = {'true', '1', '1.0', 'yes', 'y', 'x', 'đúng', 'thành công'}
FALSE = {'false', '0', '0.0', 'no', 'n', 'sai', 'thất bại'}
# A real delivery moment is not shared by loads of 3+ different days covering 20%+ of succeeded rows.
BATCH_MIN_LOAD_DAYS, BATCH_MIN_SHARE, BATCH_MIN_ROWS = 3, 0.2, 10


def batch_stamp(raw_rows):
    """The shared ``time_ket_thuc_chuyen`` of succeeded DELIVER rows when it is a batch stamp, else None."""
    by_moment = defaultdict(lambda: [0, set()])
    total = 0
    for r in raw_rows:
        if cell(r['type']).upper() != 'DELIVER' or cell(r['is_succeeded']).casefold() not in TRUE:
            continue
        moment = _moment(r['time_ket_thuc_chuyen'])
        if moment is None:
            continue
        total += 1
        item = by_moment[moment]
        item[0] += 1
        if (load := day(r['load_date'])) is not None:
            item[1].add(load)
    if total < BATCH_MIN_ROWS:
        return None
    moment, (rows, loads) = max(by_moment.items(), key=lambda kv: kv[1][0])
    if len(loads) >= BATCH_MIN_LOAD_DAYS and rows/total >= BATCH_MIN_SHARE:
        return dict(moment=moment.isoformat(timespec='minutes'), rows=rows, total=total, load_days=len(loads))
    return None


def _moment(value):
    text = cell(value)
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%d/%m/%Y %H:%M:%S', '%d/%m/%Y %H:%M'):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=TZ)
        except ValueError:
            pass
    try:
        moment = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        return None
    return moment.astimezone(TZ) if moment.tzinfo else moment.replace(tzinfo=TZ)


def sla_summary(data, start, end, now):
    """Events are GHN + Booking E day inside [start, end] (ISO dates)."""
    pair = data.vehicle_pair
    events = defaultdict(list)
    for row in data.rows.to_dict('records'):
        d = day(row['dispatch_date'])
        if row['order_id'] and d is not None:
            events[(row['order_id'], d)].append(row)
    days_by_order = defaultdict(list)
    for order, d in events:
        days_by_order[order].append(d)
    raw = defaultdict(list)
    for item in pair['data_supra']['rows']:
        r = dict(zip(RAW_HEADERS, item['values']))
        if cell(r['type']).upper() == 'DELIVER' and business_key(r['order_code']):
            raw[business_key(r['order_code'])].append((r, f"Raw#{pair['raw_digest'][:12]} dòng {item['sheet_row']}"))
    out = dict(on_time=0, late=0, pending=0, held=[], late_events=[], no_rule=0)
    stamp = batch_stamp(r for rows in raw.values() for r, _ in rows)
    if stamp:
        return {**out, 'on_time_rate': None, 'complete': False, 'batch_stamp': stamp}
    for (order, d), rows in sorted(events.items()):
        if not start <= str(d) <= end:
            continue
        refs = [r['_source'] for r in rows]
        provinces = {geography(r['source_province'], province=True) for r in rows}
        rule = {SLA_DAYS.get(p) for p in provinces}
        if len(rule) != 1 or None in rule:
            out['no_rule'] += 1
            out['held'].append(dict(day=str(d), reason='no_sla_rule', sources=refs))
            continue
        deadline = datetime.combine(d+timedelta(days=rule.pop()), time(23, 59, 59), TZ)
        later = [x for x in days_by_order[order] if x > d]
        until = min(later) if later else None
        succeeded, problems, raw_refs = [], set(), []
        for r, ref in raw[order]:
            load = day(r['load_date'])
            if load is None or load < d or (until is not None and load >= until):
                continue
            flag = cell(r['is_succeeded']).casefold()
            if flag in FALSE:
                continue
            if flag not in TRUE:
                problems.add('unknown_success_flag')
                raw_refs.append(ref)
                continue
            moment = _moment(r['time_ket_thuc_chuyen'])
            if moment is None:
                problems.add('invalid_delivery_time')
            else:
                succeeded.append(moment)
            raw_refs.append(ref)
        if problems:
            out['held'].append(dict(day=str(d), reason=','.join(sorted(problems)), sources=refs+raw_refs))
        elif not succeeded:
            if now <= deadline:
                out['pending'] += 1
            else:
                out['held'].append(dict(day=str(d), reason='no_successful_delivery_record', sources=refs))
        elif max(succeeded) <= deadline:
            out['on_time'] += 1
        else:
            out['late'] += 1
            out['late_events'].append(dict(day=str(d), delivered=max(succeeded).isoformat(timespec='minutes'),
                deadline=deadline.isoformat(timespec='minutes'), sources=refs+raw_refs))
    measured = out['on_time']+out['late']
    out['on_time_rate'] = (out['on_time']/measured) if measured else None
    out['complete'] = not out['held']
    return out


def render_sla(result, start, end):
    from .missing_data import _rows_text, _vi_day
    span = _vi_day(start)+('' if start == end else '–'+_vi_day(end))
    if (stamp := result.get('batch_stamp')):
        when = stamp['moment'][:16].replace('T', ' ')
        return '\n'.join([
            f"Kết luận SLA {span}: N/A — cột time_ket_thuc_chuyen của Raw chưa phải giờ giao: {stamp['rows']}/{stamp['total']} dòng giao thành công "
            f"của {stamp['load_days']} ngày load khác nhau có cùng một mốc {when} (giờ cập nhật hàng loạt). Em chưa tính đúng/trễ hạn.",
            'Việc cần làm: yêu cầu Raw ghi giờ kết thúc chuyến thật cho từng dòng giao; em không tự đổi sang cột khác.'])
    rate = 'N/A' if result['on_time_rate'] is None else f"{result['on_time_rate']*100:.1f}".replace('.', ',')+'%'
    head = (f"Kết luận SLA {span}: đúng hạn {rate} trên {result['on_time']+result['late']} đợt đo được "
            f"({result['on_time']} đúng hạn, {result['late']} trễ); {result['pending']} đợt chưa tới hạn.")
    if result['held']:
        head += f" {len(result['held'])} đợt chưa đo được, không tính vào tỷ lệ."
    lines = [head, 'Quy tắc: Phú Thọ D+1; Sơn La, Điện Biên, Lai Châu D+2, tính từ ngày xuất (cột E) đến 23:59; giờ giao lấy time_ket_thuc_chuyen của dòng Raw DELIVER thành công.']
    labels = {'no_sla_rule': 'Tỉnh chưa có quy tắc SLA', 'no_successful_delivery_record': 'Quá hạn nhưng Raw chưa có dòng giao thành công',
              'invalid_delivery_time': 'Giờ giao (time_ket_thuc_chuyen) sai định dạng', 'unknown_success_flag': 'Cột is_succeeded không rõ đúng/sai'}
    grouped = defaultdict(list)
    for item in result['held']:
        for code in item['reason'].split(','):
            grouped[code] += item['sources']
    for code, refs in grouped.items():
        lines.append(f'• {labels.get(code, code)} — {_rows_text(refs)}.')
    for item in result['late_events'][:5]:
        lines.append(f"• Trễ: đợt {_vi_day(item['day'])}, giao {item['delivered'][:16].replace('T', ' ')}, hạn {item['deadline'][:16].replace('T', ' ')} — {_rows_text(item['sources'])}.")
    if len(result['late_events']) > 5:
        lines.append(f"Còn {len(result['late_events'])-5} đợt trễ khác.")
    return '\n'.join(lines)
