"""Domain agents on the real Supra data: accounting, workforce, KPI, audit, coordinator.

Every number comes from code already used by /chiphi, /sla and /xe (manual cost
report, SLA summary, vehicle evidence, GHN weight summary). These functions only
combine and explain. Missing data stays N/A with the reason; partial subtotals are
never shown as totals. Replies are short, plain Vietnamese.
"""
from decimal import Decimal, ROUND_HALF_UP, localcontext

from .vehicle_report import _vi_money, _vi_number


def vnd(value):
    return _vi_money(Decimal(str(value)).quantize(Decimal(1), rounding=ROUND_HALF_UP))+'đ' if value is not None else 'N/A'


def pct(value, places=1):
    return _vi_number(Decimal(str(value)), places)+'%' if value is not None else 'N/A'


def aliases(evidence):
    """{plate: 'V1'…}, the same temporary vehicle codes /xe shows; replies never print plates."""
    out = {v['plate']: v['alias'] for v in (evidence or {}).get('inventory', []) if v.get('alias')}
    out.update({g['plate']: g['vehicle_alias'] for g in (evidence or {}).get('groups', []) if g.get('vehicle_alias')})
    return out


def car(plate, names):
    return names.get(plate, 'xe chưa có mã')


def span(scope):
    a, b = scope['start'], scope['end']
    fmt = lambda s: s[8:10]+'/'+s[5:7]
    return 'ngày '+fmt(a) if a == b else fmt(a)+'–'+fmt(b)


# ---------------- Kế toán ----------------
def accounting(report, scope, statement=None, reconciliation=None, evidence=None, km=None):
    from .manual_cost_overlay import projected_totals
    from .ncc_invoice import VAT_RATE
    lines = []
    total, _, blockers = projected_totals(report) if report and report.get('available') else (None, None, ['chưa tính được bảng chi phí'])
    if total is not None:
        vat = (Decimal(total)*VAT_RATE).quantize(Decimal(1))
        lines.append(f'Chi phí dự kiến {span(scope)}: {vnd(total)} trước VAT, VAT 8% là {vnd(vat)}, sau VAT {vnd(Decimal(total)+vat)}. '
                     'Đây là số dự kiến theo giá đã duyệt, chưa phải thực chi.')
        gp = projected_totals(report)[1]
        revenue = report['overlay']['revenue']['component_vnd']
        lines.append(f'Doanh thu dự kiến {vnd(revenue)} trước VAT; lãi gộp dự kiến {vnd(gp)} (doanh thu trừ chi phí dự kiến, cùng bảng /chiphi).')
    else:
        lines.append(f'Chi phí dự kiến {span(scope)} chưa chốt được tổng (N/A) vì: '+'; '.join(blockers[:4])+'. Doanh thu và lãi gộp dự kiến cũng N/A tới khi đủ nguồn.')
    if statement is None:
        lines.append('Chưa có bảng kê NCC cho tháng này nên chưa đối soát thực chi. Kế toán gửi file (Excel/CSV) vào nhóm kèm chú thích /import ncc là em đối soát ngay.')
        lines.append(km_text(km, evidence))
        return '\n'.join(lines)
    r = reconciliation
    names = aliases(evidence)
    after = r['amount_vnd']+r['vat_vnd'] if r['vat_vnd'] is not None else None
    lines.append(f'Bảng kê NCC ({r["lines"]} dòng trong khoảng này): {vnd(r["amount_vnd"])} trước VAT; VAT trên bảng kê '
                 f'{vnd(r["vat_vnd"])} (8% đúng ra là {vnd(r["expected_vat_vnd"])}); sau VAT {vnd(after)}.')
    issues = []
    if r['vat_mismatch_rows']:
        issues.append('VAT không đúng 8% ở dòng '+', '.join(map(str, r['vat_mismatch_rows'][:10])))
    if r['total_mismatch_rows']:
        issues.append('tổng sau VAT không khớp ở dòng '+', '.join(map(str, r['total_mismatch_rows'][:10])))
    if r['duplicate_rows']:
        issues.append('trùng biển số + ngày: '+'; '.join(f'dòng {a} và {b}' for a, b in r['duplicate_rows'][:5]))
    if r['billed_without_activity']:
        issues.append('tính tiền ngày không thấy xe chạy trong Raw ở bảng kê dòng '+', '.join(map(str, r['billed_without_activity_rows'][:10])))
    if r['activity_not_billed']:
        issues.append('xe có chạy nhưng chưa có trên bảng kê: '+', '.join(f'{car(p, names)} {d[8:10]}/{d[5:7]}' for p, d in r['activity_not_billed'][:8]))
    if r['unknown_plates']:
        issues.append('biển số không có trong Booking/Raw ở bảng kê dòng '+', '.join(map(str, r['unknown_plate_rows'][:10])))
    if r['held_rows']:
        issues.append('dòng thiếu biển/ngày/tiền chưa đọc được: '+', '.join(map(str, r['held_rows'][:10])))
    for item in r['gxt_compare']:
        if item['diff_vnd']:
            issues.append(f'xe {car(item["plate"], names)} (bảng kê dòng {", ".join(map(str, item["rows"][:6]))}): {vnd(item["billed_vnd"])} cho {item["days"]} ngày, '
                          f'theo giá ngày đã duyệt là {vnd(item["expected_vnd"])} (lệch {vnd(item["diff_vnd"])})')
    if issues:
        lines.append('Cần kế toán kiểm lại:\n'+'\n'.join('• '+i for i in issues))
    else:
        lines.append('Không thấy lệch: VAT đúng 8%, không trùng dòng, ngày tính tiền khớp ngày xe chạy, tiền xe GXT khớp giá ngày đã duyệt.')
    lines.append(km_text(km, evidence))
    return '\n'.join(lines)


def km_text(km, evidence):
    if km is None:
        return 'Chưa có nhật trình km tháng này: NCC gửi file (Excel/CSV, chú thích /import km) để đối chiếu ngày chạy; tiền theo km còn chờ biểu giá km (câu 17).'
    names = aliases(evidence)
    parts = [f'Nhật trình km: {km["lines"]} dòng, tổng {_vi_money(km["km"])} km' if km['km'] is not None else f'Nhật trình km: {km["lines"]} dòng, tổng km N/A']
    if km['logged_without_raw_rows']:
        parts.append('có km nhưng Raw không thấy xe chạy ở nhật trình dòng '+', '.join(map(str, km['logged_without_raw_rows'][:10])))
    if km['raw_without_log']:
        parts.append('xe chạy trên Raw nhưng chưa có trong nhật trình: '+', '.join(f'{car(p, names)} {d[8:10]}/{d[5:7]}' for p, d in km['raw_without_log'][:8]))
    if km['held_rows']:
        parts.append('dòng thiếu biển/ngày/km hoặc km rỗng lớn hơn km chưa đọc: '+', '.join(map(str, km['held_rows'][:10])))
    return '; '.join(parts)+'. Tiền theo km chưa tính vì chưa có biểu giá km (câu 17).'


# ---------------- Nhân sự ----------------
def workforce(report, scope, limit=12, evidence=None, roster=None, timesheet=None):
    if not report or not report.get('available'):
        return 'Chưa tính được lương giao: bảng chi phí kỳ này chưa sẵn sàng (thiếu chính sách hoặc nguồn chưa khớp).'
    gxt = report['gxt']
    names = aliases(evidence)
    groups = sorted(gxt.get('by_group', []), key=lambda g: (g['day'], g['vehicle']))
    lines = []
    if groups:
        with localcontext() as ctx:
            ctx.prec = 60
            wage = sum((Decimal(g['wage_vnd']) for g in groups), Decimal(0))
            kg = sum((Decimal(g['supra_kg']) for g in groups), Decimal(0))
        orders = sum(g['orders'] for g in groups)
        days = len({g['day'] for g in groups})
        lines.append(f'Lương giao GXT {span(scope)}: {vnd(wage)} cho {orders} đơn, {_vi_money(kg)} kg, {len(groups)} lượt xe/ngày trong {days} ngày. '
                     'Tính theo bậc kg của từng đơn; đơn đi 2 chuyến cùng xe/ngày tính 1 lần; giao thẳng không tính lương.')
        top = sorted(groups, key=lambda g: -Decimal(g['wage_vnd']))[:limit]
        lines.append('Theo xe/ngày (cao nhất trước):')
        lines += [f'• {car(g["vehicle"], names)} {g["day"][8:10]}/{g["day"][5:7]}: {g["orders"]} đơn, {_vi_money(g["supra_kg"])} kg, lương {vnd(g["wage_vnd"])}' for g in top]
        if len(groups) > limit:
            lines.append(f'… còn {len(groups)-limit} lượt xe/ngày khác.')
    else:
        lines.append(f'Chưa có lượt xe GXT nào đủ dữ liệu để tính lương giao {span(scope)}.')
    if gxt.get('held'):
        lines.append(f'Còn {len(gxt["held"])} nhóm xe/ngày chưa tính lương (N/A) vì dữ liệu Raw lỗi hoặc xe chở lẫn; nhắn "còn thiếu gì" để xem dòng.')
    points = report.get('points') or {}
    count = sum((points.get('counts') or {}).values())
    if count:
        lines.append(f'Điểm giao BC: {count} điểm, phí {vnd(points.get("eligible_vnd"))} (trả BC theo điểm, không phải lương GXT).')
    lines += people_lines(report, scope, roster, timesheet, evidence)
    return '\n'.join(lines)


def people_lines(report, scope, roster, timesheet, evidence, limit=12):
    from .workforce_data import per_person_wage, timesheet_metrics
    names = aliases(evidence)
    out = []
    if roster is None:
        out.append('Theo từng người: N/A — cần danh sách tài xế–xe theo ngày (owner/điều phối gửi file, chú thích /import taixe).')
    else:
        wages, shared, unassigned, left = per_person_wage(report, roster, scope)
        if wages:
            out.append('Lương giao theo người (từ lương xe/ngày ở trên):')
            out += [f'• {person}: {vnd(w)}' for person, w in sorted(wages.items(), key=lambda kv: -kv[1])[:limit]]
        if shared:
            out.append(f'{len(shared)} xe/ngày có từ 2 người trở lên mà đơn chưa gán ID người (J10): '
                       +', '.join(f'{car(p, names)} {d[8:10]}/{d[5:7]}' for p, d in shared[:6])+'. Thêm cột mã đơn vào danh sách để tính đúng người.')
        if left:
            out.append(f'Lương chưa gán được người: {vnd(left)} (N/A theo người, vẫn nằm trong tổng lương giao).')
        if unassigned:
            out.append(f'{len(unassigned)} xe/ngày chưa có người trong danh sách: '+', '.join(f'{car(p, names)} {d[8:10]}/{d[5:7]}' for p, d in unassigned[:6]))
    if timesheet is None:
        out.append('OT, năng suất, tỷ lệ lỗi: N/A — chưa có chấm công (owner gửi file, chú thích /import chamcong).')
    else:
        m = timesheet_metrics(timesheet, scope)
        def show(value, unit):
            return (pct(value) if unit == '%' else _vi_number(value, 2)+unit) if value is not None else 'N/A'
        out.append(f'Chấm công {m["lines"]} dòng, {m["people"]} người: OT {show(m["ot_rate_pct"], "%")}, '
                   f'năng suất {show(m["productivity"], " sản lượng/giờ")}, tỷ lệ lỗi {show(m["defect_rate_pct"], "%")} '
                   '(công thức có sẵn trong app, chờ owner review J11).')
        if m['held_rows']:
            out.append('Dòng chấm công thiếu/lỗi chưa đọc: '+', '.join(map(str, m['held_rows'][:10])))
    return out


# ---------------- Vận hành ----------------
def operations_stats(data, scope, now, period, cutoff):
    """Counts shared by /vanhanh and the daily brief (no rendering)."""
    from collections import Counter
    from .booking_quantity import quantity_summary
    from .booking_weight import weight_summary
    from .sla import classify_events
    events, stamp = classify_events(data, scope['start'], scope['end'], now)
    rows = data.rows[data.rows.dispatch_date.map(lambda d: d is not None and scope['start'] <= str(d) <= scope['end'])]
    status = Counter(e['status'] for e in events)
    return dict(events=events, stamp=stamp, rows=rows, status=status, by_day=Counter(e['day'] for e in events),
                open=status['pending']+status['overdue']+status['no_rule_open'],
                overdue_sources=[src for e in events if e['status'] == 'overdue' for src in e['sources']],
                products=quantity_summary(data, rows).get('product_units'), kg=weight_summary(data, rows, period, cutoff).get('weight_kg'))


def operations(data, scope, now, period, cutoff, limit_days=10):
    """Volume per Booking E day and backlog (dispatched, no successful DELIVER yet).

    Events are GHN + E day (owner grain). Products use quantity_summary (DO rows, additive
    by day). Kg is shown for the whole range only, with weight_summary (same as the 13h
    report): one GHN on several E days must not count its L twice in a per-day split.
    Backlog statuses come from sla.classify_events, so SLA and backlog count the same events.
    """
    from collections import Counter, defaultdict
    from .booking_quantity import quantity_summary
    from .booking_weight import weight_summary
    from .missing_data import _rows_text
    from .sla import classify_events
    stats = operations_stats(data, scope, now, period, cutoff)
    events, stamp, rows = stats['events'], stats['stamp'], stats['rows']
    lines = []
    if stamp:
        lines.append(f'Vận hành {span(scope)}: tồn đọng N/A vì cột giờ kết thúc chuyến trên Raw là dấu thời gian hàng loạt; '
                     'cần owner chỉ cột giờ giao thật (như /sla).')
        return '\n'.join(lines)
    by_day, status, open_events = stats['by_day'], stats['status'], stats['open']
    products, kg = stats['products'], stats['kg']
    lines.append(f'Vận hành {span(scope)}: {len(events)} đợt xuất, tồn đọng {open_events} đợt '
                 f'({status["overdue"]} quá hạn, {status["pending"]} chưa tới hạn'
                 +(f', {status["no_rule_open"]} ở tỉnh chưa có hạn SLA' if status['no_rule_open'] else '')+').')
    lines.append('Sản phẩm: '+(f'{_vi_money(products)}' if products is not None else 'N/A (cột J thiếu/lỗi/lặp, xem "còn thiếu gì")')+
                 '; kg cả khoảng: '+(f'{_vi_money(kg)} kg' if kg is not None else 'N/A (kg cột L chưa đủ cho mọi GHN)')+'.')
    if by_day:
        lines.append('Theo ngày xuất:')
        days = sorted(by_day)
        for d in days[-limit_days:]:
            day_rows = rows[rows.dispatch_date.map(lambda x: str(x) == d)]
            q = quantity_summary(data, day_rows).get('product_units')
            lines.append(f'• {d[8:10]}/{d[5:7]}: {by_day[d]} đợt, '+(f'{_vi_money(q)} sản phẩm' if q is not None else 'sản phẩm N/A'))
        if len(days) > limit_days:
            lines.append(f'… và {len(days)-limit_days} ngày trước đó.')
    overdue = [e for e in events if e['status'] == 'overdue']
    if overdue:
        groups = defaultdict(list)
        for e in overdue:
            groups[e['day']].extend(e['sources'])
        lines.append('Quá hạn chưa có giao thành công trên Raw:')
        lines += [f'• xuất {d[8:10]}/{d[5:7]}: {_rows_text(refs)}' for d, refs in sorted(groups.items())[:limit_days]]
        lines.append('Việc cần làm: kiểm tra giao hàng các đợt trên; nếu đã giao thì Raw còn thiếu dòng DELIVER thành công.')
    if status['held']:
        lines.append(f'{status["held"]} đợt chưa xác định được đã giao hay chưa (cờ thành công hoặc giờ giao trên Raw lỗi).')
    lines.append('Tồn đọng = đợt đã xuất chưa có DELIVER thành công trên Raw; hạn theo D+1/D+2 đã chốt (J5).')
    return '\n'.join(lines)


# ---------------- Chi phí theo tuyến ----------------
def routes_view(result, scope, limit=15):
    """/tuyen: cost by province/district at each vehicle's own price (owner J1)."""
    names = {'gxt_vehicle': 'xe GXT', 'gxt_wage': 'lương giao', 'points': 'điểm', 'ftl': 'FTL'}
    rows = sorted(result['routes'].items(), key=lambda kv: -kv[1]['total'])
    assigned = sum((v['total'] for _, v in rows), Decimal(0))
    missing = sum(result['missing'].values(), Decimal(0))
    lines = [f'Chi phí theo tuyến {span(scope)}: đã chia {vnd(assigned)} cho {len(rows)} tuyến; chưa xác định được tuyến {vnd(missing)}.']
    for (province, district), v in rows[:limit]:
        parts = ', '.join(f'{names[k]} {vnd(v[k])}' for k in names if v[k])
        unit = f', {vnd(v["total"]/v["events"])}/đợt' if v['events'] else ''
        lines.append(f'• {province.title()} / {district.title()}: {vnd(v["total"])} — {v["events"]} đợt{unit} ({parts})')
    if len(rows) > limit:
        lines.append(f'… và {len(rows)-limit} tuyến khác.')
    if missing:
        lines.append('Chưa xác định được tuyến: '+', '.join(f'{names[k]} {vnd(v)}' for k, v in result['missing'].items() if v)
                     +' (đơn/cửa hàng thuộc nhiều tuyến, xe FTL chở nhiều tuyến hoặc thiếu dòng Booking).')
    if result['pools']:
        lines.append('Chưa chia theo tuyến (khoản dùng chung): '+', '.join(f'{k} {vnd(v)}' for k, v in result['pools'].items())+'.')
    lines.append('Mỗi khoản theo đúng giá xe/nhà xe đã tính trong /chiphi (J1). Chi phí/đợt chỉ gồm phần đã chia, chưa là tổng theo tuyến.')
    return '\n'.join(lines)


# ---------------- Đội xe ----------------
def fleet(scope, evidence, capacities, km=None, report=None):
    """Truck-days, load factor (owner J4: goods ÷ capacity), cost per vehicle-day (J2), km and waiting hours from the trip log."""
    groups = [g for g in evidence.get('groups', []) if scope['start'] <= g['day'] <= scope['end']]
    trucks = len({g['plate'] for g in groups})
    load, counted, missing = load_factor(evidence, scope, capacities)
    lines = [f'Đội xe {span(scope)}: {len(groups)} lượt xe/ngày trên Raw, {trucks} xe; tỷ lệ tải (hàng ÷ tải trọng) '
             +(f'{pct(load)} trên {counted} lượt có tải trọng' if load is not None else 'N/A (chưa có tải trọng xe; owner khai báo bằng /taitrong)')+'.']
    if km is None:
        lines.append('Km, km rỗng, giờ chờ: N/A — chưa có nhật trình km (NCC gửi file, chú thích /import km).')
    else:
        lines.append(f'Theo nhật trình: tổng km {_vi_money(km["km"]) if km["km"] is not None else "N/A"}; km rỗng '
                     +(f'{_vi_money(km["empty_km"])} km' if km['empty_km'] is not None else 'N/A (cột km rỗng thiếu/trống)')
                     +'; giờ chờ '+(f'{_vi_number(km["wait_hours"], 1)} giờ' if km['wait_hours'] is not None else 'N/A (cột giờ chờ thiếu/trống)')+'.')
    lines.append(trip_cost_text(report))
    lines.append('Dự báo nhu cầu xe: chờ file tồn kho và hàng sắp về owner gửi (J3); kịch bản ca xe owner đã duyệt xem bằng /xe.')
    return '\n'.join(lines)


def trip_cost_text(report):
    """Owner J2: a trip is a vehicle/day. Cost per vehicle/day from the /chiphi table; N/A when that part is N/A."""
    if not report or not report.get('available'):
        return 'Chi phí/chuyến (xe/ngày): N/A — bảng chi phí chưa tính được cho khoảng này.'
    g, f = report['gxt'], report['ftl']
    days = g.get('eligible_vehicle_days') or 0
    gxt = (f'xe GXT {vnd(Decimal(g["vehicle_pool_vnd"])/days)}/xe-ngày tiền thuê cả xe, phần Supra {vnd(Decimal(g["vehicle_supra_vnd"])/days)}/xe-ngày '
           f'({days} xe/ngày)') if days and g.get('vehicle_pool_vnd') is not None else 'xe GXT N/A'
    groups = f.get('eligible_groups') or 0
    ftl = (f'FTL {vnd(Decimal(f["eligible_vnd"])/groups)}/xe-ngày ({groups} xe/ngày)' if groups and f.get('eligible_vnd') is not None else 'FTL N/A')
    return f'Chi phí/chuyến (xe/ngày, J2): {gxt}; {ftl}. Nhóm đang giữ N/A không tính vào.'


# ---------------- KPI ----------------
def load_factor(evidence, scope, capacities):
    """(ratio or None, trucks counted, plates without a capacity). Owner capacity first, else one Booking T value."""
    booking = {v['plate']: v.get('booking_declared_capacity_kg') or [] for v in evidence.get('inventory', [])}
    used, missing = Decimal(0), set()
    cap_total, counted = Decimal(0), 0
    for g in evidence.get('groups', []):
        if not scope['start'] <= g['day'] <= scope['end'] or g.get('issues'):
            continue
        cap = capacities.get(g['plate'])
        if cap is None and len(booking.get(g['plate'], [])) == 1:
            cap = Decimal(booking[g['plate']][0])
        if cap is None:
            missing.add(g['plate'])
            continue
        used += Decimal(g['supra_kg'])+Decimal(g['other_kg'])
        cap_total += cap
        counted += 1
    return (used/cap_total*100 if cap_total else None), counted, sorted(missing)


def kpi_rows(targets, scope, sla, report, weight, orders, evidence, capacities):
    """(rows, load_meta): each row = dict(key, name, unit, sense, value, why, target, ok). Shared by /kpi and the daily brief."""
    from .manual_cost_overlay import projected_totals
    from ..kpi_targets import TARGETS
    rows = []
    rate = sla.get('on_time_rate') if sla else None
    rate = Decimal(str(rate))*100 if rate is not None else None  # sla_summary gives a 0–1 fraction
    rows.append(('sla', rate, 'chưa đủ dữ liệu giao hàng' if rate is None else ''))
    total = projected_totals(report)[0] if report and report.get('available') else None
    blockers = projected_totals(report)[2] if report and report.get('available') else ['chưa tính được bảng chi phí']
    kg = Decimal(weight['weight_kg']) if weight and weight.get('weight_kg') else None
    rows.append(('chiphi_kg', Decimal(total)/kg if total is not None and kg else None,
                 'tổng chi phí chưa chốt: '+blockers[0] if total is None else ('chưa đủ kg cột L' if not kg else '')))
    rows.append(('chiphi_don', Decimal(total)/orders if total is not None and orders else None,
                 'tổng chi phí chưa chốt: '+blockers[0] if total is None else ('không có đơn' if not orders else '')))
    load, counted, missing = load_factor(evidence, scope, capacities)
    names = aliases(evidence)
    rows.append(('tai', load, ('chưa có tải trọng xe '+', '.join(car(p, names) for p in missing[:6])+'; xem mã xe bằng /xe') if load is None else ''))
    out = []
    for key, value, why in rows:
        name, unit, sense = TARGETS[key]
        target = targets.get(key)
        ok = None
        if value is not None and target:
            ok = value >= target['value'] if sense == 'min' else value <= target['value']
        out.append(dict(key=key, name=name, unit=unit, sense=sense, value=value, why=why, target=target['value'] if target else None, ok=ok))
    return out, dict(load=load, counted=counted, missing=missing)


def kpi_value(row, value=None):
    value = row['value'] if value is None else value
    return (pct(value) if row['unit'] == '%' else vnd(value)+row['unit'][1:]) if value is not None else 'N/A'


def kpi_goal(row):
    goal = pct(row['target'], 0) if row['unit'] == '%' else vnd(row['target'])+row['unit'][1:]
    return f'mục tiêu {"≥" if row["sense"] == "min" else "≤"} {goal}'


def previous_scope(scope):
    """The same-length window just before ``scope`` (OWNER J7 proposal A)."""
    from datetime import date, timedelta
    start, end = date.fromisoformat(scope['start']), date.fromisoformat(scope['end'])
    prev_end = start-timedelta(days=1)
    return {'start': str(prev_end-(end-start)), 'end': str(prev_end)}


def kpi_delta(row, before):
    """'tăng 2,0 điểm %' / 'giảm 150đ/kg' versus the previous window, or None when either side is N/A."""
    if row['value'] is None or before is None or before['value'] is None:
        return None
    diff = row['value']-before['value']
    if not diff:
        return 'không đổi'
    word = 'tăng' if diff > 0 else 'giảm'
    amount = _vi_number(abs(diff), 1)+' điểm %' if row['unit'] == '%' else vnd(abs(diff))+row['unit'][1:]
    return f'{word} {amount}'


def worsened(row, before):
    """Owner J6: warn when a KPI moves the wrong way versus the previous window (any amount)."""
    if row['value'] is None or before['value'] is None:
        return False
    return row['value'] < before['value'] if row['sense'] == 'min' else row['value'] > before['value']


def kpi(targets, scope, sla, report, weight, orders, evidence, capacities, previous=None):
    """previous: {'scope', 'rows'} for the window before, or {'scope', 'reason'} when it cannot be computed."""
    rows, meta = kpi_rows(targets, scope, sla, report, weight, orders, evidence, capacities)
    before = {r['key']: r for r in (previous or {}).get('rows', [])}
    load, counted, missing = meta['load'], meta['counted'], meta['missing']
    lines = [f'KPI {span(scope)}:']
    passed = sum(r['ok'] is True for r in rows)
    failed = sum(r['ok'] is False for r in rows)
    for r in rows:
        verdict = ''
        if r['ok'] is not None:
            verdict = (' — đạt' if r['ok'] else ' — CHƯA ĐẠT')+f' ({kpi_goal(r)})'
        elif r['value'] is not None:
            verdict = ' — chưa đặt mục tiêu'
        delta = kpi_delta(r, before.get(r['key'])) if before else None
        trend = f'; so với kỳ trước {delta}' if delta else ''
        lines.append(f'• {r["name"]}: {kpi_value(r)}{verdict}{trend}'+(f' ({r["why"]})' if r['why'] else ''))
    misses = [f'{r["name"]} {kpi_value(r)}, {kpi_goal(r)}' for r in rows if r['ok'] is False]
    worse = [f'{r["name"]} {kpi_delta(r, before[r["key"]])} so với kỳ trước' for r in rows
             if r['key'] in before and kpi_delta(r, before[r['key']]) and worsened(r, before[r['key']])]
    if misses or worse:
        lines.insert(1, 'Cảnh báo: '+'; '.join(misses+worse)+'.')
    if previous:
        ps = previous['scope']
        lines.append(f'Kỳ trước để so sánh: {span(ps)} (cùng số ngày, J7)'+
                     (f'; chưa so được vì {previous["reason"]}.' if previous.get('reason') else '. Chỉ số N/A ở một trong hai kỳ thì không so.'))
    if load is not None and missing:
        lines.append(f'Tỷ lệ tải tính trên {counted} lượt xe/ngày có tải trọng; {len(missing)} xe chưa có tải trọng nên chưa tính.')
    if load is not None:
        lines.append('Tải trọng lấy theo owner khai báo hoặc cột T Booking; chưa phải cân thực tế.')
    if not targets:
        lines.append('Chưa có mục tiêu nào. Owner đặt bằng /muctieu sla 95, /muctieu chiphi_kg 1500, /muctieu chiphi_don 20000, /muctieu tai 80.')
    head = f'{passed} chỉ số đạt, {failed} chưa đạt' if passed+failed else 'chưa chấm được chỉ số nào'
    lines[0] = f'KPI {span(scope)}: {head}.'
    return '\n'.join(lines)


# ---------------- Kiểm toán ----------------
def audit_checks(report, evidence, sla, scope, reconciliation=None, km=None):
    """[(name, ok, detail)]: independent re-checks of the code's own results, then document checks.

    ok=None means N/A (the document has not been sent yet): shown, not counted as passed or failed.
    """
    from .manual_cost_overlay import projected_totals
    checks = []
    names = aliases(evidence)
    def check(name, ok, detail=''):
        checks.append((name, ok, detail))
    raw_last = evidence.get('raw_last_day')
    check('Raw đã cập nhật tới cuối khoảng xem', bool(raw_last) and str(raw_last)[:10] >= scope['end'],
          f'Raw mới tới {str(raw_last)[:10] if raw_last else "chưa rõ"}')
    groups = [g for g in evidence.get('groups', []) if scope['start'] <= g['day'] <= scope['end']]
    bad = [g for g in groups if g.get('issues')]
    check('Nhóm xe/ngày trong Raw không có dòng lỗi', not bad, f'{len(bad)}/{len(groups)} nhóm có lỗi' if bad else '')
    if report and report.get('available'):
        gxt = report['gxt']
        by = gxt.get('by_group', [])
        with localcontext() as ctx:
            ctx.prec = 100
            if gxt.get('vehicle_supra_vnd') is not None:
                share = sum((Decimal(g['vehicle_supra_vnd']) for g in by), Decimal(0))
                check('Tiền xe GXT phần Supra = tổng từng xe/ngày', share == Decimal(gxt['vehicle_supra_vnd']),
                      f'{vnd(share)} so với {vnd(gxt["vehicle_supra_vnd"])}')
                check('Phần Supra không vượt tiền thuê xe', Decimal(gxt['vehicle_supra_vnd']) <= Decimal(gxt['vehicle_pool_vnd']))
                wage = sum((Decimal(g['wage_vnd']) for g in by), Decimal(0))
                check('Lương giao = tổng từng xe/ngày', wage == Decimal(gxt['wage_supra_vnd']), f'{vnd(wage)} so với {vnd(gxt["wage_supra_vnd"])}')
                over = [g for g in by if Decimal(g['vehicle_supra_vnd']) > Decimal(g['vehicle_day_vnd'])]
                check('Không xe/ngày nào bị chia quá 100% tiền xe', not over, ', '.join(f'{car(g["vehicle"], names)} {g["day"]}' for g in over[:5]))
            kg_by = {(g['vehicle'], g['day']): Decimal(g['supra_kg']) for g in by}
            raw_kg = {(g['plate'], g['day']): Decimal(g['supra_kg']) for g in groups}
            diff = [k for k, v in kg_by.items() if k in raw_kg and raw_kg[k] != v]
            check('Kg Supra trong bảng chi phí khớp kg Raw từng xe/ngày', not diff, ', '.join(f'{car(p, names)} {d}' for p, d in diff[:5]))
        total, _, blockers = projected_totals(report)
        check('Tổng dự kiến chỉ công bố khi mọi phần đủ nguồn', total is None or not blockers)
        held = len(gxt.get('held', []))+len(report['ftl'].get('held', []))+len(report['points'].get('held', []))
        check('Không còn nhóm giữ riêng (N/A)', held == 0, f'{held} nhóm đang giữ' if held else '')
    else:
        check('Bảng chi phí tính được cho khoảng này', False, (report or {}).get('reason', 'chưa có'))
    if sla:
        check('Giờ kết thúc chuyến trong Raw không bị đóng dấu hàng loạt', not sla.get('batch_stamp'), 'có dấu thời gian hàng loạt, SLA có thể sai' if sla.get('batch_stamp') else '')
    r = reconciliation
    if r is None:
        check('Đối soát bảng kê NCC', None, 'chưa có bảng kê tháng này (kế toán gửi, /import ncc)')
    else:
        check('Bảng kê NCC: VAT đúng 8% và tổng sau VAT khớp', not r['vat_mismatch_rows'] and not r['total_mismatch_rows'],
              'dòng '+', '.join(map(str, (r['vat_mismatch_rows']+r['total_mismatch_rows'])[:8])))
        check('Bảng kê NCC: không trùng biển số + ngày', not r['duplicate_rows'], '; '.join(f'dòng {a} và {b}' for a, b in r['duplicate_rows'][:4]))
        check('Bảng kê NCC: chỉ tính tiền ngày xe có chạy trên Raw', not r['billed_without_activity'],
              'bảng kê dòng '+', '.join(map(str, r['billed_without_activity_rows'][:8])))
        check('Bảng kê NCC: tiền xe GXT khớp giá ngày đã duyệt', not any(x['diff_vnd'] for x in r['gxt_compare']),
              ', '.join(f'{car(x["plate"], names)} lệch {vnd(x["diff_vnd"])}' for x in r['gxt_compare'] if x['diff_vnd'])[:200])
    if km is None:
        check('Đối chiếu nhật trình km', None, 'chưa có nhật trình tháng này (NCC gửi, /import km)')
    else:
        check('Nhật trình km: ngày có km đều thấy xe chạy trên Raw', not km['logged_without_raw_rows'],
              'nhật trình dòng '+', '.join(map(str, km['logged_without_raw_rows'][:8])))
    return checks


def audit(report, evidence, sla, scope, today=None, reconciliation=None, km=None):
    """Returns (passed, counted, text); N/A document checks are listed but not counted."""
    checks = audit_checks(report, evidence, sla, scope, reconciliation, km)
    counted = [c for c in checks if c[1] is not None]
    passed = sum(ok for _, ok, _ in counted)
    lines = [f'Kiểm toán {span(scope)}: đạt {passed}/{len(counted)} phép kiểm.']
    fails = [(n, d) for n, ok, d in checks if ok is False]
    if fails:
        lines.append('Chưa đạt:')
        lines += [f'• {n}'+(f': {d}' if d else '') for n, d in fails]
    else:
        lines.append('Mọi phép kiểm đã chạy đều khớp.')
    missing = [(n, d) for n, ok, d in checks if ok is None]
    if missing:
        lines.append('Chưa kiểm được (N/A): '+'; '.join(f'{n} — {d}' for n, d in missing)+'.')
    lines.append('Phần tính lại dùng cùng nguồn Booking/Raw; phần chứng từ chỉ có khi đã nhận bảng kê/nhật trình.')
    return passed, len(counted), '\n'.join(lines)


# ---------------- Điều phối ----------------
# Who acts on each kind of item (owner approved J8 on 05/11/2026; details in docs/DOMAIN_AGENTS.md).
ROLE = {'decide': 'owner', 'booking': 'CS (Booking)', 'raw': 'điều phối GXT (Raw)', 'data': 'CS / điều phối GXT', 'overdue': 'điều phối GXT', 'kpi': 'owner', 'audit': 'owner',
        'ncc': 'kế toán', 'km': 'NCC', 'targets': 'owner (/muctieu)', 'capacity': 'owner (/taitrong)'}


def who_for(line):
    """Suggested owner for one /thieu line, from its "Cần: …" text (prices, rules, confirmations → owner)."""
    import re
    from ..intent import _plain
    need = _plain(line.rsplit('Cần:', 1)[-1])
    if re.search(r'gia|xac nhan|owner|quy tac|chinh sach|duyet|mo hinh', need):
        return ROLE['decide']
    if re.search(r'\braw\b|bien s\b|chuyen|ngay load', need):
        return ROLE['raw']
    if re.search(r'cot l|bien ag|booking|cs\b|kg', need):
        return ROLE['booking']
    return ROLE['data']


def daily_brief(scope, report, missing, ops, kpi_result, checks, has_statement, parts):
    """The coordinator's daily brief: conclusion, ranked actions with a suggested owner, data to supply, key numbers.

    missing = (count, text) from missing_checklist; ops = operations_stats; kpi_result = (rows, meta);
    checks = audit_checks; parts = [(label, first line)] for the key-number section.
    Ranking (presentation only): data that blocks the cost total, overdue deliveries, KPI misses, audit failures.
    """
    from .manual_cost_overlay import projected_totals
    from .missing_data import _rows_text
    actions, needs = [], []
    count, text = missing if missing else (None, '')
    if count:
        groups = {}
        for line in (l for l in text.split('\n') if l.startswith('• ')):
            groups.setdefault(who_for(line), []).append(line)
        for who, lines_ in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            what = 'Quyết/duyệt' if who == ROLE['decide'] else 'Bổ sung'
            actions.append((f'{what} {len(lines_)} nhóm dữ liệu đang giữ N/A (chặn tổng chi phí)', who, lines_[:3]))
    if ops and not ops['stamp'] and ops['status']['overdue']:
        actions.append((f'Kiểm tra {ops["status"]["overdue"]} đợt quá hạn chưa có giao thành công', ROLE['overdue'],
                        ['• '+_rows_text(ops['overdue_sources'])]))
    rows, meta = kpi_result if kpi_result else ([], {})
    misses = [r for r in rows if r['ok'] is False]
    if misses:
        actions.append(('KPI chưa đạt: '+'; '.join(f'{r["name"]} {kpi_value(r)} ({kpi_goal(r)})' for r in misses), ROLE['kpi'], []))
    fails = [(n, d) for n, ok, d in (checks or []) if ok is False]
    if fails:
        actions.append((f'Kiểm toán chưa đạt {len(fails)} phép', ROLE['audit'], ['• '+n+(f': {d}' if d else '') for n, d in fails[:3]]))
    if not has_statement:
        needs.append(('Bảng kê NCC tháng này (Excel/CSV, chú thích /import ncc) để đối soát thực chi', ROLE['ncc']))
    if report and report.get('available') and (report.get('unpriced_ncc_routes') or (report.get('remote') and report['remote'].get('km_extra_vnd') is None)):
        needs.append(('Nhật trình km NCC (ngày, biển, tuyến, km) để tính cước theo km', ROLE['km']))
    if rows and not any(r['target'] is not None for r in rows):
        needs.append(('Mục tiêu KPI (SLA, chi phí/kg, chi phí/đơn, tỷ lệ tải)', ROLE['targets']))
    if meta.get('missing'):
        needs.append((f'Tải trọng của {len(meta["missing"])} xe chưa có (mã xe xem bằng /xe)', ROLE['capacity']))
    total = projected_totals(report)[0] if report and report.get('available') else None
    blockers = projected_totals(report)[2] if report and report.get('available') else ['chưa tính được bảng chi phí']
    cost = 'tổng chi phí dự kiến đã đủ nguồn' if total is not None else 'tổng chi phí chưa chốt ('+blockers[0]+')'
    backlog = (f'; tồn đọng {ops["open"]} đợt ({ops["status"]["overdue"]} quá hạn)' if ops and not ops['stamp'] else '')
    lines = [f'Báo cáo ngày {span(scope)}: {len(actions)} việc cần xử lý; {cost}{backlog}.']
    if actions:
        lines.append('Việc ưu tiên:')
        for i, (what, who, detail) in enumerate(actions, 1):
            lines.append(f'{i}. {what}. Phụ trách: {who}.')
            lines += ['   '+d for d in detail]
    if needs:
        lines.append('Cần bổ sung:')
        lines += [f'• {what} — {who}' for what, who in needs]
    lines.append('Số chính:')
    for label, first in parts:
        lines.append(f'• {label}: {first}')
    lines.append('Người phụ trách theo phân công owner đã duyệt (J8). Nhắn tên mảng để xem chi tiết.')
    return '\n'.join(lines)


def first_line(text):
    """First line of an agent reply, without a prefix the label already says."""
    import re
    first = (text or 'N/A').strip().split('\n', 1)[0]
    first = re.sub(r'^CẦN ANH BỔ SUNG(?: — [^:]+)?: (\d+ nhóm dữ liệu)\..*$', r'còn \1 cần bổ sung.', first)
    first = re.sub(r'^(KPI|Kiểm toán|Vận hành|Lương giao GXT|Chi phí dự kiến|Đội xe) [^:]+: ', '', first)
    return first[:1].lower()+first[1:] if first[1:2].islower() else first

