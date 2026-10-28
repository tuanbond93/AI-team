"""Bounded code-only views; complete evidence remains in the captured RAM record."""
from decimal import Decimal,localcontext,ROUND_HALF_UP
from datetime import datetime,date,timedelta,timezone
from calendar import monthrange
from zoneinfo import ZoneInfo
from .booking_vehicles import vehicle_evidence,RAW_PIN

ROLE_LABELS={'TRANSFER_LINEHAUL':'xe trung chuyển','GXT_LASTMILE':'GXT giao',
    'DIRECT_GXT':'GXT lấy thẳng','DIRECT_NCC':'NCC đi thẳng'}
FLAG_LABELS={'missing_booking_vehicle_rows':'dòng Booking thiếu/sai biển AG',
    'unknown_booking_mode_rows':'dòng Booking chưa khớp cơ chế xe',
    'unknown_or_conflicting_mode':'cơ chế xe chưa rõ hoặc mâu thuẫn',
    'multiple_booking_vehicles':'AG có nhiều xe',
    'missing_booking_vehicle':'quan sát chưa có biển AG đọc được',
    'expected_representative_mismatch':'S chưa khớp biển đại diện kỳ vọng',
    'direct_vehicle_mismatch':'biển GXT khác AG',
    'missing_date':'thiếu ngày','missing_plate':'thiếu/sai biển S','missing_order':'thiếu mã đơn',
    'missing_trip':'thiếu mã chuyến','invalid_weight':'kg thiếu/lỗi','duplicate_event_key':'khóa bản ghi trùng',
    'zero_denominator':'mẫu số bằng 0','representative':'biển đại diện'}


def booking_intake(r):
    from ..chief.report import display
    t=r['totals'];w=r.get('weight_quality',{});h=r.get('handling_component',{});q=r.get('quantity_quality',{})
    lines=[_intake_conclusion(t,w,h,q),'Báo cáo Booking (chưa đối soát) — '+r['scope'],
        'Sản phẩm: '+display(t.get('product_units'),'sản phẩm')+' sản phẩm; kiện/thùng chưa suy được từ số sản phẩm.',
        'Kg toàn phạm vi: '+display(t.get('kg'),'kg')+' kg.']
    for m in r.get('booking_rows',[])[:2]:lines.insert(2,m['Chỉ tiêu']+': '+m['Giá trị tính toán']+' '+m['Đơn vị'])
    if w.get('available'):lines.append('Kg phần GHN đủ nguồn (cột L): '+display(w.get('eligible_weight_kg'),'kg')+' kg, chưa thay tổng toàn phạm vi hoặc khối lượng đã giao thành công.')
    if h.get('available'):
        unit='đợt GHN/ngày' if h.get('grain')=='GHN_BOOKING_E_DAY' else 'GHN'
        held=(str(h['unresolved_ghn'])+' '+unit+' còn giữ riêng.' if h['unresolved_ghn'] is not None else 'số đợt giữ riêng N/A.')
        lines+=['Bốc xếp DC toàn phạm vi: '+display(h['component_vnd'],'VND theo điều khoản')+' VND theo điều khoản.',
            'Phần đủ điều kiện: '+display(h['eligible_vnd'],'VND theo điều khoản')+' VND theo điều khoản; '+held]
        if h.get('reason')=='OWNER_RULE_WINDOW_NOT_COVERING_SCOPE':
            window=h['rules_window'];lines.append('Quy tắc bốc xếp owner chỉ áp cho '+_vi_day(window['start'],year=True)+' đến '+_vi_day(window['end'],year=True)+'; kỳ báo cáo dài hơn khoảng đó nên bốc xếp để N/A. Cần owner duyệt quy tắc cho cả kỳ.')
        elif h.get('reason')=='RAW_STALE':
            lines.append('Raw chưa có bản mới trong 15 phút trước giờ gửi: phần Booking vẫn gửi, bốc xếp theo quy tắc hai chặng để N/A tới lượt Raw mới.')
        elif h.get('reason')=='OWNER_RULES_INVALID_OR_STALE':
            lines.append('Quy tắc bốc xếp owner đã cài không còn khớp nguồn/cấu hình hiện tại nên bốc xếp để N/A, không dùng cách tính cũ. Cần nạp lại quy tắc cho đúng bản nguồn.')
        if h.get('raw_weight_filled_events') and h.get('eligible_vnd') is not None:
            lines.append(str(h['raw_weight_filled_events'])+' đợt trống cột L được lấy kg từ Raw cùng GHN, từ ngày xuất tới trước lần xuất kế tiếp (gram/1.000) theo quy tắc owner.')
        for b in r['handling_rate_buckets']:
            if b['eligible_ghn']:lines.append('• '+b['rate_vnd_per_kg']+'đ/kg: '+display(b['eligible_kg'],'kg')+' kg → '+display(b['eligible_vnd'],'VND theo điều khoản')+' VND theo điều khoản.')
    lines+=['Tổng doanh thu B2B: N/A. Tổng chi phí và lãi lỗ: N/A; chưa đủ doanh thu và chi phí các chặng. Bốc xếp riêng chưa là tiền nghiệm thu/thanh toán.','CẦN ĐỐI CHIẾU:']
    if w.get('available'):
        flags=[str(w[k])+' '+s for k,s in [('missing_ghn','GHN thiếu kg cột L'),('invalid_ghn','GHN kg cột L lỗi'),('conflicting_ghn','GHN có nhiều tổng kg khác nhau'),('partial_scope_ghn','GHN có dòng ngoài kỳ/giờ chốt')] if w.get(k)]
        if flags:lines.append('• Kg: '+', '.join(flags)+'. Không chọn dòng cuối hoặc chia kg theo DO.')
    if q.get('available') and not q.get('complete'):lines.append('• Số sản phẩm (cột J) hoặc mã GHN/SO/DO thiếu, lỗi, lặp: giữ tổng chưa chốt, không cộng trùng hay điền 0.')
    if h.get('available'):
        labels={'missing_weight':'thiếu kg cột L','invalid_weight':'kg cột L lỗi','conflicting_weight':'kg cột L mâu thuẫn','mixed_rate':'qua mức giá','mixed_routing':'khác điều kiện phí','unknown_routing':'chưa khớp điều kiện phí','partial_scope':'chưa đủ ngày/kỳ','unknown_rate':'chưa có giá','two_leg_unresolved':'chưa rõ liên kết hai chặng AG/S'}
        flags=[str(n)+' '+labels[k] for k,n in h['counters'].items() if n] if h.get('unresolved_ghn') is not None else []
        if flags:lines.append('• Bốc xếp: '+', '.join(flags)+'. Subtotal không thay tổng component.')
        lines.append('Điều kiện phí độc lập xe vật lý: TP Lai Châu đi thẳng vẫn thuộc diện bốc xếp theo xác nhận owner; chưa tự tính phí xe/giao theo điểm.')
    elif not q.get('available'):lines.append('• Nguồn cũ chưa có mapping sản phẩm/kg/điều khoản đủ để tính. Mã lặp có thể là chi tiết/lịch sử, chưa chứng minh lỗi.')
    from ..telegram_presentation import source_note
    lines.append(source_note({'source_provenance':r.get('source_provenance',{})}))
    lines.extend(i['fact'] for i in r['issues'] if i['code']=='SOURCE_CAVEAT')
    question=r.get('intake_question','Đối chiếu chứng từ phần thiếu.')
    if question.startswith('Bước tiếp theo: '):question=question.removeprefix('Bước tiếp theo: ')
    lines+=['BƯỚC TIẾP: '+question,
        '/thieu xem từng dòng cần bổ sung; /details xem bảng số và quy tắc; /xe xem xe. Báo cáo này tính bằng code, chưa dùng AI.']
    return '\n'.join(lines)


def _intake_conclusion(t,w,h,q):
    done,gaps=[],[]
    for name,quality in (('số sản phẩm',q),('tổng kg',w),('bốc xếp DC',h)):
        if quality.get('available'):(done if quality.get('complete') else gaps).append(name)
    if gaps:
        return ('Kết luận: '+', '.join(gaps)+' chưa chốt vì còn dòng thiếu/lỗi; các số dưới chỉ là phần đủ nguồn. '
                'Việc cần làm: gửi /thieu để xem dòng cần bổ sung, sửa nguồn rồi xuất lại đúng tab.')
    if not done:
        return 'Kết luận: nguồn này mới đếm được dòng và mã GHN; chưa có quy tắc đã duyệt để tính sản phẩm, kg hay chi phí.'
    return 'Kết luận: '+', '.join(done)+' đã đủ nguồn trong kỳ; doanh thu, tổng chi phí và lãi lỗ chưa có căn cứ.'


def _vi_day(value,*,year=False):
    from .booking_vehicles import day
    d=day(value)
    return d.strftime('%d/%m/%Y' if year else '%d/%m') if d else 'chưa xác định'


def _vi_number(value,places=3):
    number=Decimal(value)
    with localcontext() as ctx:
        ctx.prec=max(100,len(number.as_tuple().digits)+places+2)
        return format(number,',.'+str(places)+'f').replace(',','_').replace('.',',').replace('_','.')


def _vi_money(value):
    """VND/kg for display: at most 3 decimals (half-up, as the old display), trailing zeros dropped.

    Kg-share allocations are non-terminating Decimals; every digit made messages
    unreadable. Exact values stay in the report and audit records.
    """
    number=Decimal(str(value)).quantize(Decimal('0.001'),rounding=ROUND_HALF_UP)
    places=max(0,-number.normalize().as_tuple().exponent) if number!=number.to_integral_value() else 0
    return _vi_number(number,places)


def _vi_received(value):
    return datetime.fromisoformat(value).astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).strftime('%H:%M %d/%m/%Y')


def vehicle_scope(v,text=''):
    """Parse a bounded day/range inside the already selected period and cutoff."""
    import re
    from .booking_vehicles import day
    parts=text.split(); dates=[]; aliases=[]
    for part in parts:
        if re.fullmatch(r'V[0-9]{1,6}',part):aliases.append(part)
        elif re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}',part):
            try:dates.append(date.fromisoformat(part))
            except ValueError:raise ValueError('Ngày xe không hợp lệ.') from None
        else:raise ValueError('Dùng /xe [YYYY-MM-DD] [YYYY-MM-DD] [Vn].')
    if len(dates)>2 or len(aliases)>1:raise ValueError('Chọn tối đa hai ngày và một mã xe tạm.')
    first=date.fromisoformat(v['period']+'-01');last=min(date(first.year,first.month,monthrange(first.year,first.month)[1]),day(v['cutoff']))
    if dates:
        start,end=dates[0],dates[-1]
        if start>end:raise ValueError('Ngày bắt đầu phải trước hoặc bằng ngày kết thúc.')
        if start<first or end>last:raise ValueError('Ngày xem phải nằm trong kỳ và ngày chốt đã chọn.')
    else:
        raw_first=day(v['raw_first_day']);raw_last=day(v['raw_last_day'])
        start=max(first,raw_first) if raw_first else None
        end=min(last,raw_last) if raw_last else None
        if start is not None and end is not None and start>end:start=end=None
    return {'start':str(start) if start else None,'end':str(end) if end else None,'aliases':aliases,'explicit':bool(dates)}


def vehicle_scope_quality(data,v,text=''):
    """Count unassigned raw records for this date range in RAM, without changing the evidence engine."""
    from .booking_vehicles import day,plate,RAW_HEADERS
    from .booking_quantity import cell
    pair=getattr(data,'vehicle_pair',None)
    if (not pair or not v.get('available') or pair['raw_digest']!=v['raw_digest'] or pair['booking_digest']!=v['booking_digest']
            or pair['vehicle_catalog_sha256']!=v['catalog_sha256']):return None
    scope=vehicle_scope(v,text);start=day(scope['start']);end=day(scope['end'])
    representatives={plate(p) for p in pair['vehicle_catalog']['representative_plates']}
    date_i=RAW_HEADERS.index('load_date');type_i=RAW_HEADERS.index('type');plate_i=RAW_HEADERS.index('truck_plate_number')
    from .booking_vehicles import unassigned_direct
    from .booking_quantity import business_key
    orders={}
    for r in data.rows.to_dict('records'):
        if r['order_id']:orders.setdefault(r['order_id'],[]).append(r)
    order_i=RAW_HEADERS.index('order_code')
    result={'missing_plate':0,'representative':0,'undated':False,'unassigned_direct':0}
    for row in pair['data_supra']['rows']:
        cells=row['values']
        if cell(cells[type_i]).upper()!='DELIVER':continue
        d=day(cells[date_i])
        if d is None:result['undated']=True;continue
        if start is None or end is None or not start<=d<=end:continue
        pl=plate(cells[plate_i])
        if not pl and unassigned_direct(orders.get(business_key(cells[order_i]),[]),pair['vehicle_catalog'],d):result['unassigned_direct']+=1
        elif not pl:result['missing_plate']+=1
        elif pl in representatives:result['representative']+=1
    return result


def _scope_label(scope):
    if not scope['start'] or not scope['end']:return 'chưa xác định khoảng dữ liệu'
    return _vi_day(scope['start'],year=True) if scope['start']==scope['end'] else _vi_day(scope['start'],year=True)+'–'+_vi_day(scope['end'],year=True)


def _scope_totals(selected):
    eligible=[g for g in selected if g['share'] is not None]
    with localcontext() as ctx:
        ctx.prec=100
        supra=sum((Decimal(g['supra_kg']) for g in eligible),Decimal(0))
        other=sum((Decimal(g['other_kg']) for g in eligible),Decimal(0))
        ratio=supra/(supra+other)*100 if supra+other else None
    return eligible,supra,other,ratio


def _handling_line(cost):
    if not cost:return 'Bốc xếp: chưa có kết quả theo phạm vi này.'
    if cost.get('reason')=='VEHICLE_ALLOCATION_NOT_AVAILABLE':
        return 'Bốc xếp riêng xe: chưa gán được vì thiếu khóa phí–GHN–xe đáng tin.'
    if not cost.get('available'):return 'Bốc xếp: chưa đủ cấu hình hoặc nguồn đã duyệt để tính trong kỳ.'
    if not cost['booking_rows']:return 'Bốc xếp: chưa có dòng Booking ngày xuất trong khoảng này.'
    if cost['complete'] and not cost['eligible_transfer_ghn'] and cost['direct_excluded_ghn']:
        return ('Bốc xếp: 0đ theo điều khoản; '+str(cost['direct_excluded_ghn'])+
            (' đợt GHN/ngày' if cost.get('grain')=='GHN_BOOKING_E_DAY' else ' GHN')+' không thuộc diện phí. Chưa là tiền thực trả.')
    subtotal=(_vi_money(cost['eligible_vnd'])+'đ' if cost['eligible_vnd'] is not None else 'chưa có phần đủ nguồn')
    whole=('tổng khoản '+_vi_money(cost['component_vnd'])+'đ' if cost['component_vnd'] is not None else 'tổng khoản chưa chốt')
    missing=('; '+str(cost['missing_id_rows'])+' dòng thiếu mã' if cost['missing_id_rows'] else '')
    if cost.get('unscoped_dates'):missing+='; có dòng thiếu ngày chưa gán kỳ'
    grain=' đợt GHN/ngày' if cost.get('grain')=='GHN_BOOKING_E_DAY' else ' GHN'
    return ('Bốc xếp theo ngày xuất Booking: phần đủ nguồn '+subtotal+' ('+str(cost['eligible_transfer_ghn'])+
        grain+'); '+str(cost['unresolved_ghn'])+grain+' cần đối chiếu'+missing+'; '+whole+'. Chưa nghiệm thu/thực trả.')


def _forecast_line(forecast):
    if not forecast or forecast.get('reason')=='NOT_CONFIGURED':
        return 'Dự kiến xe theo ca: chưa tính; kịch bản đã chốt chưa được nạp.'
    if forecast.get('reason')=='VEHICLE_ALLOCATION_NOT_AVAILABLE':
        return 'Dự kiến xe ghép: chưa gán cho mã xe này.'
    if forecast.get('reason')=='OUTSIDE_APPROVED_WINDOW':
        return 'Dự kiến xe theo ca: khoảng xem ngoài kỳ đã xác nhận, chưa tính.'
    if not forecast.get('available'):
        return 'Dự kiến xe theo ca: cấu hình hoặc nguồn chưa khớp, cần kiểm tra lại.'
    if forecast.get('tariff_breakdown') is not None:
        parts=[];terms_by_tariff=[]
        for t in forecast['tariff_breakdown']:
            tons=_vi_number(Decimal(t['declared_class_kg'])/Decimal(1000),3).rstrip('0').rstrip(',')
            unit='ca' if t['unit']=='PER_SHIFT' else 'chuyến'
            hours=(' '+t['shift_hours']+'h' if t['shift_hours'] else '')
            terms=', '.join({'INCLUDED':'gồm','EXCLUDED':'không gồm','UNKNOWN':'chưa rõ'}[t['inclusions'][k]]+' '+label for k,label in (('fuel','dầu'),('driver_wage','lái xe'),('tolls','cầu đường'),('vat','VAT'),('dc_handling','bốc xếp DC')))
            terms_by_tariff.append(terms)
            parts.append(tons+'T: '+str(t['vehicle_days'])+' xe/ngày × '+t['units_per_vehicle_day']+' '+unit+'/ngày × '+_vi_number(t['rate_vnd'],0)+'đ/'+unit+hours+' = '+_vi_money(t['amount_vnd'])+'đ')
        if len(set(terms_by_tariff))==1:parts.append(terms_by_tariff[0])
        else:parts=[part+' ('+terms+')' for part,terms in zip(parts,terms_by_tariff)]
        amount=(_vi_money(forecast['eligible_pool_vnd'])+'đ' if forecast['eligible_pool_vnd'] is not None else 'chưa có')
        basis=('Theo Booking E/AG, T là loại cước; chưa là ca/chuyến thực chạy hay phân bổ Supra, phát sinh chưa tính.' if forecast.get('booked_class_basis') else 'Theo Booking E/AG, tải T; chưa là thực chạy hay phân bổ Supra, phát sinh chưa tính.')
        return ('DỰ KIẾN xe dùng chung, phần đủ nguồn: '+amount+'; '+'; '.join(parts)+'. '+str(forecast['held_vehicle_days'])+' nhóm và '+str(forecast['unassigned_rows'])+' dòng giữ riêng; '+str(forecast['excluded_other_class_days'])+' nhóm tải khác chưa có giá. '+('Tổng chưa chốt. ' if forecast['full_pool_vnd'] is None else '')+basis+(' Có lượt riêng do owner xác nhận.' if forecast.get('owner_confirmed_booked_trips') else '')+(' Có dòng thiếu ngày chưa gán kỳ.' if forecast.get('undated_unscoped') else ''))
    with localcontext() as ctx:
        ctx.prec=100;tons=Decimal(forecast['declared_class_kg'])/Decimal(1000)
    size=_vi_number(tons,3).rstrip('0').rstrip(',')
    amount=(_vi_money(forecast['eligible_pool_vnd'])+'đ phần đủ nguồn' if forecast['eligible_pool_vnd'] is not None else 'chưa có phần đủ nguồn')
    whole='tổng chưa chốt' if forecast['full_pool_vnd'] is None else 'đủ nguồn theo giả định'
    return ('DỰ KIẾN xe '+size+'T dùng chung: '+amount+'; '+str(forecast['eligible_vehicle_days'])+
        ' xe/ngày × '+forecast['shifts_per_vehicle_day']+' ca/ngày × '+_vi_number(forecast['rate_vnd'],0)+
        'đ/ca '+forecast['shift_hours']+'h; '+str(forecast['held_vehicle_days'])+' nhóm, '+str(forecast['unassigned_rows'])+
        ' dòng giữ riêng, '+str(forecast['excluded_other_class_days'])+' nhóm tải khác không áp giá; '+whole+
        '. Theo Booking E/AG, tải theo T; chưa là ca thực chạy hay phân bổ Supra, gói/VAT chưa rõ.'+
        (' Có dòng thiếu ngày chưa gán kỳ.' if forecast.get('undated_unscoped') else ''))


def _vehicle_summary(v,selected,scope,quality,cost,forecast):
    label=_scope_label(scope);aliases=scope['aliases'];eligible,supra,other,ratio=_scope_totals(selected)
    stale=(datetime.now(timezone.utc)-datetime.fromisoformat(v['raw_retrieved_at'])).total_seconds()>900
    lines=['Kỳ xem riêng '+label+'. '+('Đây là bản dữ liệu đã lưu, chưa được cập nhật lại.' if stale else 'Số liệu từ bản dữ liệu đã lưu.')+((' Xe '+aliases[0]+' là mã tạm của bản này.') if aliases else '')]
    lines.append(_handling_line(cost)+' Tổng chi phí/lãi lỗ chưa đủ.')
    lines.append(_forecast_line(forecast))
    if ratio is not None:
        lines.append(('Xe/ngày đủ dữ liệu của mã đã chọn: ' if aliases else 'Tất cả xe/ngày đủ dữ liệu, kể cả xe/ngày không ghi nhận Supra: ')+_vi_money(supra)+'kg hàng Supra, '+_vi_money(other)+
            'kg hàng khác; tỷ trọng Supra '+_vi_number(ratio,2)+'% tính theo tổng kg.')
    elif selected:lines.append('Các ghi nhận xe trong khoảng này chưa đủ dữ liệu để tính tỷ trọng.')
    else:lines.append('Chưa có ghi nhận xe đọc được trong khoảng này'+(' cho mã '+aliases[0] if aliases else '')+'.')
    held=sum(g['share'] is None for g in selected)
    if held:lines.append(str(held)+' nhóm xe/ngày có dữ liệu thiếu hoặc mâu thuẫn được giữ riêng.')
    if quality is None:
        lines.append('Chưa tách được số dòng thiếu biển riêng cho khoảng này.')
    elif aliases:
        if quality['missing_plate'] or quality['representative']:lines.append('Các dòng thiếu biển hoặc biển đại diện không thể gán cho mã xe này.')
    else:
        lines.append('Chưa gắn xe trong khoảng: '+str(quality['missing_plate'])+' dòng thiếu/sai biển; '+str(quality['representative'])+' dòng biển đại diện.')
    if quality and quality['undated']:lines.append('Có dòng thiếu ngày nên chưa thể xếp vào khoảng này.')
    if any(m['flags'] and scope['start'] and scope['end'] and scope['start']<=str(m['day'])<=scope['end'] and
           (not aliases or aliases[0] in [m['raw_vehicle_alias'],*m['booking_vehicle_aliases']]) for m in v['mappings']):
        lines.append('Có thông tin biển hoặc tuyến cần đối chiếu; chưa kết luận xe chạy sai.')
    from .booking_vehicles import day
    if scope['start'] and day(v['raw_first_day']) and day(scope['start'])<day(v['raw_first_day']):
        lines.append('Bản nguồn bắt đầu từ '+_vi_day(v['raw_first_day'])+'; phần trước chưa có căn cứ đối chiếu.')
    if scope['end'] and day(v['raw_last_day']) and day(scope['end'])>day(v['raw_last_day']):
        lines.append('Bản nguồn chỉ ghi nhận đến '+_vi_day(v['raw_last_day'])+'; phần sau chưa có căn cứ đối chiếu.')
    lines.append('Theo mã nguồn, chưa xác minh khách hàng; hàng có thể lặp qua chặng. Tỷ trọng không là tải sử dụng/lãi lỗ hay phí xe.')
    lines.append('/details xem chi tiết.')
    return '\n\n'.join(lines)


def _vehicle_actions(v,scope,selected,quality,source_pin,cost,forecast):
    """Explain the selected range first; updating sources is optional later work."""
    import re
    aliases=scope['aliases'];start=scope['start'];end=scope['end']
    bound=(isinstance(source_pin,dict) and set(source_pin)==set(RAW_PIN) and
        all(str(source_pin[k])==str(RAW_PIN[k]) for k in RAW_PIN))
    url=(f"https://docs.google.com/spreadsheets/d/{RAW_PIN['spreadsheet_id']}/edit#gid={RAW_PIN['gid']}" if bound else None)
    eligible,supra,other,ratio=_scope_totals(selected)
    if quality is not None and not aliases:
        quality_text=(('Chưa gắn xe: '+str(quality['missing_plate'])+' dòng thiếu/sai biển, '+str(quality['representative'])+' dòng biển đại diện.')
            if quality['missing_plate'] or quality['representative'] else 'Không có dòng chưa gắn xe trong khoảng.')
    else:quality_text='Dòng thiếu biển không gán được cho mã xe này.' if aliases else 'Chưa có số dòng thiếu biển theo khoảng.'
    if quality and quality['undated']:quality_text+=' Có dòng thiếu ngày chưa gán kỳ.'
    lines=['CHI PHÍ '+_scope_label(scope)+' — bản dữ liệu đã lưu.',_handling_line(cost),_forecast_line(forecast),
        ('Các tuyến/loại xe khác: có bảng giá cũ, chưa nối tính; cần hiệu lực kỳ, đơn vị và gói gồm nhiên liệu/lương/cầu đường.' if forecast and forecast.get('available') else
         'DỰ KIẾN xe lấy/trung chuyển/giao thẳng: có bảng giá cũ, chưa nối tính; cần hiệu lực kỳ, đơn vị và gói gồm nhiên liệu/lương/cầu đường.'),
        'DỰ KIẾN giao GXT/BC/NCC: chưa nối; kiểm tra đơn vị và số điểm/chuyến có căn cứ hoặc giả định đã chốt; không lấy DO làm điểm.',
        'DỰ KIẾN lương/kho/phát sinh: chưa nối; cần đối tượng, kỳ và phân bổ; tránh cộng lại khoản đã gồm trong giá xe.',
        'Tổng dự kiến/lãi lỗ chưa đủ. Giá doanh thu đã chốt; phần mềm chưa nối tính.',
        ('Bước tiếp: đối chiếu nhóm giữ riêng; chốt giá, đơn vị cho '+str(forecast['excluded_other_class_days'])+' xe/ngày tải khác và phí giao/kho/lương. Chưa rõ số lượng thì không điền 0.' if forecast and forecast.get('available') else
         'Bước tiếp: đưa kịch bản đã chốt vào Codex để kiểm tra; dùng bảng giá xe/giao khác sau khi xác nhận hiệu lực, đơn vị và khoản đã gồm. Chưa rõ số lượng thì không điền 0.'),
        'Mẫu khi có: [ngày/kỳ] | [hiệu lực] | [mã chuyến/ca] | [biển/tuyến] | [giá/số tiền NCC] | [chuyến/ca/ngày/tháng/điểm] | [đã gồm] | [số lượng] | [chứng từ].',
        'Dự kiến không là thực chi. Khi đối chiếu: ngày, mã chuyến/ca/kỳ, biển/tuyến, tiền NCC, đơn vị, khoản đã gồm và chứng từ. Gửi Codex kiểm tra; Telegram chưa nhập riêng file xe/chi phí.',
        ((('Xe/ngày đủ dữ liệu của mã đã chọn: ' if aliases else 'Tất cả xe/ngày đủ dữ liệu, kể cả nhóm không ghi nhận Supra: ')+
         'Supra '+_vi_money(supra)+'kg; hàng khác '+_vi_money(other)+'kg; tỷ trọng Supra '+_vi_number(ratio,2)+'% theo tổng kg.') if ratio is not None else 'Khoảng này chưa có xe/ngày đủ dữ liệu để tính tỷ trọng.'),
        'Giữ riêng '+str(sum(g['share'] is None for g in selected))+' nhóm xe/ngày chưa đủ dữ liệu. '+quality_text,
        'Xe/ngày và mã Vn: /details audit.']
    cohort=[g for g in eligible if Decimal(g['supra_kg'])>0]
    if cohort:
        _,cs,co,cr=_scope_totals(cohort)
        raw_index=next(i for i,line in enumerate(lines) if line.startswith(('Xe/ngày đủ dữ liệu của mã','Tất cả xe/ngày đủ dữ liệu','Khoảng này chưa có xe/ngày')))
        lines.insert(raw_index+1,'Nhóm '+str(len(cohort))+' xe/ngày có ghi nhận Supra: '+_vi_money(cs)+'kg Supra + '+_vi_money(co)+'kg hàng khác cùng xe/ngày; tỷ trọng '+_vi_number(cr,2)+'% theo kg của nhóm này.')
    lines+=['Anh có quyền xem; nếu mở rộng sau: CSV raw_current nguyên tab, giữ đủ 25 cột, cả Supra và hàng khác; kỳ này xem được ngay.',
        ('Nguồn raw_current:\n'+url if url else 'Chưa đủ thông tin để tạo link nguồn tin cậy; cần kiểm tra lại nguồn đã duyệt.')]
    rows=[];sha=v.get('raw_digest','')
    fresh=(datetime.now(timezone.utc)-datetime.fromisoformat(v['raw_retrieved_at'])).total_seconds()<=900
    if fresh and bound and re.fullmatch('[a-f0-9]{64}',sha) and v.get('provenance')=='SOURCE_RECORD_CS_MEMBERSHIP_NOT_PHYSICAL_OR_FINANCIAL_TRUTH':
        for m in v['mappings']:
            if not m['flags'] or not start or not end or not start<=str(m['day'])<=end:continue
            if aliases and aliases[0] not in [m['raw_vehicle_alias'],*m['booking_vehicle_aliases']]:continue
            match=re.fullmatch(r'Raw#'+sha[:12]+r' dòng ([0-9]{1,6})',m.get('source',''))
            if match and 2<=int(match[1])<=100000 and int(match[1]) not in rows:rows.append(int(match[1]))
            if len(rows)==2:break
    row_index=None
    if rows:
        row_index=len(lines)
        lines.append('Vị trí trong bản xuất; nếu Sheet đã đổi, đối chiếu đúng chuyến trước:\n'+'\n'.join(f"Raw S{row}:\n{url}&range=S{row}" for row in rows))
    elif not fresh:lines.append('Bản lưu cũ: dòng có thể đổi; không tự sửa trên Sheet.')
    lines+=['/details audit xem truy vết kỹ thuật.']
    result='\n\n'.join(lines)
    if len(result)>1800 and row_index is not None and len(rows)>1:
        lines[row_index]='Vị trí trong bản xuất; nếu Sheet đã đổi, đối chiếu đúng chuyến trước:\n'+f"Raw S{rows[0]}:\n{url}&range=S{rows[0]}"
        result='\n\n'.join(lines)
    if len(result)>1800 and row_index is not None:
        lines[row_index]='Xem tham chiếu dòng trong /details audit; Sheet có thể đổi, không tự sửa.'
        result='\n\n'.join(lines)
    if len(result)>1800:
        for prefix in ('Bản lưu cũ:', 'Anh có quyền xem;', 'Nguồn raw_current:'):
            for i,line in enumerate(lines):
                if line.startswith(prefix):
                    lines.pop(i);result='\n\n'.join(lines);break
            if len(result)<=1800:break
    return result


def vehicle_view(v,text='',details=False,*,audit=False,source_pin=None,answered_text=None,scope_quality=None,cost_record=None,forecast_record=None):
    if not v.get('available'):return ('Em chưa có đủ hai nguồn dữ liệu đơn xuất hàng và xe đã được duyệt, nên chưa đối chiếu được xe chở hàng và chi phí. '
        'Gửi bản xuất vào cuộc trò chuyện Codex đang cấu hình để kiểm tra trước khi nạp. Telegram chưa có nhập riêng file xe.')
    scope=vehicle_scope(v,text);start,end,aliases=scope['start'],scope['end'],scope['aliases']
    selected=[g for g in v['groups'] if start and end and start<=g['day']<=end and (not aliases or g['vehicle_alias']==aliases[0])]
    if not details:return _vehicle_summary(v,selected,scope,scope_quality,cost_record,forecast_record)
    if not audit:return _vehicle_actions(v,scope,selected,quality=scope_quality,source_pin=source_pin,cost=cost_record,forecast=forecast_record)
    limit=10
    eligible,supra,other,ratio=_scope_totals(selected)
    lines=['XE / CHIA SẺ THEO NGUỒN — chưa phân bổ tài chính',
        'Phạm vi xem: '+_scope_label(scope)+(('; xe '+aliases[0]) if aliases else '')+'.',
        f"Booking đến {_vi_day(v['booking_last_day'],year=True)}; Raw DELIVER {_vi_day(v['raw_first_day'],year=True)}–{_vi_day(v['raw_last_day'],year=True)}; nhận Raw {_vi_received(v['raw_retrieved_at'])} (giờ Việt Nam).",
        f"Trong phạm vi: {len(selected)} nhóm xe-ngày, {len(selected)-len(eligible)} nhóm giữ riêng; không coi là số chuyến/ca thực hiện.",
        'Phần đủ nguồn: Supra '+_vi_money(supra)+'kg; hàng khác '+_vi_money(other)+'kg; tỷ trọng theo tổng kg '+(_vi_number(ratio,2)+'%' if ratio is not None else 'N/A')+'.',
        ('Chất lượng theo khoảng ngày (không thể gán dòng thiếu biển cho một mã xe): '+str(scope_quality) if scope_quality is not None else 'Chưa có số dòng thiếu biển theo riêng khoảng ngày.'),
        f"Hiện tối đa {limit} nhóm (alias theo phiên nguồn)."]
    lines.append('CHI PHÍ BOOKING THEO NGÀY XUẤT TRONG PHẠM VI: '+_handling_line(cost_record))
    if cost_record and cost_record.get('available'):
        if cost_record.get('rules_sha256'):lines.append('Quy tắc owner '+cost_record['rules_sha256']+'; grain GHN/ngày xuất Booking.')
        if cost_record.get('eligible_events'):lines.append('Mẫu đợt bốc xếp: '+str([{k:x[k] for k in ('day','sources','kg','amount_vnd')} for x in cost_record['eligible_events'][:8]])+'.')
        if cost_record.get('legacy_warnings'):lines.append('Cảnh báo cũ: '+str(cost_record['legacy_warnings']['counters'])+'; mẫu nguồn '+str(cost_record['legacy_warnings']['issue_sources'][:8])+'.')
        lines.append('Catalog bốc xếp '+cost_record['handling_catalog_sha256']+'; Booking '+cost_record['booking_digest']+
            '; đối chiếu theo E và L/GHN, không dùng kg Raw. Total cost/GP chưa tính.')
        for b in cost_record['rate_buckets']:
            if b['eligible_ghn']:
                lines.append('Giá '+b['rate_vnd_per_kg']+'đ/kg: '+str(b['eligible_ghn'])+' GHN; '+_vi_money(b['eligible_kg'])+
                    'kg; '+_vi_money(b['eligible_vnd'])+'đ theo điều khoản.')
        lines.append('Cờ bốc xếp (có thể trùng một GHN, không cộng làm tổng): '+str(cost_record['counters'])+
            '; thiếu mã: '+str(cost_record['missing_id_rows'])+' dòng; qua biên khoảng: '+str(cost_record['boundary_ghn'])+' GHN.')
        refs=list(dict.fromkeys(cost_record['issue_sources']))
        if refs:lines.append('Mẫu nguồn cần đối chiếu: '+'; '.join(refs[:8])+f" (mẫu ≤8/{len(refs)}).")
    lines.append('KỊCH BẢN CA XE DÙNG CHUNG: '+_forecast_line(forecast_record))
    if forecast_record and forecast_record.get('available'):
        lines.append('Scenario '+forecast_record['scenario_sha256']+'; Booking '+forecast_record['booking_digest']+
            '; Raw '+forecast_record['raw_digest']+'; vehicle catalog '+forecast_record['vehicle_catalog_sha256']+
            '; handling catalog '+forecast_record['handling_catalog_sha256']+'.')
        lines.append('Căn cứ áp giá: cột T cho xe AG; '+(forecast_record.get('booked_class_basis') or forecast_record['class_basis'])+'; ghi nhận T: '+str(forecast_record['class_audit'])+
            '; loại tải khác '+str(forecast_record['excluded_other_class_days'])+' xe/ngày; gói '+str(forecast_record['inclusions'])+'.')
        if forecast_record.get('tariff_breakdown') is not None:lines.append('Chi tiết các bảng giá và điều khoản: '+str(forecast_record['tariff_breakdown'])+'.')
        lines.append(str(forecast_record['warning_vehicle_days'])+' nhóm có thêm dòng tuyến khác hoặc nhiều booking cùng xe/ngày; không nhân ca, chưa chứng minh ca thực chạy.')
        for entry in forecast_record['group_evidence'][:10]:
            lines.append('Nhóm '+_vi_day(entry['date'],year=True)+' '+str(entry['vehicle_alias'])+'; '+str(entry['issues'])+
                '; cảnh báo '+str(entry['warnings'])+'; nguồn '+'; '.join(entry['sources'][:3]))
        if forecast_record['unassigned_sources']:
            lines.append('Mẫu nguồn chưa gán được: '+'; '.join(forecast_record['unassigned_sources'][:8])+'.')
        if forecast_record['undated_sources']:
            lines.append('Mẫu dòng thiếu ngày chưa gán kỳ: '+'; '.join(forecast_record['undated_sources'][:8])+'.')
        lines.append('Chỉ là dự kiến dùng chung; phần chưa hiển thị không được coi là đã giải quyết. Không cộng vào total cost/GP.')
    for g in selected[:limit]:
        ratio='N/A — '+', '.join(FLAG_LABELS.get(f,f) for f in g['issues']) if g['share'] is None else _vi_number(Decimal(g['share'])*100,2)+'%'
        lines.append(f"• {_vi_day(g['day'],year=True)} {g['vehicle_alias']}: Supra {_vi_money(g['supra_kg'])}kg / khác {_vi_money(g['other_kg'])}kg; tỷ lệ S-source {ratio}.")
        if details:lines.append('  Nguồn: '+'; '.join(g['sources'][:10])+f" (mẫu ≤10/{len(g['sources'])})")
        if g['issues']:lines.append('  Mã chất lượng nhóm: '+', '.join(g['issues']))
    lines+=['AG upstream/NCC: share N/A khi không có mẫu số hàng khác của chính AG. Không gán tỷ lệ S cho AG, không suy 100% từ đi thẳng; không coi thiếu quan sát là chưa chạy/giao.',
        'S đại diện không là xe vật lý. S khác kỳ vọng/AG là cờ đối chiếu, không sửa S hoặc tự gọi sai xe.']
    from datetime import datetime,timezone
    if (datetime.now(timezone.utc)-datetime.fromisoformat(v['raw_retrieved_at'])).total_seconds()>900:lines.append('Bản nhận Raw đã quá15 phút; refresh Booking không làm Raw mới. Đây chỉ là chẩn đoán bản nguồn cũ.')
    if scope_quality and scope_quality['missing_plate']:lines.append('Chia sẻ của dòng thiếu/sai S: N/A; không gán sang AG hoặc làm hỏng mẫu số của xe khác.')
    lines+=['J sản phẩm và '+('L/GHN/ngày xuất/bốc xếp' if cost_record and cost_record.get('grain')=='GHN_BOOKING_E_DAY' else 'L/GHN/bốc xếp')+' giữ grain riêng; kg vận chuyển không nhân vào doanh thu. Không xác minh client identity/hoàn tất/SLA.',
        '/vehicles YYYY-MM-DD YYYY-MM-DD Vn để lọc; /details audit owner xem quy tắc/truy vết bản này. 0 HTTP.']
    if details:
        if isinstance(source_pin,dict) and set(source_pin)==set(RAW_PIN) and all(str(source_pin[k])==str(RAW_PIN[k]) for k in RAW_PIN):
            lines.append('Nguồn raw_current:\n'+f"https://docs.google.com/spreadsheets/d/{RAW_PIN['spreadsheet_id']}/edit#gid={RAW_PIN['gid']}")
        lines.append('Nếu sau này xuất Raw: giữ đủ 25 cột gốc, cả Supra và hàng khác; kiểm tra load_date(B), trip_code(E), order_code(F), weight(J, gram), type(P), truck_plate_number(S). Không tự sửa Sheet; gửi Codex để kiểm tra trước khi nạp.')
        lines+=['Metadata toàn kỳ bản chụp, ngoài số liệu phạm vi xem: '+str(v['source_s_groups'])+' nhóm xe/ngày, '+str(v['source_s_vehicles'])+' định danh S; Supra '+_vi_money(v['supra_kg'])+'kg, hàng khác '+_vi_money(v['other_kg'])+'kg.',
            'Mã vai trò toàn kỳ: '+str(v['role_vehicle_counts']), 'Cờ mapping toàn kỳ: '+str(v['mapping_quality']), 'Chất lượng toàn kỳ và dòng thiếu ngày chưa gán kỳ: '+str(v['quality'])]
        lines+=['Versions: Booking '+v['booking_digest']+'; Raw '+v['raw_digest']+'; policy/catalog '+v['catalog_sha256'],
            'Toàn bộ nhóm, quan hệ nhiều chặng và evidence giữ trong bản RAM; nguồn business đầy đủ trong DPAPI. Các dòng chưa hiển thị không được coi là đã giải quyết.']
        relevant=[m for m in v['mappings'] if start and end and start<=str(m['day'])<=end and (not aliases or aliases[0] in [m['raw_vehicle_alias'],*m['booking_vehicle_aliases']])]
        for m in relevant[:20]:lines.append(_vi_day(m['day'],year=True)+' '+str(m['mode'])+' AG='+','.join(m['booking_vehicle_aliases'])+' S='+str(m['raw_vehicle_alias'])+'; '+','.join(m['flags'])+'; '+m['source'])
        lines.append('Bản /xe đã trả lời:\n'+(answered_text if answered_text is not None else 'Chưa lưu nội dung tin trả lời trong bản truy vết này; không dựng lại thành tin đã gửi.'))
    return '\n'.join(lines)


def fleet_context(v):
    values={'groups':(v['source_s_groups'],'nhóm S-ngày'),'vehicles':(v['source_s_vehicles'],'định danh S'),
        'supra_kg':(v['supra_kg'],'kg theo CS'),'other_kg':(v['other_kg'],'kg ngoài CS'),
        'missing_plate_rows':(v['quality'].get('missing_plate',0),'dòng'),
        'mapping_flags':(sum(v['mapping_quality'].values()),'cờ, có thể trùng')}
    values.update({'role_'+k:(n,'xe trong vai trò, không cộng chồng') for k,n in v['role_vehicle_counts'].items()})
    return dict(vehicle=True,label='VEHICLE SOURCE RECORD / UNVERIFIED '+v['booking_digest'][:12]+'/'+v['raw_digest'][:12]+'/'+v['catalog_sha256'][:12],
        cache_identity=(v['booking_digest'],v['raw_digest'],v['catalog_sha256'],v['period'],v['cutoff']),
        source_provenance={'booking_digest':v['booking_digest'],'raw_digest':v['raw_digest'],'vehicle_catalog_sha256':v['catalog_sha256'],
            'booking_retrieved_at':v['booking_retrieved_at'],'raw_retrieved_at':v['raw_retrieved_at']},
        scope=f"Kỳ {v['period']}, cutoff {v['cutoff']}; Raw {v['raw_first_day']}–{v['raw_last_day']}; Booking đến {v['booking_last_day']}; source records, not financial allocation",
        limitations='DELIVER only; grams/1000; ALL CS membership, not client identity audit. No raw plates/orders/catalog. Representative not physical. AG upstream/NCC share unavailable; never infer 100% from direct. No kg-to-J/L/cost/revenue inference; mismatches are flags, no last winner.',
        metrics={'vehicle.'+k:dict(value=x,label=k,unit=u,formula='deterministic source-record aggregate; not physical/financial truth',
            sources=['Paired source metadata'],source_count=2,source_sample_only=True) for k,(x,u) in values.items()})
