"""Vehicle evidence and recorded DELIVER kg share. Never a financial allocator."""
import hashlib
import json
import re
from collections import defaultdict,Counter
from datetime import datetime,date
from decimal import Decimal,localcontext
from zoneinfo import ZoneInfo
from .booking_quantity import cell,business_key
from .booking_weight import parse_kg
from .booking_handling import geography,validate_catalog

PAIR_SCHEMA='supra-vehicle-paired-source-intake-v4'
BOOK_HEADERS=['Ngày LTC','Mã đơn GHN','Trạng thái','Ngày xuất','MÃ SO','MÃ DO','Mã siêu thị',
    'Qty (Units)','Weight (kg)/ tổng cửa hàng','Tỉnh','Huyện','Tải trọng','BIỂN SỐ XE ']
# Owner 28/10/2026: column Y 'Ghi chú 2' marks special orders (e.g. 'KT 24.10 booking giao sớm 20.10').
BOOK_HEADERS_V2=BOOK_HEADERS+['Ghi chú 2']
OPENING_NOTE=re.compile(r'(?<![A-Za-zÀ-ỹĐđ])(?:CHKT|KT)(?![A-Za-zÀ-ỹĐđ])|khai\s*tr(?:ươ|uo)ng',re.I)


# Owner 28/10/2026: a Raw DELIVER row with a blank plate on a direct-delivery GHN is the
# Booking AG truck itself; staff tick "không gán giao" so no GXT wage is attributed.
DIRECT_MODES={'GXT_DIRECT','NCC_DIRECT','LAI_CHAU_DIRECT'}


def unassigned_direct(order_rows,catalog,when):
    return bool(order_rows) and when is not None and all(classify(r,catalog,when) in DIRECT_MODES for r in order_rows)


def is_opening_note(value):
    """KT/CHKT as a standalone token (KT24.10 yes, KTX no) or 'khai trương' in Ghi chú 2."""
    import unicodedata
    return bool(OPENING_NOTE.search(unicodedata.normalize('NFC',cell(value))))


RAW_HEADERS=['time_updated','load_date','time_bat_dau_chuyen','time_ket_thuc_chuyen','trip_code',
    'order_code','so_kien','client_type','weight','type','is_succeeded','truck_plate_number',
    'warehouse_name','region_shortname','from_province','from_district','to_province','to_district']
RAW_PIN={'spreadsheet_id':'1-7FvodhoSpZXs4WRFXhL6QPx9sF2fgGHKK34GNylXJk','tab':'raw_current','gid':520894521}
VEHICLE_POLICY={'version':'supra-vehicle-share-policy-v1','provenance':'USER_CONFIRMED_IN_CODEX',
    'date':'2026-10-26','event_filter':'DELIVER','day_field':'load_date','weight_field':'weight',
    'weight_source_unit':'g','weight_divisor':'1000','membership':'EXACT_ALL_CS_GHN_NOT_MONTH_ONLY',
    'grouping':'LOAD_DATE_AND_NORMALIZED_REAL_S','duplicate_policy':'HOLD_GROUP_NO_FIRST_LAST_OR_SUM',
    'representative_policy':'NOT_PHYSICAL_NO_AG_SHARE_INFERENCE',
    'routing_before_vehicle':True,'vehicle_and_handling_independent':True,
    'verification':'SOURCE_RECORD_CS_MEMBERSHIP_NOT_PHYSICAL_OR_FINANCIAL_TRUTH'}


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def plate(value):
    text=re.sub(r'[\s.\-]','',cell(value)).upper()
    return text if re.fullmatch(r'[0-9]{2}[A-Z]{1,2}[0-9]{4,6}',text) else ''


def day(value):
    text=cell(value)
    if not text:return None
    for fmt in ('%Y-%m-%d','%d/%m/%Y','%d/%m/%Y %H:%M:%S','%Y-%m-%d %H:%M:%S'):
        try:return datetime.strptime(text,fmt).date()
        except ValueError:pass
    try:
        dt=datetime.fromisoformat(text.replace('Z','+00:00'))
        return dt.astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).date() if dt.tzinfo else dt.date()
    except ValueError:return None


def capacity_kg(value):
    s=cell(value).casefold().replace(' ','')
    m=re.fullmatch(r'([0-9]+(?:[.,][0-9]+)?)(t|tấn|kg)',s)
    if not m:return None
    return str(Decimal(m[1].replace(',','.'))*(1000 if m[2] in ('t','tấn') else 1))


def validate_vehicle_catalog(c,booking_pin,raw_pin,expected=None):
    try:
        if (set(c)!={'schema','version','source_pins','period','provenance','confirmed_date','representative_plates','transfer_class_kg','routing','evidence_hashes'} or
                c['schema']!='supra-vehicle-catalog-v1' or set(c['source_pins'])!={'booking','data_supra'}):raise ValueError
        for name,pin in (('booking',booking_pin),('data_supra',raw_pin)):
            if set(c['source_pins'][name])!={'spreadsheet_id','tab','gid'} or any(str(c['source_pins'][name][k])!=str(pin[k]) for k in c['source_pins'][name]):raise ValueError
        if any(str(raw_pin[k])!=str(RAW_PIN[k]) for k in RAW_PIN):raise ValueError
        if not isinstance(c['representative_plates'],list) or not 1<=len(c['representative_plates'])<=10:raise ValueError
        if any(not plate(v) for v in c['representative_plates']) or len(set(map(plate,c['representative_plates'])))!=len(c['representative_plates']):raise ValueError
        if not isinstance(c['transfer_class_kg'],str) or not re.fullmatch(r'[1-9][0-9]{0,6}',c['transfer_class_kg']):raise ValueError
        modes={'TRANSFER_GXT','TRANSFER_REP_LASTMILE','GXT_DIRECT','NCC_DIRECT','LAI_CHAU_DIRECT'}
        routes=[]
        for r in c['routing']:
            if set(r)!={'rule_id','scope','province','district','store_id','mode','handling_eligible','from','to','evidence_ref'} or r['mode'] not in modes or type(r['handling_eligible']) is not bool:raise ValueError
            routes.append({k:v for k,v in r.items() if k not in ('mode','handling_eligible')} | {'routing':'TRANSFER' if r['handling_eligible'] else 'DIRECT'})
        # Reuse strict interval/alias/source/evidence validation without applying
        # handling eligibility as a physical vehicle mode.
        h={k:c[k] for k in ('version','period','provenance','confirmed_date','evidence_hashes')}
        h.update(schema='supra-dc-handling-catalog-v1',source_pin=c['source_pins']['booking'],routing=routes,
            rates=[dict(rate_id='VALIDATION',**{'from':c['period']+'-01','to':c['period']+'-01'},vnd_per_kg='0',currency='VND',evidence_ref='validation-only')])
        validate_catalog(h,booking_pin)
        sha=digest(c)
        if expected is not None and sha!=expected:raise ValueError
        return sha
    except (ValueError,TypeError,KeyError,AttributeError):
        raise ValueError('Vehicle catalog sai schema/pin/hash/mode hoặc rule chồng lặp.') from None


def catalog_from_owner_semantics(semantics,models,booking_pin,*,version,evidence_hashes,store_match=None):
    """RAM-only construction; no old six-plate defaults and no business writes."""
    if semantics.get('schema')!='supra-vehicle-field-semantics-v2' or semantics.get('status')!='OWNER_RULES_CONFIRMED_READY_FOR_GUARDED_IMPLEMENTATION':
        raise ValueError('Owner chưa chốt cơ chế xe.')
    rules=semantics['rules'];ncc=next(r for r in rules if r['mode']=='NCC_DIRECT')
    lc=next(r for r in rules if r['mode']=='LAI_CHAU_DIRECT')
    if ncc.get('real_vehicle_source')!='BOOKING_AG' or lc.get('tp_lai_chau_dc_handling_applies') is not True:
        raise ValueError('Thiếu xác nhận AG hoặc ngoại lệ bốc xếp.')
    tc=next(r for r in rules if r['mode']=='TRANSFER_THEN_GXT')
    c=dict(schema='supra-vehicle-catalog-v1',version=version,source_pins={'booking':booking_pin,'data_supra':dict(RAW_PIN)},
        period=semantics['applicable_month'],provenance='USER_CONFIRMED_IN_CODEX',confirmed_date=semantics['confirmed_date'],
        representative_plates=[semantics['raw_representative_plate']],transfer_class_kg=str(tc['first_leg_vehicle_class_kg']),routing=[],evidence_hashes=evidence_hashes)
    for i,r in enumerate(models['rules']):
        province=geography(r['province'],province=True);area=geography(r['area'])
        ncc_areas={geography(a) for a in ncc['areas']}
        bare=re.sub(r'^(?:h|tp|tx|q) ','',area)
        if province==geography(lc['province'],province=True):mode='LAI_CHAU_DIRECT'
        elif province==geography(ncc['province'],province=True) and bare in ncc_areas:mode='NCC_DIRECT'
        elif r['routing_mode_confirmed']=='DIRECT':mode='GXT_DIRECT'
        elif province==geography('Sơn La',province=True) and 'BC' in r['delivery_model'].upper() and any(v in r['delivery_model'].lower() for v in ('100k','100.000')):mode='TRANSFER_REP_LASTMILE'
        else:mode='TRANSFER_GXT'
        # The province name comes from owner semantics mode SON_LA, not a
        # historical plate assignment. Explicit BC/100k exception only there.
        scope='STORE' if r['matching_scope']=='STORE_EXCEPTION' else 'DISTRICT'
        district=r['area'];sid=''
        if scope=='STORE':
            if not store_match or geography(store_match['user_rule_area'])!=area:raise ValueError('Thiếu match H đã được xác minh.')
            sid=store_match['matched_store_id'];district=store_match['observed_source_district']
        c['routing'].append(dict(rule_id='V'+str(i+1),scope=scope,province=r['province'],district=district,store_id=sid,mode=mode,
            handling_eligible=r['dc_handling_applicability_confirmed'],**{'from':r['effective_from_confirmed'],'to':r['effective_to_confirmed']},evidence_ref='owner_delivery_models.json#/rules/'+str(i)))
    validate_vehicle_catalog(c,booking_pin,RAW_PIN)
    return c


def classify(row,c,when):
    if when is None:return None
    active=[r for r in c['routing'] if r['from']<=str(when)<=r['to']]
    p=geography(row.get('source_province',''),province=True);d=geography(row.get('source_district',''));sid=row.get('source_store_id','')
    matches=[r for r in active if r['scope']=='STORE' and r['store_id']==sid]
    if matches:
        return matches[0]['mode'] if len(matches)==1 and geography(matches[0]['province'],province=True)==p and geography(matches[0]['district'])==d else None
    if not sid and any(r['scope']=='STORE' and geography(r['province'],province=True)==p and geography(r['district'])==d for r in active):return None
    matches=[r for r in active if r['scope']=='DISTRICT' and geography(r['province'],province=True)==p and geography(r['district'])==d]
    return matches[0]['mode'] if len(matches)==1 else None


def vehicle_evidence(data,period,cutoff):
    pair=getattr(data,'vehicle_pair',None)
    if not pair:return {'available':False,'reason':'NO_PAIRED_VEHICLE_SOURCE'}
    c=pair['vehicle_catalog'];raw=pair['data_supra'];representatives=set(map(plate,c['representative_plates']))
    validate_vehicle_catalog(c,c['source_pins']['booking'],raw,pair['vehicle_catalog_sha256'])
    all_cs=set(data.rows.loc[data.rows.order_id.ne(''),'order_id'])
    by_order=defaultdict(list)
    for r in data.rows.to_dict('records'):
        if r['order_id']:by_order[r['order_id']].append(r)
    events=[];qc=Counter();keys=Counter();all_days=[]
    for i,row in enumerate(raw['rows']):
        r=dict(zip(RAW_HEADERS,row['values']))
        if cell(r['type']).upper()!='DELIVER':continue
        d=day(r['load_date']);pl=plate(r['truck_plate_number']);order=business_key(r['order_code']);trip=business_key(r['trip_code'])
        kg,status=parse_kg(r['weight']);kg=kg/Decimal(1000) if kg is not None else None
        if d:all_days.append(d)
        if d is not None and (d.strftime('%Y-%m')!=period or d>cutoff.date()):continue
        qc['deliver_rows']+=1
        problems=[];direct_blank=not pl and bool(order) and unassigned_direct(by_order.get(order,[]),c,d)
        if direct_blank:qc['unassigned_direct_rows']+=1
        for flag,bad in (('missing_date',d is None),('missing_plate',not pl and not direct_blank),('missing_order',not order),('missing_trip',not trip),('invalid_weight',status!='VALID')):
            if bad:qc[flag]+=1;problems.append(flag)
        if pl in representatives:qc['representative_rows']+=1;problems.append('representative')
        key=(d,trip,order,pl,'DELIVER')
        if d and trip and order and pl:keys[key]+=1
        events.append(dict(day=d,plate=pl,order=order,trip=trip,kg=kg,supra=order in all_cs if order else None,
            key=key,issues=problems,source=f"Raw#{pair['raw_digest'][:12]} dòng {row['sheet_row']}",raw_row=row['sheet_row'],unassigned_direct=direct_blank))
    groups=defaultdict(list);inventory=defaultdict(lambda:dict(roles=set(),sources=set(),capacities=set(),booking_capacity_raw=set()))
    mappings=[];mode_q=Counter();role_assets=defaultdict(set)
    def asset(pl,role,ref,capacity=None):
        if not pl or pl in representatives:return
        inventory[pl]['roles'].add(role);inventory[pl]['sources'].add(ref);role_assets[role].add(pl)
        if capacity is not None:inventory[pl]['capacities'].add(str(capacity))
    for row in data.rows.to_dict('records'):
        d=row['dispatch_date']
        if not d or d.strftime('%Y-%m')!=period or d>cutoff.date():continue
        mode=classify(row,c,d);ag=plate(row.get('source_booking_plate',''))
        if not mode:mode_q['unknown_booking_mode_rows']+=1;continue
        if not ag:mode_q['missing_booking_vehicle_rows']+=1;continue
        role=('TRANSFER_LINEHAUL' if mode.startswith('TRANSFER') else 'DIRECT_NCC' if mode in ('NCC_DIRECT','LAI_CHAU_DIRECT') else 'DIRECT_GXT')
        asset(ag,role,row['_source'],c['transfer_class_kg'] if role=='TRANSFER_LINEHAUL' else row.get('source_capacity_raw'))
        if ag not in representatives:inventory[ag]['booking_capacity_raw'].add(cell(row.get('source_capacity_raw')))
    for e in events:
        if keys[e['key']]>1 and all(e[k] for k in ('day','trip','order','plate')):
            e['issues'].append('duplicate_event_key');qc['duplicate_event_rows']+=1
        if e['day'] and e['plate'] and e['plate'] not in representatives:groups[(e['day'],e['plate'])].append(e)
        candidates=by_order.get(e['order'],[])
        if not candidates:continue
        modes={classify(r,c,e['day']) for r in candidates};ags={plate(r.get('source_booking_plate','')) for r in candidates}-{''}
        flags=[];mode=next(iter(modes)) if len(modes)==1 else None
        if mode is None:flags.append('unknown_or_conflicting_mode')
        if len(ags)>1:flags.append('multiple_booking_vehicles')
        if not ags:flags.append('missing_booking_vehicle')
        if mode in ('NCC_DIRECT','LAI_CHAU_DIRECT','TRANSFER_REP_LASTMILE'):
            if e['plate'] not in representatives:flags.append('expected_representative_mismatch')
        elif mode=='GXT_DIRECT':
            if not e['unassigned_direct'] and (len(ags)!=1 or e['plate'] not in ags):flags.append('direct_vehicle_mismatch')
        elif mode=='TRANSFER_GXT':
            asset(e['plate'],'GXT_LASTMILE',e['source'])
        for f in flags:mode_q[f]+=1
        mappings.append(dict(order=e['order'],day=e['day'],raw_plate=e['plate'],booking_plates=sorted(ags),mode=mode,flags=flags,source=e['source'],unassigned_direct=e['unassigned_direct']))
    daily=[]
    with localcontext() as ctx:
        ctx.prec=100
        for (d,pl),es in sorted(groups.items()):
            bad=sorted({f for e in es for f in e['issues']})
            sums={True:Decimal(0),False:Decimal(0)}
            for e in es:
                if not e['issues'] and e['supra'] is not None:sums[e['supra']]+=e['kg']
            den=sums[True]+sums[False]
            if den==0:bad.append('zero_denominator');qc['zero_denominator_groups']+=1
            daily.append(dict(day=str(d),plate=pl,supra_kg=str(sums[True]),other_kg=str(sums[False]),
                share=str(sums[True]/den) if den and not bad else None,issues=bad,rows=len(es),
                trips=len({e['trip'] for e in es if e['trip']}),sources=[e['source'] for e in es]))
    alias={p:'V'+str(i+1) for i,p in enumerate(sorted(set(inventory)|{g['plate'] for g in daily}))}
    for g in daily:g['vehicle_alias']=alias[g['plate']]
    for m in mappings:
        m['raw_vehicle_alias']=alias.get(m['raw_plate']);m['booking_vehicle_aliases']=[alias[p] for p in m['booking_plates'] if p in alias]
    return dict(available=True,period=period,cutoff=str(cutoff.date()),booking_digest=pair['booking_digest'],raw_digest=pair['raw_digest'],catalog_sha256=pair['vehicle_catalog_sha256'],
        provenance='SOURCE_RECORD_CS_MEMBERSHIP_NOT_PHYSICAL_OR_FINANCIAL_TRUTH',
        raw_retrieved_at=raw['retrieved_at'],booking_retrieved_at=pair['booking_retrieved_at'],
        raw_first_day=str(min(all_days)) if all_days else None,raw_last_day=str(max(all_days)) if all_days else None,
        booking_last_day=str(max((r for r in data.rows.dispatch_date if r is not None),default=None)),
        source_s_groups=len(daily),source_s_vehicles=len({g['plate'] for g in daily}),
        supra_kg=str(sum((Decimal(g['supra_kg']) for g in daily),Decimal(0))),other_kg=str(sum((Decimal(g['other_kg']) for g in daily),Decimal(0))),
        role_vehicle_counts={r:len(ps) for r,ps in role_assets.items()},quality=dict(qc),mapping_quality=dict(mode_q),
        groups=daily,mappings=mappings,inventory=[dict(plate=p,alias=alias[p],roles=sorted(v['roles']),sources=sorted(v['sources']),declared_capacities=sorted(v['capacities']),
            booking_capacity_raw=sorted(v['booking_capacity_raw']),booking_declared_capacity_kg=sorted({x for z in v['booking_capacity_raw'] if (x:=capacity_kg(z)) is not None}),observed_capacity_kg=None,ag_share=None) for p,v in sorted(inventory.items())])
