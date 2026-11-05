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


def span(scope):
    a, b = scope['start'], scope['end']
    fmt = lambda s: s[8:10]+'/'+s[5:7]
    return 'ngày '+fmt(a) if a == b else fmt(a)+'–'+fmt(b)


# ---------------- Kế toán ----------------
def accounting(report, scope, statement=None, reconciliation=None):
    from .manual_cost_overlay import projected_totals
    from .ncc_invoice import VAT_RATE
    lines = []
    total, _, blockers = projected_totals(report) if report and report.get('available') else (None, None, ['chưa tính được bảng chi phí'])
    if total is not None:
        vat = (Decimal(total)*VAT_RATE).quantize(Decimal(1))
        lines.append(f'Chi phí dự kiến {span(scope)}: {vnd(total)} trước VAT, VAT 8% là {vnd(vat)}, sau VAT {vnd(Decimal(total)+vat)}. '
                     'Đây là số dự kiến theo giá đã duyệt, chưa phải thực chi.')
    else:
        lines.append(f'Chi phí dự kiến {span(scope)} chưa chốt được tổng (N/A) vì: '+'; '.join(blockers[:4])+'.')
    if statement is None:
        lines.append('Chưa có bảng kê NCC cho tháng này nên chưa đối soát thực chi. Kế toán gửi file (Excel/CSV) vào nhóm kèm chú thích /import ncc là em đối soát ngay.')
        return '\n'.join(lines)
    r = reconciliation
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
        issues.append('tính tiền ngày không thấy xe chạy trong Raw: '+', '.join(f'{p} {d[8:10]}/{d[5:7]}' for p, d in r['billed_without_activity'][:8]))
    if r['activity_not_billed']:
        issues.append('xe có chạy nhưng chưa có trên bảng kê: '+', '.join(f'{p} {d[8:10]}/{d[5:7]}' for p, d in r['activity_not_billed'][:8]))
    if r['unknown_plates']:
        issues.append('biển số không có trong Booking/Raw: '+', '.join(r['unknown_plates'][:8]))
    if r['held_rows']:
        issues.append('dòng thiếu biển/ngày/tiền chưa đọc được: '+', '.join(map(str, r['held_rows'][:10])))
    for item in r['gxt_compare']:
        if item['diff_vnd']:
            issues.append(f'xe {item["plate"]}: bảng kê {vnd(item["billed_vnd"])} cho {item["days"]} ngày, theo giá ngày đã duyệt là {vnd(item["expected_vnd"])} (lệch {vnd(item["diff_vnd"])})')
    if issues:
        lines.append('Cần kế toán kiểm lại:\n'+'\n'.join('• '+i for i in issues))
    else:
        lines.append('Không thấy lệch: VAT đúng 8%, không trùng dòng, ngày tính tiền khớp ngày xe chạy, tiền xe GXT khớp giá ngày đã duyệt.')
    lines.append('Tiền FTL/NCC theo km chưa đối chiếu được vì chưa có nhật trình km.')
    return '\n'.join(lines)


# ---------------- Nhân sự ----------------
def workforce(report, scope, limit=12):
    if not report or not report.get('available'):
        return 'Chưa tính được lương giao: bảng chi phí kỳ này chưa sẵn sàng (thiếu chính sách hoặc nguồn chưa khớp).'
    gxt = report['gxt']
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
        lines += [f'• {g["vehicle"]} {g["day"][8:10]}/{g["day"][5:7]}: {g["orders"]} đơn, {_vi_money(g["supra_kg"])} kg, lương {vnd(g["wage_vnd"])}' for g in top]
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
    lines.append('Chưa có tên nhân viên/tài xế theo xe nên chưa tính được theo từng người.')
    return '\n'.join(lines)


# ---------------- Vận hành ----------------
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
    events, stamp = classify_events(data, scope['start'], scope['end'], now)
    rows = data.rows[data.rows.dispatch_date.map(lambda d: d is not None and scope['start'] <= str(d) <= scope['end'])]
    lines = []
    if stamp:
        lines.append(f'Vận hành {span(scope)}: tồn đọng N/A vì cột giờ kết thúc chuyến trên Raw là dấu thời gian hàng loạt; '
                     'cần owner chỉ cột giờ giao thật (như /sla).')
        return '\n'.join(lines)
    by_day = Counter(e['day'] for e in events)
    status = Counter(e['status'] for e in events)
    open_events = status['pending']+status['overdue']+status['no_rule_open']
    quantity = quantity_summary(data, rows)
    weight = weight_summary(data, rows, period, cutoff)
    products = quantity.get('product_units')
    kg = weight.get('weight_kg')
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
    lines.append('Tồn đọng = đợt đã xuất chưa có DELIVER thành công trên Raw; hạn theo D+1/D+2 đã chốt (OWNER J5).')
    return '\n'.join(lines)


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


def kpi(targets, scope, sla, report, weight, orders, evidence, capacities):
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
    rows.append(('tai', load, ('chưa có tải trọng xe: '+', '.join(missing[:6])) if load is None else ''))
    lines = [f'KPI {span(scope)}:']
    passed = failed = 0
    for key, value, why in rows:
        name, unit, sense = TARGETS[key]
        shown = (pct(value) if unit == '%' else vnd(value)+unit[1:]) if value is not None else 'N/A'
        target = targets.get(key)
        verdict = ''
        if value is not None and target:
            ok = value >= target['value'] if sense == 'min' else value <= target['value']
            passed, failed = passed+ok, failed+(not ok)
            goal = (pct(target['value'], 0) if unit == '%' else vnd(target['value'])+unit[1:])
            verdict = (' — đạt' if ok else ' — CHƯA ĐẠT')+f' (mục tiêu {"≥" if sense == "min" else "≤"} {goal})'
        elif value is not None:
            verdict = ' — chưa đặt mục tiêu'
        lines.append(f'• {name}: {shown}{verdict}'+(f' ({why})' if why else ''))
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
def audit(report, evidence, sla, scope, today=None):
    """Independent re-checks of the code's own results. Returns (passed, total, text)."""
    from .manual_cost_overlay import projected_totals
    checks = []
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
                check('Không xe/ngày nào bị chia quá 100% tiền xe', not over, ', '.join(f'{g["vehicle"]} {g["day"]}' for g in over[:5]))
            kg_by = {(g['vehicle'], g['day']): Decimal(g['supra_kg']) for g in by}
            raw_kg = {(g['plate'], g['day']): Decimal(g['supra_kg']) for g in groups}
            diff = [k for k, v in kg_by.items() if k in raw_kg and raw_kg[k] != v]
            check('Kg Supra trong bảng chi phí khớp kg Raw từng xe/ngày', not diff, ', '.join(f'{p} {d}' for p, d in diff[:5]))
        total, _, blockers = projected_totals(report)
        check('Tổng dự kiến chỉ công bố khi mọi phần đủ nguồn', total is None or not blockers)
        held = len(gxt.get('held', []))+len(report['ftl'].get('held', []))+len(report['points'].get('held', []))
        check('Không còn nhóm giữ riêng (N/A)', held == 0, f'{held} nhóm đang giữ' if held else '')
    else:
        check('Bảng chi phí tính được cho khoảng này', False, (report or {}).get('reason', 'chưa có'))
    if sla:
        check('Giờ kết thúc chuyến trong Raw không bị đóng dấu hàng loạt', not sla.get('batch_stamp'), 'có dấu thời gian hàng loạt, SLA có thể sai' if sla.get('batch_stamp') else '')
    passed = sum(ok for _, ok, _ in checks)
    lines = [f'Kiểm toán {span(scope)}: đạt {passed}/{len(checks)} phép kiểm.']
    fails = [(n, d) for n, ok, d in checks if not ok]
    if fails:
        lines.append('Chưa đạt:')
        lines += [f'• {n}'+(f': {d}' if d else '') for n, d in fails]
    else:
        lines.append('Mọi phép cộng lại, đối chiếu kg và điều kiện công bố tổng đều khớp.')
    lines.append('Đây là kiểm tra lại bằng code trên cùng nguồn; chưa thay đối soát chứng từ.')
    return passed, len(checks), '\n'.join(lines)


# ---------------- Điều phối ----------------
def overview(parts):
    """parts: [(label, text)] from the other agents; first line of each, in one message."""
    lines = ['Tổng quan nhanh từng mảng:']
    import re
    for label, text in parts:
        first = (text or 'N/A').strip().split('\n', 1)[0]
        first = re.sub(r'^CẦN ANH BỔ SUNG(?: — [^:]+)?: (\d+ nhóm dữ liệu)\..*$', r'còn \1 cần bổ sung.', first)
        first = re.sub(r'^(KPI|Kiểm toán) [^:]+: ', '', first)  # the label already says it
        lines.append(f'• {label}: {first[:1].lower()+first[1:] if first[1:2].islower() else first}')
    lines.append('Nhắn tên mảng (chi phí, SLA, xe, lương giao, KPI, kiểm toán, kế toán, còn thiếu gì) để xem chi tiết.')
    return '\n'.join(lines)
