"""Synthetic paired sources only; no Telegram/provider/production vault access."""
from copy import deepcopy
from datetime import datetime,timezone,timedelta
from decimal import Decimal
import hashlib,json,threading
import pandas as pd
import pytest
from ops_team.supra.booking import parse_booking,booking_context,BOOKING_HEADERS
from ops_team.supra.booking_quantity import RULE,SHEET_ID
from ops_team.supra.booking_weight import WEIGHT_RULE
from ops_team.supra.booking_handling import HANDLING_POLICY
from ops_team.supra.booking_vehicles import (PAIR_SCHEMA,BOOK_HEADERS,RAW_HEADERS,RAW_PIN,
    VEHICLE_POLICY,digest,plate,capacity_kg,vehicle_evidence,validate_vehicle_catalog)
from ops_team.supra.vehicle_report import vehicle_view,fleet_context,booking_intake,vehicle_scope,vehicle_scope_quality
from ops_team.supra.vehicle_cost import vehicle_cost_status
from ops_team.telegram_snapshot import ingest_minimized_snapshot,ingest_snapshot,load_snapshot
from ops_team.telegram_vehicle_snapshot import vehicle_plan_identity
from ops_team.telegram_secrets import save_protected,load_protected
from ops_team.telegram_state import BusinessState,GROUP_ID
from ops_team.telegram_assistant import AssistantResponder,TZ
from ops_team.telegram_bridge import Incoming
from ops_team.chief.report import booking_review

PIN=dict(spreadsheet_id=SHEET_ID,tab='DC PHÚ THỌ',gid=0)
AREA="'DC PHÚ THỌ'!A1:AK100000"
CUTOFF=datetime(2026,10,31,23,59,tzinfo=TZ)
REP='99Z99999'  # fictional, never the owner's representative identifier


def catalogs():
    common=dict(version='synthetic-v1',period='2026-10',provenance='USER_CONFIRMED_IN_CODEX',
        confirmed_date='2026-10-26',evidence_hashes=[dict(file_id='SYNTHETIC',sha256='a'*64)])
    v=dict(**deepcopy(common),schema='supra-vehicle-catalog-v1',source_pins=dict(booking=PIN,data_supra=RAW_PIN),
        representative_plates=[REP],transfer_class_kg='8000',routing=[])
    h=dict(**deepcopy(common),schema='supra-dc-handling-catalog-v1',source_pin=PIN,routing=[],
        rates=[dict(rate_id='R',**{'from':'2026-10-01','to':'2026-10-31'},vnd_per_kg='100',currency='VND',evidence_ref='synthetic')])
    for i,mode in enumerate(['TRANSFER_GXT','TRANSFER_REP_LASTMILE','GXT_DIRECT','NCC_DIRECT','LAI_CHAU_DIRECT']):
        eligible=mode in ('TRANSFER_GXT','TRANSFER_REP_LASTMILE','LAI_CHAU_DIRECT')
        r=dict(rule_id='V'+str(i),scope='DISTRICT',province='Province Synthetic',district='District '+str(i),
            store_id='',**{'from':'2026-10-01','to':'2026-10-31'},evidence_ref='synthetic')
        v['routing'].append(dict(**r,mode=mode,handling_eligible=eligible))
        h['routing'].append(dict(**r,routing='TRANSFER' if eligible else 'DIRECT'))
    return v,h


def book(order='SYN_PRIVATE_ORDER',mode=0,ag='99A12345',date='10/10/2026',do='DO1'):
    return ['10/10/2026',order,'delivered',date,'SO1',do,'STORE_SYN','10','2.5',
        'Province Synthetic','District '+str(mode),'8T',ag]


def event(order='SYN_PRIVATE_ORDER',pl='99B12345',grams='1250',trip=123,date='2026-10-10',kind='DELIVER'):
    r=dict(load_date=date,trip_code=trip,order_code=order,weight=grams,type=kind,
        truck_plate_number=pl,client_type='B2C',to_province='Province Synthetic',to_district='District 0')
    return [r.get(k,'') for k in RAW_HEADERS]


def source(headers,rows,*,raw=False,stamp=None):
    pin=RAW_PIN if raw else PIN
    return dict(**pin,source_url=f"https://docs.google.com/spreadsheets/d/{pin['spreadsheet_id']}/edit#gid={pin['gid']}",
        range="'raw_current'!A1:Y100000" if raw else AREA,retrieved_at=stamp or datetime.now(timezone.utc).isoformat(),
        raw_sha256='d'*64,raw_column_count=25 if raw else 37,headers=headers,
        rows=[dict(sheet_row=i,values=r) for i,r in enumerate(rows,2)])


def payload(books=None,raws=None,stamp=None,rawstamp=None):
    v,h=catalogs()
    return dict(schema=PAIR_SCHEMA,booking=source(BOOK_HEADERS,books or [book()],stamp=stamp),
        data_supra=source(RAW_HEADERS,raws if raws is not None else [event(),event('OTHER',grams='3750',trip=124)],raw=True,stamp=rawstamp or stamp),
        confirmed_business_rules={k:v for k,v in RULE.items() if k!='version'},
        confirmed_weight_rules={k:v for k,v in WEIGHT_RULE.items() if k!='version'},confirmed_handling_policy=HANDLING_POLICY,
        handling_catalog=h,handling_catalog_sha256=digest(h),vehicle_policy=VEHICLE_POLICY,
        vehicle_catalog=v,vehicle_catalog_sha256=digest(v))


def stage(tmp_path,p):
    b=json.dumps(p,ensure_ascii=False).encode();path=(tmp_path/'SYNTHETIC_PAIR.dpapi').resolve();save_protected(path,b)
    d={k:v for k,v in p.items() if k not in ('booking','data_supra','handling_catalog','vehicle_catalog')}
    for k in ('booking','data_supra'):d[k]={k2:v for k2,v in p[k].items() if k2!='rows'}|dict(row_count=len(p[k]['rows']))
    d.update(handling_catalog_version=p['handling_catalog']['version'],vehicle_catalog_version=p['vehicle_catalog']['version'],
        minimized_plaintext_sha256=hashlib.sha256(b).hexdigest())
    return path,d


def parsed(books=None,raws=None,stamp=None):
    p=payload(books,raws,stamp=stamp);f=pd.DataFrame([r['values'] for r in p['booking']['rows']],columns=BOOK_HEADERS)
    f['_source']=['SYNTHETIC BOOK '+str(i) for i in range(len(f))]
    data=parse_booking(f,quantity_rules=RULE,weight_rules=WEIGHT_RULE,handling_policy=HANDLING_POLICY,
        handling_catalog=p['handling_catalog'],handling_catalog_sha256=p['handling_catalog_sha256'],vehicle_fields=True)
    data.vehicle_pair=dict(schema=PAIR_SCHEMA,booking_digest=digest(p['booking']),booking_retrieved_at=p['booking']['retrieved_at'],
        data_supra=p['data_supra'],raw_digest=digest(p['data_supra']),vehicle_policy=VEHICLE_POLICY,
        vehicle_catalog=p['vehicle_catalog'],vehicle_catalog_sha256=p['vehicle_catalog_sha256'])
    return data


def evidence(books=None,raws=None):return vehicle_evidence(parsed(books,raws),'2026-10',CUTOFF)


def pins(state):
    facts=dict(sheets_id=SHEET_ID,sheets_tab=PIN['tab'],sheets_gid='0',sheets_ranges=json.dumps({'supra_booking':AREA}),
        source_id='browser_snapshot',period='2026-10',warehouses=PIN['tab'],cutoff='23:59',report_time='13:00',timezone='Asia/Ho_Chi_Minh')
    for k,v in facts.items():state.remember(k,v,123,0,GROUP_ID)
    return facts


def responder(state):return AssistantResponder(state,dict(owner_id=123,models={}))


def test_all_cs_membership_deliver_only_grams_and_no_proration_of_j_l():
    books=[book(date='01/09/2026'),book('NEW',do='DO2')]
    raws=[event(),event('OTHER',grams='3750'),event(grams='999999',kind='PICK'),event(grams='999999',kind='RETURN')]
    data=parsed(books,raws);before=booking_context(data,'2026-10',[PIN['tab']],CUTOFF,dict(tab=PIN['tab'],digest='b'*64))
    v=vehicle_evidence(data,'2026-10',CUTOFF);g=v['groups'][0]
    assert v['quality']['deliver_rows']==2 and Decimal(g['supra_kg'])==Decimal('1.250')
    assert Decimal(g['other_kg'])==Decimal('3.750') and Decimal(g['share'])==Decimal('.25')
    assert g['trips']==1  # numeric trip code accepted; distinct source codes, not dispatch count
    after=booking_context(data,'2026-10',[PIN['tab']],CUTOFF,dict(tab=PIN['tab'],digest='b'*64))
    assert before==after and after['metrics']['booking.product_units']['value']==10
    assert Decimal(after['metrics']['booking.weight_kg']['value'])==Decimal('2.5')


@pytest.mark.parametrize('mode,expectedrole',[(0,'TRANSFER_LINEHAUL'),(1,'TRANSFER_LINEHAUL'),(2,'DIRECT_GXT'),(3,'DIRECT_NCC'),(4,'DIRECT_NCC')])
def test_owner_modes_do_not_reassign_representative_or_infer_upstream_share(mode,expectedrole):
    rawplate='99A12345' if mode==2 else '99B12345' if mode==0 else REP
    v=evidence([book(mode=mode)],[event(pl=rawplate)])
    ag=next(a for a in v['inventory'] if a['plate']=='99A12345')
    assert ag['roles']==[expectedrole] and ag['ag_share'] is None and ag['observed_capacity_kg'] is None
    assert REP not in [a['plate'] for a in v['inventory']]
    assert v['mappings'][0]['mode']==['TRANSFER_GXT','TRANSFER_REP_LASTMILE','GXT_DIRECT','NCC_DIRECT','LAI_CHAU_DIRECT'][mode]
    assert not v['mapping_quality']
    if mode==0:assert v['role_vehicle_counts']['GXT_LASTMILE']==1
    if mode in (1,3,4):assert v['groups']==[]


def test_representative_booking_ag_never_creates_empty_physical_inventory():
    v=evidence([book(ag=REP)],[event(pl=REP)])
    assert not v['inventory'] and not v['role_vehicle_counts']


def test_direct_lai_chau_keeps_handling_eligibility_independent_of_vehicle():
    data=parsed([book(mode=4)],[event(pl=REP)])
    ctx=booking_context(data,'2026-10',[PIN['tab']],CUTOFF,dict(tab=PIN['tab'],digest='b'*64))
    assert Decimal(ctx['handling_quality']['component_vnd'])==Decimal('250')
    assert vehicle_evidence(data,'2026-10',CUTOFF)['mappings'][0]['mode']=='LAI_CHAU_DIRECT'


def test_multiple_booking_vehicles_and_expected_mismatch_preserve_observations():
    v=evidence([book(mode=3),book(mode=3,ag='99C12345',do='DO2')],[event()])
    m=v['mappings'][0]
    assert len(m['booking_plates'])==2 and m['raw_plate']=='99B12345'
    assert set(m['flags'])=={'multiple_booking_vehicles','expected_representative_mismatch'}
    assert Decimal(v['groups'][0]['share'])==1  # raw record ratio stays diagnostic; not AG share
    text=vehicle_view(v)
    assert 'thông tin biển hoặc tuyến cần đối chiếu' in text and 'chưa kết luận xe chạy sai' in text
    assert 'expected_representative_mismatch' not in text and 'DIRECT_NCC' not in text
    assert 'expected_representative_mismatch' in vehicle_view(v,details=True,audit=True)
    direct=evidence([book(mode=2)],[event()]);assert direct['mapping_quality']['direct_vehicle_mismatch']==1


@pytest.mark.parametrize('field,value,issue',[('order_code','','missing_order'),('trip_code','','missing_trip'),('weight','','invalid_weight'),('weight','#REF!','invalid_weight')])
def test_bad_identifiable_event_holds_group_no_silent_partial_ratio(field,value,issue):
    bad=event();bad[RAW_HEADERS.index(field)]=value
    v=evidence(raws=[event(),bad,event('OTHER',pl='99C12345',trip=456)])
    held=next(g for g in v['groups'] if g['plate']=='99B12345')
    assert held['share'] is None and issue in held['issues']
    assert next(g for g in v['groups'] if g['plate']=='99C12345')['share'] is not None


@pytest.mark.parametrize('different_weight',[False,True])
def test_duplicate_key_holds_all_records_no_first_last_or_silent_sum(different_weight):
    a=event();b=event(grams='3000' if different_weight else '1250')
    v=evidence(raws=[a,b,event('OTHER',trip=456)])
    assert v['quality']['duplicate_event_rows']==2
    assert v['groups'][0]['share'] is None and 'duplicate_event_key' in v['groups'][0]['issues']
    assert Decimal(v['groups'][0]['supra_kg'])==0


def test_missing_date_plate_zero_weight_and_coverage_are_explicit():
    v=evidence([book(date='26/10/2026')],[event(pl=''),event(date=''),event('OTHER',pl='99C12345',grams='0')])
    assert v['quality']['missing_plate']==1 and v['quality']['missing_date']==1
    assert v['quality']['zero_denominator_groups']==1 and v['groups'][0]['share'] is None
    text=vehicle_view(v)
    assert 'Kỳ xem riêng 10/10/2026' in text and '26/10' not in text
    assert 'không coi thiếu quan sát' in vehicle_view(v,details=True,audit=True)
    assert v['booking_last_day']=='2026-10-26' and v['raw_last_day']=='2026-10-10'


def test_normalization_capacity_and_bounded_private_view():
    assert plate('99b-123.45')=='99B12345'
    assert capacity_kg('8T')=='8000' and capacity_kg('1,5 tấn')=='1500.0' and capacity_kg('8') is None
    raws=[event('OTHER'+str(i),pl='99B'+str(10000+i),trip=i) for i in range(20)]
    v=evidence(raws=raws);text=vehicle_view(v);details=vehicle_view(v,details=True,audit=True)
    assert 'tỷ lệ S-source' not in text and details.count('tỷ lệ S-source')==10
    assert 'SYN_PRIVATE_ORDER' not in details and '99B10000' not in details
    alias=v['groups'][0]['vehicle_alias'];assert 'tỷ trọng Supra 0,00%' in vehicle_view(v,'2026-10-10 '+alias)
    for query in ('bad','2026-13-40','V1 V2','2026-10-10 V1 extra'):
        with pytest.raises(ValueError):vehicle_view(v,query)
    ctx=fleet_context(v);encoded=json.dumps(ctx)
    assert not any(s in encoded for s in (REP,'99A12345','99B10000','SYN_PRIVATE_ORDER','Province Synthetic','routing'))
    assert len(ctx['metrics'])<=10 and ctx['source_provenance']['raw_digest']==v['raw_digest']


def test_pair_atomic_roundtrip_and_code_commands_zero_http(tmp_path,monkeypatch):
    state=BusinessState(tmp_path/'vault');facts=pins(state);before=state.facts()
    try:
        path,d=stage(tmp_path,payload());result=ingest_minimized_snapshot(path,d,state,123)
        assert state.facts()==before and result['requires_plan_review']
        data,src=load_snapshot(state,facts);v=vehicle_evidence(data,'2026-10',CUTOFF)
        assert len(data.rows)==1 and len(v['groups'])==1 and src['vehicle_raw_created']<=datetime.now(timezone.utc).timestamp()
        a=responder(state)
        monkeypatch.setattr(a,'_get_agent',lambda **kw:pytest.fail('code-only command called model'))
        for cmd in ('vehicles','xe'):
            text=a.answer(Incoming(GROUP_ID,123,1,None,cmd,''))
            assert '1,250kg hàng Supra' in text and '3,750kg hàng khác' in text
        assert v['raw_digest'] not in a.answer(Incoming(GROUP_ID,123,2,None,'details',''))
        assert v['raw_digest'] in a.answer(Incoming(GROUP_ID,123,2,None,'details','audit'))
        assert 'Chỉ chủ sở hữu' in a.answer(Incoming(GROUP_ID,456,3,None,'details',''))
        report=a.report();assert len(report)<2200 and 'chưa dùng ai' in report.lower()
        assert 'BƯỚC TIẾP: Bước tiếp theo' not in report
        assert len(a._report_details)>len(report) and 'HTTP attempts cho yêu cầu này: 0' in a._report_details
        a._code_details={(GROUP_ID,i,123):{'kind':'vehicles','evidence':v,'filter':''} for i in range(100)}
        a.answer(Incoming(GROUP_ID,123,3,200,'vehicles',''))
        assert len(a._code_details)==100
        a.answer(Incoming(GROUP_ID,123,4,None,'vehicles',''))
        assert 'Chưa hoàn tất' in a.answer(Incoming(GROUP_ID,123,4,None,'vehicles','bad'))
        assert 'Em chưa giữ chi tiết câu trả lời trong chủ đề này' in a.answer(Incoming(GROUP_ID,123,5,None,'details',''))
    finally:state.close()
    state=BusinessState(tmp_path/'vault')
    try:assert len(load_snapshot(state,facts)[0].vehicle_pair['data_supra']['rows'])==2
    finally:state.close()


@pytest.mark.parametrize('mutation',['raw_pin','book_pin','version','hash','header','timestamp','fee_eligibility','cancel','stale'])
def test_pair_rejects_bad_payload_without_changing_pointer(tmp_path,mutation):
    state=BusinessState(tmp_path/'vault');pins(state)
    try:
        p=payload();path,d=stage(tmp_path,p);ingest_minimized_snapshot(path,d,state,123)
        pointer=(state.root/'snapshot-latest.dpapi').read_bytes();event_cancel=threading.Event()
        p=deepcopy(p)
        if mutation=='raw_pin':p['data_supra']['gid']=1
        elif mutation=='book_pin':p['booking']['gid']=1
        elif mutation=='version':p['vehicle_catalog']['version']=''
        elif mutation=='hash':p['vehicle_catalog_sha256']='f'*64
        elif mutation=='header':p['data_supra']['headers'][0]='unapproved'
        elif mutation=='timestamp':p['data_supra']['retrieved_at']=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
        elif mutation=='fee_eligibility':
            p['vehicle_catalog']['routing'][4]['handling_eligible']=False;p['vehicle_catalog_sha256']=digest(p['vehicle_catalog'])
        elif mutation=='cancel':event_cancel.set()
        elif mutation=='stale':p['data_supra']['retrieved_at']=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
        path,d=stage(tmp_path,p)
        with pytest.raises(ValueError):ingest_minimized_snapshot(path,d,state,123,cancel=event_cancel)
        assert (state.root/'snapshot-latest.dpapi').read_bytes()==pointer
    finally:state.close()


def full_refresh(p):
    rows=[]
    for r in p['booking']['rows']:
        out=['']*37
        for k,v in zip(BOOK_HEADERS,r['values']):out[[x.strip() for x in BOOKING_HEADERS].index(k.strip())]=v
        rows.append(out)
    return dict(version=1,source='GOOGLE_BROWSER_VIEWER_EXPORT',**PIN,range=AREA,
        retrieved_at=datetime.now(timezone.utc).isoformat(),headers=BOOKING_HEADERS,rows=rows)


def test_primary_refresh_keeps_raw_age_plan_identity_and_invalidates_cache(tmp_path):
    state=BusinessState(tmp_path/'vault');facts=pins(state)
    try:
        a=responder(state);oldplan=a._plan_code();p=payload(rawstamp=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat())
        path,d=stage(tmp_path,p);ingest_minimized_snapshot(path,d,state,123)
        pairedplan=a._plan_code();assert pairedplan!=oldplan
        state.set_control('approved_plan',oldplan)
        data,src=load_snapshot(state,facts);v=a._vehicle_evidence(data,'2026-10',CUTOFF);identity=vehicle_plan_identity(state)
        ingest_snapshot(full_refresh(p),state,123);data2,src2=load_snapshot(state,facts)
        assert src2['created']>=src['created'] and src2['vehicle_raw_created']==src['vehicle_raw_created']
        assert data2.vehicle_pair['raw_digest']==data.vehicle_pair['raw_digest']
        assert data2.vehicle_pair['booking_digest']!=data.vehicle_pair['booking_digest']
        assert vehicle_plan_identity(state)==identity and a._plan_code()==pairedplan
        assert state.control('approved_plan')==oldplan
        v2=a._vehicle_evidence(data2,'2026-10',CUTOFF);assert v2 is not v
        assert 'quá15 phút' in vehicle_view(v2,details=True,audit=True) and any('paired Raw' in m for m in a.plan()[2])
        changed=deepcopy(data2);changed.vehicle_pair['data_supra']['rows'][1]['values'][RAW_HEADERS.index('weight')]='6250'
        changed.vehicle_pair['raw_digest']=digest(changed.vehicle_pair['data_supra'])
        v3=a._vehicle_evidence(changed,'2026-10',CUTOFF)
        assert v3 is not v2 and Decimal(v3['groups'][0]['other_kg'])==Decimal('6.25')
        changed.vehicle_pair['vehicle_catalog']['routing'][0]['mode']='GXT_DIRECT'
        changed.vehicle_pair['vehicle_catalog_sha256']=digest(changed.vehicle_pair['vehicle_catalog'])
        v4=a._vehicle_evidence(changed,'2026-10',CUTOFF)
        assert v4 is not v3 and v4['mapping_quality']['direct_vehicle_mismatch']==1
    finally:state.close()


@pytest.mark.parametrize('rawfresh',[False,True])
def test_scheduled_pair_requires_both_sources_fresh_before_claim_or_send(tmp_path,monkeypatch,rawfresh):
    state=BusinessState(tmp_path/'vault');pins(state)
    try:
        now=datetime.now(TZ).replace(hour=13,minute=0,second=0,microsecond=0)
        # Use a past due day; timestamps remain real source captures in test fixtures.
        if now>datetime.now(TZ):now-=timedelta(days=1)
        p=payload(stamp=now.isoformat(),rawstamp=(now-timedelta(minutes=0 if rawfresh else 16)).isoformat())
        path,d=stage(tmp_path,p);ingest_minimized_snapshot(path,d,state,123)
        a=responder(state);state.set_control('approved_plan',a._plan_code());state.set_control('schedule_enabled',True)
        sent=[];monkeypatch.setattr('ops_team.telegram_assistant.time.time',lambda:now.timestamp())
        monkeypatch.setattr(a,'report',lambda:'SYNTHETIC REPORT')
        result=a.tick(lambda *args:sent.append(args),now=now,external_snapshot=True)
        assert result['state']==('SENT' if rawfresh else 'AWAITING_FRESH_SOURCE')
        assert bool(sent)==rawfresh and state.control('schedule_enabled')
        if not rawfresh:
            fresh=payload(stamp=now.isoformat());path,d=stage(tmp_path,fresh);ingest_minimized_snapshot(path,d,state,123)
            assert a.tick(lambda *args:sent.append(args),now=now,external_snapshot=True)['state']=='SENT'
    finally:state.close()


def test_legacy_no_pair_explicit_na():
    data=parsed();data.vehicle_pair=None
    assert not vehicle_evidence(data,'2026-10',CUTOFF)['available']
    assert 'chưa có đủ hai nguồn dữ liệu' in vehicle_view(vehicle_evidence(data,'2026-10',CUTOFF))


def presentation_evidence():
    raws=[event(grams='3030293'),event('OTHER',grams='1520499',trip=124),
        event(pl='99C12345',grams='100000',trip=125),event('OTHER',pl='99C12345',grams='900000',trip=126),
        event(pl='99D12345',grams='999000',trip=127),event(pl='99D12345',grams='999000',trip=127),
        event(grams='12121200',date='2026-10-11',trip=128),event('OTHER',pl='',grams='500000',trip=129)]
    return vehicle_evidence(parsed([book(date='14/10/2026')],raws,stamp='2026-10-15T14:00:00+00:00'),'2026-10',CUTOFF)


def test_manager_summary_scopes_daily_decimal_totals_and_keeps_evidence_in_details():
    import re
    v=presentation_evidence();original=deepcopy(v)
    main=vehicle_view(v,'2026-10-10');details=vehicle_view(v,'2026-10-10',details=True,audit=True)
    assert 500<=len(main)<=900
    assert '3.130,293kg hàng Supra' in main and '2.420,499kg hàng khác' in main
    assert 'tỷ trọng Supra 56,39%' in main and 'kể cả xe/ngày không ghi nhận Supra' in main
    assert 'Đây là bản dữ liệu đã lưu, chưa được cập nhật lại.' in main
    assert '12/10 đến 14/10' not in main and not re.search(r'\bV\d+\b',main)
    assert not any(term in main for term in ('ALL CS','DELIVER','S-source','AG upstream','N/A','cutoff','0 HTTP','Versions:'))
    assert v['raw_digest'] not in main and v['raw_digest'] in details
    assert all(term in details for term in ('duplicate_event_rows','duplicate_event_key','TRANSFER_LINEHAUL','N/A','chưa hiển thị'))
    assert '21:00 15/10/2026 (giờ Việt Nam)' in details
    assert v==original  # presentation leaves ratios, counters, precision and hashes intact


def test_selected_temporary_vehicle_preserves_ratio_and_holds_incomplete_group():
    v=presentation_evidence()
    good=next(g for g in v['groups'] if g['day']=='2026-10-10' and g['plate']=='99B12345')
    held=next(g for g in v['groups'] if g['plate']=='99D12345')
    selected=vehicle_view(v,'2026-10-10 '+good['vehicle_alias'])
    assert '3.030,293kg' in selected and '1.520,499kg' in selected and '66,59%' in selected
    assert good['vehicle_alias'] in selected and 'mã tạm của bản này' in selected
    assert '99B12345' not in selected and 'SYN_PRIVATE_ORDER' not in selected
    unresolved=vehicle_view(v,'2026-10-10 '+held['vehicle_alias'])
    assert 'chưa đủ dữ liệu để tính tỷ trọng' in unresolved and '0,00%' not in unresolved and held['share'] is None


def test_no_observation_is_not_reported_as_zero_or_no_delivery():
    v=presentation_evidence()
    for query in ('2026-10-30','2026-10-10 V999'):
        text=vehicle_view(v,query)
        assert 'chưa có ghi nhận' in text.lower() and '0,000kg' not in text and 'chưa chạy' not in text
    data=parsed(raws=[])
    empty=vehicle_view(vehicle_evidence(data,'2026-10',CUTOFF))
    assert 'chưa có ghi nhận' in empty.lower() and 'N/A' not in empty


def range_rows():
    return [event(grams='100000',date='2026-10-10',trip=101),
        event('OTHER',grams='900000',date='2026-10-10',trip=102),
        event('OTHER',pl='99C12345',grams='3000000',date='2026-10-10',trip=103),
        event(grams='900000',date='2026-10-11',trip=104),
        event('OTHER',grams='100000',date='2026-10-11',trip=105),
        event(grams='9000000',date='2026-10-12',trip=106),
        event(pl='99D12345',grams='500000',date='2026-10-10',trip=107),
        event(pl='99D12345',grams='500000',date='2026-10-10',trip=107),
        event('OTHER',pl='',date='2026-10-10',trip=108),
        event('OTHER',pl='',date='2026-10-11',trip=109),
        event('OTHER',pl='',date='2026-10-12',trip=110),
        event('OTHER',pl=REP,date='2026-10-10',trip=111),
        event('OTHER',date='',trip=112),
        event('OTHER',pl='',date='2026-10-10',trip=113,kind='PICK')]


def test_range_uses_weighted_total_and_distinct_supra_cohort_without_crossing_bounds():
    data=parsed([book(date='14/10/2026')],range_rows());v=vehicle_evidence(data,'2026-10',CUTOFF);original=deepcopy(v)
    query='2026-10-10 2026-10-11';quality=vehicle_scope_quality(data,v,query)
    assert quality=={'missing_plate':2,'representative':1,'undated':True}
    main=vehicle_view(v,query,scope_quality=quality)
    details=vehicle_view(v,query,details=True,source_pin=RAW_PIN,scope_quality=quality)
    assert len(main)<=1000 and len(details)<=1800
    assert '10/10/2026–11/10/2026' in main and '1.000,000kg hàng Supra' in main and '4.000,000kg hàng khác' in main
    assert 'tỷ trọng Supra 20,00%' in main and 'kể cả xe/ngày không ghi nhận Supra' in main
    assert '2 dòng thiếu/sai biển; 1 dòng biển đại diện' in main and 'thiếu ngày' in main
    assert '1 nhóm xe/ngày' in main and '12/10 đến 14/10' not in main and '26/10' not in main
    assert 'Nhóm 2 xe/ngày có ghi nhận Supra' in details and '1.000,000kg hàng khác cùng xe/ngày' in details
    assert 'tỷ trọng 50,00%' in details and '12/10–14/10' not in details
    assert '9.000,000kg' not in main and '9.000,000kg' not in details and v==original
    default_quality=vehicle_scope_quality(data,v,'');default=vehicle_view(v,scope_quality=default_quality)
    assert '10/10/2026–12/10/2026' in default and '10.000,000kg hàng Supra' in default
    assert 'tỷ trọng Supra 71,43%' in default and default_quality['missing_plate']==3


def test_range_scoped_quality_and_catalog_binding_do_not_reuse_month_counts():
    data=parsed([book(date='14/10/2026')],range_rows());v=vehicle_evidence(data,'2026-10',CUTOFF)
    q10=vehicle_scope_quality(data,v,'2026-10-10');q11=vehicle_scope_quality(data,v,'2026-10-11')
    assert q10=={'missing_plate':1,'representative':1,'undated':True}
    assert q11=={'missing_plate':1,'representative':0,'undated':True}
    changed=deepcopy(data);changed.vehicle_pair['vehicle_catalog_sha256']='f'*64
    assert vehicle_scope_quality(changed,v,'2026-10-10') is None
    alias=next(g['vehicle_alias'] for g in v['groups'] if g['plate']=='99B12345')
    text=vehicle_view(v,'2026-10-10 2026-10-11 '+alias,scope_quality=vehicle_scope_quality(data,v,'2026-10-10 2026-10-11 '+alias))
    assert '50,00%' in text and 'Dòng thiếu biển' not in text  # source-wide counts cannot be assigned to one vehicle
    assert 'dòng thiếu biển hoặc biển đại diện không thể gán' in text


@pytest.mark.parametrize('query',['2026-10-11 2026-10-10','2026-09-30 2026-10-01',
    '2026-10-01 2026-11-01','2026-10-01 2026-10-32','2026-10-01 2026-10-02 2026-10-03',
    '2026-10-01 V1234567'])
def test_range_rejects_reversed_invalid_out_of_scope_or_unbounded_filters(query):
    v=vehicle_evidence(parsed([book(date='14/10/2026')],range_rows()),'2026-10',CUTOFF)
    with pytest.raises(ValueError):vehicle_scope(v,query)


def test_range_single_day_empty_held_and_raw_coverage_are_explicit():
    data=parsed([book(date='14/10/2026')],range_rows());v=vehicle_evidence(data,'2026-10',CUTOFF)
    day10=vehicle_view(v,'2026-10-10',scope_quality=vehicle_scope_quality(data,v,'2026-10-10'))
    assert '100,000kg hàng Supra' in day10 and '3.900,000kg hàng khác' in day10 and '2,50%' in day10
    held=next(g['vehicle_alias'] for g in v['groups'] if g['plate']=='99D12345')
    held_text=vehicle_view(v,'2026-10-10 2026-10-11 '+held,scope_quality=vehicle_scope_quality(data,v,'2026-10-10 2026-10-11 '+held))
    assert 'chưa đủ dữ liệu để tính tỷ trọng' in held_text and '0,00%' not in held_text
    upstream=next(g['alias'] for g in v['inventory'] if g['plate']=='99A12345')
    no_share=vehicle_view(v,'2026-10-10 2026-10-11 '+upstream)
    assert 'chưa có ghi nhận' in no_share.lower() and '100,00%' not in no_share
    wider=vehicle_view(v,'2026-10-01 2026-10-22',scope_quality=vehicle_scope_quality(data,v,'2026-10-01 2026-10-22'))
    assert 'Bản nguồn bắt đầu từ 10/10' in wider and 'chỉ ghi nhận đến 12/10' in wider
    assert 'chưa có căn cứ đối chiếu' in wider


def test_range_responder_captures_scoped_quality_and_replays_exact_response(tmp_path,monkeypatch):
    from ops_team.telegram_bridge import parse_message
    state=BusinessState(tmp_path/'vault');pins(state)
    try:
        path,d=stage(tmp_path,payload([book(date='14/10/2026')],range_rows()));ingest_minimized_snapshot(path,d,state,123)
        a=responder(state);monkeypatch.setattr(a,'_get_agent',lambda **kw:pytest.fail('range view must not call model'))
        query='2026-10-10 2026-10-11'
        incoming=parse_message({'message_id':1,'chat':{'id':GROUP_ID,'type':'supergroup'},
            'from':{'id':123,'is_bot':False},'text':'/xe@test_bot '+query},'test_bot',99)
        assert incoming.command=='xe' and incoming.text==query
        main=a.answer(incoming)
        key=(GROUP_ID,None,123);record=deepcopy(a._code_details[key]);assert record['filter']==query
        assert record['scope_quality']=={'missing_plate':2,'representative':1,'undated':True}
        monkeypatch.setattr(a,'_load',lambda:pytest.fail('details must not reload source'))
        action=a.answer(Incoming(GROUP_ID,123,2,None,'details',''))
        audit=a.answer(Incoming(GROUP_ID,123,3,None,'details','audit'))
        assert '20,00%' in action and '50,00%' in action and '12/10–14/10' not in action
        assert main in audit and 'Phạm vi xem: 10/10/2026–11/10/2026' in audit
        assert record['evidence']['raw_digest'] in audit and 'ngoài số liệu phạm vi xem' in audit
        assert a._code_details[key]==record
        assert 'Chỉ chủ sở hữu' in a.answer(Incoming(GROUP_ID,456,4,None,'details','audit'))
    finally:state.close()


def cost_fixture(raw_grams='1250'):
    def b(ghn,when,*,mode=0,weight='2.5',do='DO1'):
        r=book(ghn,mode=mode,date=when,do=do);r[8]=weight;return r
    values=[b('FULL','10/10/2026'),b('FULL','17/10/2026',do='DO2'),
        b('SECOND','19/10/2026',weight='1.250'),b('DIRECT','19/10/2026',mode=2,weight='#REF!'),
        b('DIRECTLC','20/10/2026',mode=4,weight='2'),b('MISSING','20/10/2026',weight=''),
        b('LEFT','09/10/2026'),b('LEFT','10/10/2026',do='DO2'),
        b('RIGHT','20/10/2026'),b('RIGHT','21/10/2026',do='DO2'),
        b('UNDATED','10/10/2026'),b('UNDATED','',do='DO2'),
        b('RATES','18/10/2026'),b('RATES','19/10/2026',do='DO2'),
        b('ROUTES','15/10/2026'),b('ROUTES','15/10/2026',mode=2,do='DO2'),
        b('BADL','19/10/2026',weight='#REF!'),b('CONFLICT','12/10/2026',weight='1.5'),
        b('CONFLICT','12/10/2026',weight='2.5',do='DO2')]
    data=parsed(values,[event(grams=raw_grams),event('OTHER',grams='3000',trip=124)])
    data.handling_catalog['rates']=[
        dict(rate_id='R1',**{'from':'2026-10-01','to':'2026-10-18'},vnd_per_kg='200',currency='VND',evidence_ref='synthetic'),
        dict(rate_id='R2',**{'from':'2026-10-19','to':'2026-10-31'},vnd_per_kg='100',currency='VND',evidence_ref='synthetic')]
    data.handling_catalog_sha256=digest(data.handling_catalog)
    return data,vehicle_evidence(data,'2026-10',CUTOFF)


def test_scoped_handling_preserves_once_per_ghn_boundaries_and_direct_exception():
    data,v=cost_fixture();original=deepcopy(data.rows);scope='2026-10-10 2026-10-20'
    cost=vehicle_cost_status(data,v,scope)
    assert cost['available'] and cost['component_vnd'] is None and cost['total_cost_vnd'] is None and cost['gross_profit_vnd'] is None
    assert Decimal(cost['eligible_vnd'])==Decimal('825.000') and cost['eligible_transfer_ghn']==3
    assert [Decimal(b['eligible_vnd']) for b in cost['rate_buckets']]==[Decimal('500.000'),Decimal('325.000')]
    assert cost['direct_excluded_ghn']==1 and cost['unresolved_ghn']==8 and cost['boundary_ghn']==3
    assert cost['counters']['partial_scope']==3 and cost['counters']['mixed_rate']==1 and cost['counters']['mixed_routing']==1
    assert cost['counters']['missing_weight']==1 and cost['counters']['invalid_weight']==1 and cost['counters']['conflicting_weight']==1
    assert {e['order_id'] for e in cost['group_evidence'] if 'partial_scope' in e['issues']}=={'LEFT','RIGHT','UNDATED'}
    pd.testing.assert_frame_equal(data.rows,original)
    more_raw,v_more=cost_fixture(raw_grams='999999999')
    assert vehicle_cost_status(more_raw,v_more,scope)['eligible_vnd']==cost['eligible_vnd']


def test_cost_alias_never_receives_all_vehicle_handling_and_catalog_guards():
    data,v=cost_fixture();query='2026-10-10 2026-10-20'
    alias=vehicle_cost_status(data,v,query+' V1')
    assert not alias['available'] and alias['reason']=='VEHICLE_ALLOCATION_NOT_AVAILABLE' and 'eligible_vnd' not in alias
    different=deepcopy(data);different.vehicle_pair['raw_digest']='f'*64
    with pytest.raises(ValueError):vehicle_cost_status(different,v,query)
    different=deepcopy(data);different.handling_catalog['source_pin']['gid']=999
    different.handling_catalog_sha256=digest(different.handling_catalog)
    with pytest.raises(ValueError):vehicle_cost_status(different,v,query)
    different=deepcopy(data);different.handling_catalog['rates'][0]['vnd_per_kg']='999'
    with pytest.raises(ValueError):vehicle_cost_status(different,v,query)
    missing=deepcopy(data);missing.handling_catalog=None
    assert vehicle_cost_status(missing,v,query)['reason']=='NO_APPROVED_HANDLING_CONFIG'
    changed=deepcopy(data);changed.handling_catalog['rates'][0]['vnd_per_kg']='300'
    changed.handling_catalog_sha256=digest(changed.handling_catalog)
    revised=vehicle_cost_status(changed,v,query)
    assert Decimal(revised['eligible_vnd'])==Decimal('1075.000')
    assert revised['handling_catalog_sha256']!=vehicle_cost_status(data,v,query)['handling_catalog_sha256']


def test_scoped_handling_complete_direct_zero_and_empty_range_are_not_total_cost():
    direct=book('DIRECT',date='19/10/2026',mode=2);direct[8]='#REF!'
    data=parsed([direct],[event(date='2026-10-19')]);v=vehicle_evidence(data,'2026-10',CUTOFF)
    zero=vehicle_cost_status(data,v,'2026-10-19')
    assert zero['complete'] and Decimal(zero['component_vnd'])==0 and zero['direct_excluded_ghn']==1
    assert zero['total_cost_vnd'] is None and zero['gross_profit_vnd'] is None
    empty=vehicle_cost_status(data,v,'2026-10-20')
    assert empty['booking_rows']==0 and empty['component_vnd'] is None and empty['eligible_vnd'] is None


def test_range_boundary_retains_all_original_issue_flags_without_double_counting_ghn():
    a=book('BOUNDARY',date='18/10/2026');a[8]=''
    b=book('BOUNDARY',date='19/10/2026',do='DO2');b[8]=''
    data=parsed([a,b],[event(date='2026-10-19')])
    data.handling_catalog['rates']=[
        dict(rate_id='R1',**{'from':'2026-10-01','to':'2026-10-18'},vnd_per_kg='200',currency='VND',evidence_ref='synthetic'),
        dict(rate_id='R2',**{'from':'2026-10-19','to':'2026-10-31'},vnd_per_kg='100',currency='VND',evidence_ref='synthetic')]
    data.handling_catalog_sha256=digest(data.handling_catalog)
    v=vehicle_evidence(data,'2026-10',CUTOFF);cost=vehicle_cost_status(data,v,'2026-10-19')
    assert cost['unresolved_ghn']==1 and cost['boundary_ghn']==1 and cost['component_vnd'] is None
    assert set(cost['group_evidence'][0]['issues'])=={'partial_scope','mixed_rate','missing_weight'}
    assert all(cost['counters'][k]==1 for k in ('partial_scope','mixed_rate','missing_weight'))


def test_cost_presentation_prioritizes_terms_and_estimate_gaps_without_promoting_actual():
    data,v=cost_fixture();query='2026-10-10 2026-10-20';cost=vehicle_cost_status(data,v,query)
    quality=vehicle_scope_quality(data,v,query)
    main=vehicle_view(v,query,scope_quality=quality,cost_record=cost)
    details=vehicle_view(v,query,details=True,scope_quality=quality,cost_record=cost,source_pin=RAW_PIN)
    audit=vehicle_view(v,query,details=True,audit=True,scope_quality=quality,cost_record=cost,source_pin=RAW_PIN,answered_text=main)
    assert len(main)<=1000 and len(details)<=1800
    assert '825,000đ' in main and '8 GHN cần đối chiếu' in main and 'tổng khoản chưa chốt' in main
    assert details.index('Bốc xếp')<details.index('DỰ KIẾN xe')<details.index('xe/ngày đủ dữ liệu')
    assert all(s in details for s in ('825,000đ','8 GHN cần đối chiếu','chưa nối tính','DỰ KIẾN giao',
        'lương/kho/phát sinh','Giá doanh thu đã chốt','chưa nối tính','bảng giá cũ','không điền 0',
        '[giá/số tiền NCC]','[chứng từ]','Dự kiến không là thực chi','Telegram chưa nhập riêng'))
    assert '23/10' not in details and '26/10' not in details and '100k/điểm' not in details
    assert not any(s in main+details+audit for s in ('SYN_PRIVATE_ORDER','99A12345','99B12345','GHN_FULL'))
    assert main in audit and cost['handling_catalog_sha256'] in audit and cost['booking_digest'] in audit
    assert 'Giá 200đ/kg: 1 GHN; 2,500kg; 500,000đ' in audit
    assert 'Giá 100đ/kg: 2 GHN; 3,250kg; 325,000đ' in audit
    assert 'có thể trùng một GHN, không cộng làm tổng' in audit and cost['total_cost_vnd'] is None


def test_cost_vehicle_filter_does_not_assign_range_component_to_alias():
    data,v=cost_fixture();query='2026-10-10 2026-10-20 V1';cost=vehicle_cost_status(data,v,query)
    main=vehicle_view(v,query,cost_record=cost);details=vehicle_view(v,query,details=True,cost_record=cost,source_pin=RAW_PIN)
    audit=vehicle_view(v,query,details=True,audit=True,cost_record=cost,source_pin=RAW_PIN,answered_text=main)
    assert 'Bốc xếp riêng xe: chưa gán được' in main and 'Bốc xếp riêng xe: chưa gán được' in details
    assert '825,000đ' not in main+details+audit and 'Giá 200đ/kg:' not in audit


def test_cost_trace_replays_range_result_without_reload_or_model(tmp_path,monkeypatch):
    data,v=cost_fixture();state=BusinessState(tmp_path/'vault');facts=pins(state)
    try:
        a=responder(state);source=dict(tab=PIN['tab'],digest='b'*64)
        monkeypatch.setattr(a,'_scoped',lambda:(data,source,facts,'2026-10',[PIN['tab']],CUTOFF,0))
        monkeypatch.setattr(a,'_get_agent',lambda **kw:pytest.fail('cost views must not call model'))
        query='2026-10-10 2026-10-20';main=a.answer(Incoming(GROUP_ID,123,1,None,'xe',query))
        key=(GROUP_ID,None,123);record=deepcopy(a._code_details[key]);assert record['filter']==query
        assert Decimal(record['cost_record']['eligible_vnd'])==Decimal('825.000')
        monkeypatch.setattr(a,'_scoped',lambda:pytest.fail('details must use captured cost evidence'))
        details=a.answer(Incoming(GROUP_ID,123,2,None,'details',''))
        audit=a.answer(Incoming(GROUP_ID,123,3,None,'details','audit'))
        assert '825,000đ' in main and '825,000đ' in details and main in audit
        assert record['cost_record']['handling_catalog_sha256'] in audit and a._code_details[key]==record
        assert 'Chỉ chủ sở hữu' in a.answer(Incoming(GROUP_ID,456,4,None,'details','audit'))
    finally:state.close()


def forecast_fixture(tmp_path,basis='OWNER_CONFIRMED_BOOKING_T_AG',custom_books=None,custom_raws=None):
    from ops_team.telegram_vehicle_forecast import SCHEMA
    def row(ghn,when,ag,capacity='7T',mode=0):
        r=book(ghn,mode=mode,date=when,ag=ag,do='DO_'+ghn);r[11]=capacity;return r
    books=[row('F01','10/10/2026','99A12345'),row('F02','10/10/2026','99A12345'),row('OTHER_ROUTE','10/10/2026','99A12345','7T',3),
        row('OTHER_CLASS','11/10/2026','99C12345','5T',6),
        row('MIXED_A','12/10/2026','99D12345','7T'),row('MIXED_B','12/10/2026','99D12345','5T'),
        row('MISSING_AG','13/10/2026',''),row('MULTI','14/10/2026','99E12345'),row('MULTI','14/10/2026','99F12345'),
        row('MIXED_ROUTE','15/10/2026','99G12345'),row('MIXED_ROUTE','15/10/2026','99G12345','7T',3),
        row('MISSING_T','16/10/2026','99H12345',''),row('F03','17/10/2026','99A12345')]
    if custom_books is not None:books=custom_books
    p=payload(books,custom_raws if custom_raws is not None else [event(date='2026-10-10'),event('OTHER',trip=124,date='2026-10-20')])
    remote=deepcopy(p['vehicle_catalog']['routing'][4]);remote['rule_id']='V8';remote['district']='District 8'
    p['vehicle_catalog']['routing'].append(remote)
    rh=deepcopy(p['handling_catalog']['routing'][4]);rh['rule_id']='H8';rh['district']='District 8'
    p['handling_catalog']['routing'].append(rh)
    extra=deepcopy(p['vehicle_catalog']['routing'][0]);extra['rule_id']='V6';extra['district']='District 6'
    p['vehicle_catalog']['routing'].append(extra);p['vehicle_catalog_sha256']=digest(p['vehicle_catalog'])
    h=deepcopy(p['handling_catalog']['routing'][0]);h['rule_id']='H6';h['district']='District 6'
    p['handling_catalog']['routing'].append(h);p['handling_catalog_sha256']=digest(p['handling_catalog'])
    state=BusinessState(tmp_path/'vault');facts=pins(state)
    state.set_control('paired',dict(chat_id=GROUP_ID,owner_id=123))
    path,descriptor=stage(tmp_path,p);ingest_minimized_snapshot(path,descriptor,state,123)
    data,source=load_snapshot(state,facts);v=vehicle_evidence(data,'2026-10',CUTOFF)
    selected=[p['vehicle_catalog']['routing'][0],p['vehicle_catalog']['routing'][-1]]
    cfg=dict(schema=SCHEMA,version='synthetic-v1',owner_id=123,group_id=GROUP_ID,
        booking_pin={k:p['booking'][k] for k in ('spreadsheet_id','tab','gid','range')},
        raw_pin={k:p['data_supra'][k] for k in ('spreadsheet_id','tab','gid','range')},period='2026-10',
        window={'start':'2026-10-10','end':'2026-10-20'},vehicle_catalog_sha256=p['vehicle_catalog_sha256'],
        handling_catalog_sha256=p['handling_catalog_sha256'],route_selector={'scope':'DISTRICT','routes':[
            {k:r[k] for k in ('rule_id','province','district')} for r in selected]},
        vehicle={'source':'BOOKING_AG','declared_class_kg':'7000','class_basis':basis},
        time={'source':'BOOKING_E','counting':'DISTINCT_DATE_AG','shifts_per_vehicle_day':'1','shift_hours':'10'},
        rate={'amount_vnd':'1234567','currency':'VND','unit':'PER_SHIFT'},
        inclusions={'fuel':'UNKNOWN','driver_wage':'UNKNOWN','tolls':'UNKNOWN','vat':'UNKNOWN'},
        provenance={'source':'USER_CONFIRMED_IN_CODEX','confirmed_date':'2026-10-26'})
    return state,facts,data,source,v,cfg


def stage_forecast(tmp_path,cfg):
    raw=json.dumps(cfg,ensure_ascii=False).encode();path=(tmp_path/'SYNTHETIC_SHIFT.dpapi').resolve();save_protected(path,raw)
    return path,{'schema':cfg['schema'],'sha256':hashlib.sha256(raw).hexdigest()}


def multi_fixture(cfg):
    from ops_team.telegram_vehicle_forecast import MULTI_SCHEMA
    cfg=deepcopy(cfg);cfg['schema']=MULTI_SCHEMA
    cfg['vehicle']={'source':'BOOKING_AG','class_basis':'OWNER_CONFIRMED_BOOKING_T_AG'}
    cfg['time']={'source':'BOOKING_E','counting':'DISTINCT_DATE_AG'}
    cfg.pop('rate');cfg.pop('inclusions')
    inc={'fuel':'INCLUDED','driver_wage':'INCLUDED','tolls':'INCLUDED','vat':'EXCLUDED','dc_handling':'EXCLUDED'}
    cfg['tariffs']=[dict(declared_class_kg=cls,units_per_vehicle_day='1',shift_hours=hours,
        rate=dict(amount_vnd=rate,currency='VND',unit=unit),inclusions=deepcopy(inc)) for cls,hours,rate,unit in
        [('7000','10','1234567','PER_SHIFT'),('5000',None,'876543','PER_TRIP')]]
    return cfg


def synthetic_cost_policy(cfg):
    from ops_team.telegram_manual_cost_policy import SCHEMA
    binding=multi_fixture(cfg)
    return {'schema': SCHEMA, 'forecast_binding': binding,
        'forecast_sha256': hashlib.sha256(json.dumps(binding,ensure_ascii=False).encode()).hexdigest(),
        'selectors': {'point_bc': ['V1'], 'point_lc': ['V4'], 'ftl': ['V3'], 'gxt': ['V0', 'V2']},
        'rates': {'point_bc_vnd': '10', 'point_lc_vnd': '20', 'ftl_vnd': '30',
            'ftl_capacity_kg': '100', 'gxt_vehicle_day_vnd': '40', 'warehouse_labor_day_vnd': '50'},
        'wage': {'source_sha256': 'a'*64, 'extraction_sha256': 'b'*64, 'unit': 'VND_PER_GHN_BY_KG',
            'tiers': [{'upper_bound': None, 'inclusive': False, 'formula': {'kind': 'FIXED', 'amount_vnd': '5'}}]},
        'provenance': 'USER_CONFIRMED_IN_CODEX', 'ftl_ceil_confirmed': True, 'package_selectors_complete': True}


def synthetic_cost_policy_v2(cfg):
    from ops_team.telegram_manual_cost_policy import SCHEMA_V2
    p=synthetic_cost_policy(cfg);p['schema']=SCHEMA_V2
    p['selectors']['point_remote']=['V8'];p['rates']['point_remote_vnd']='20'
    p['remote_km_status']='UNAVAILABLE'
    p['ftl_tariffs']=[{'class_kg':'100','rate_vnd':'30','method':'CEIL_WEIGHT'},
                      {'class_kg':'200','rate_vnd':'40','method':'ONE_IF_WITHIN_CAPACITY'},
                      {'class_kg':'300','rate_vnd':'50','method':'ONE_IF_WITHIN_CAPACITY'}]
    return p


def test_cost_policy_v2_accepts_remote_and_distinct_ftl_tariffs(tmp_path):
    from ops_team.telegram_manual_cost_policy import validate_policy
    state,facts,data,source,v,cfg=forecast_fixture(tmp_path)
    try:
        policy=synthetic_cost_policy_v2(cfg)
        assert validate_policy(policy,data,source,facts,123,state.control('paired')) is policy
        for change in ('duplicate','unknown_method','unauthorized_ceil','unknown_km','legacy_mismatch','overlap_selector'):
            p=deepcopy(policy)
            if change=='duplicate':p['ftl_tariffs'][1]['class_kg']='100'
            elif change=='unknown_method':p['ftl_tariffs'][1]['method']='CEIL_UNCONFIRMED'
            elif change=='unauthorized_ceil':p['ftl_tariffs'][1]['method']='CEIL_WEIGHT'
            elif change=='unknown_km':p['remote_km_status']='FREE'
            elif change=='legacy_mismatch':p['ftl_tariffs'][0]['rate_vnd']='999'
            else:p['selectors']['point_remote']=['V4']
            with pytest.raises(ValueError):validate_policy(p,data,source,facts,123,state.control('paired'))
    finally:state.close()


def test_cost_policy_v2_import_is_atomic_and_preserves_binding(tmp_path):
    from ops_team.telegram_vehicle_forecast import ingest_forecast
    from ops_team.telegram_manual_cost_policy import ingest_policy,SCHEMA_V2,FILENAME
    state,facts,data,source,v,cfg=forecast_fixture(tmp_path)
    try:
        policy=synthetic_cost_policy_v2(cfg)
        path,desc=stage_forecast(tmp_path,policy['forecast_binding']);ingest_forecast(path,desc,state,123)
        def stage_policy(item):
            raw=json.dumps(item,ensure_ascii=False).encode();path=(tmp_path/'SYN_V2.dpapi').resolve();save_protected(path,raw)
            return path,{'schema':SCHEMA_V2,'sha256':hashlib.sha256(raw).hexdigest()}
        path,desc=stage_policy(policy);ingest_policy(path,desc,state,123);before=(state.root/FILENAME).read_bytes()
        policy['remote_km_status']='FREE';path,desc=stage_policy(policy)
        with pytest.raises(ValueError):ingest_policy(path,desc,state,123)
        assert (state.root/FILENAME).read_bytes()==before
    finally:state.close()


def test_revenue_overlay_must_rebind_to_new_base_policy(tmp_path):
    from ops_team.telegram_cost_overlay import ingest_overlay
    from ops_team.telegram_manual_cost_policy import ingest_policy,SCHEMA_V2
    state,facts,data,source,v,cfg=forecast_fixture(tmp_path)
    try:
        _,old_overlay=stage_synthetic_cost_overlay(tmp_path,state,cfg)
        path,desc=stage_overlay(tmp_path,old_overlay);ingest_overlay(path,desc,state,123)
        policy=synthetic_cost_policy_v2(cfg);raw=json.dumps(policy,ensure_ascii=False).encode()
        path=(tmp_path/'SYN_REBIND_POLICY.dpapi').resolve();save_protected(path,raw)
        ingest_policy(path,{'schema':SCHEMA_V2,'sha256':hashlib.sha256(raw).hexdigest()},state,123)
        path,desc=stage_overlay(tmp_path,old_overlay)
        with pytest.raises(ValueError):ingest_overlay(path,desc,state,123)
        old_overlay['base_policy_sha256']=hashlib.sha256(raw).hexdigest()
        path,desc=stage_overlay(tmp_path,old_overlay)
        assert ingest_overlay(path,desc,state,123)['state']=='SAVED_MANUAL_ONLY'
    finally:state.close()


def test_remote_base_is_partial_and_added_once_with_handling_overlap(tmp_path):
    from ops_team.supra.manual_cost_report import _calculate
    from ops_team.supra.manual_cost_overlay import calculate_overlay
    state,facts,fixture,source,_,cfg=forecast_fixture(tmp_path)
    try:
        policy=synthetic_cost_policy_v2(cfg)
        order=book('SYN_REMOTE',mode=8);order[6]='STORE_REMOTE'
        data=parsed([order],[event('SYN_REMOTE',pl=REP)])
        route=deepcopy(fixture.vehicle_pair['vehicle_catalog']['routing'][-2])
        data.vehicle_pair['vehicle_catalog']['routing'].append(route)
        data.vehicle_pair['vehicle_catalog_sha256']=digest(data.vehicle_pair['vehicle_catalog'])
        h=deepcopy(data.handling_catalog['routing'][4]);h['rule_id']='H8';h['district']='District 8'
        data.handling_catalog['routing'].append(h);data.handling_catalog_sha256=digest(data.handling_catalog)
        v=vehicle_evidence(data,'2026-10',CUTOFF);scope=vehicle_scope(v,'2026-10-10')
        r=_calculate(data,v,policy,scope,'a'*64,vehicle_cost_status(data,v,'2026-10-10'),{'available':False})
        assert r['remote']['eligible_points']==1 and Decimal(r['remote']['eligible_vnd'])==20
        assert r['remote']['km_extra_vnd'] is None and r['remote']['component_vnd'] is None
        assert r['points']['eligible_vnd'] is None and Decimal(r['dc_included_vnd'])==250
        assert Decimal(r['dc_incremental_vnd'])==0
        _,overlay=stage_synthetic_cost_overlay(tmp_path,state,cfg)
        result=calculate_overlay(data,v,r,policy,overlay,'b'*64)
        assert Decimal(result['known_cost_subtotal'])==70 and Decimal(result['components']['remote_base'])==20
        assert result['total_cost_vnd'] is None and result['gross_profit_vnd'] is None
    finally:state.close()


def stage_synthetic_cost_overlay(tmp_path,state,cfg):
    from ops_team.telegram_vehicle_forecast import ingest_forecast
    from ops_team.telegram_manual_cost_policy import ingest_policy,SCHEMA as BASE_SCHEMA
    from ops_team.telegram_cost_overlay import SCHEMA
    policy=synthetic_cost_policy(cfg)
    path,desc=stage_forecast(tmp_path,policy['forecast_binding']);ingest_forecast(path,desc,state,123)
    base_raw=json.dumps(policy,ensure_ascii=False).encode();path=(tmp_path/'SYN_COST_POLICY.dpapi').resolve();save_protected(path,base_raw)
    ingest_policy(path,{'schema':BASE_SCHEMA,'sha256':hashlib.sha256(base_raw).hexdigest()},state,123)
    prices=[dict(province=p,**{'from':'2026-10-01','to':'2026-10-31'},vnd_per_unit=str(i+2)) for i,p in enumerate(['Province Synthetic','Province Two','Province Three','Province Four'])]
    config=dict(schema=SCHEMA,base_policy_sha256=hashlib.sha256(base_raw).hexdigest(),forecast_sha256=policy['forecast_sha256'],
        upstream_ratio='1',warehouse_ratio='1',revenue=dict(unit='VND_PER_PRODUCT_UNIT',vat='EXCLUDED',prices=prices,price_source_sha256='d'*64),
        provenance=dict(source='USER_CONFIRMED_IN_CODEX',confirmed_date='2026-10-27',evidence_sha256='c'*64))
    return policy,config


def stage_overlay(tmp_path,config):
    raw=json.dumps(config,ensure_ascii=False).encode();path=(tmp_path/'SYN_OVERLAY.dpapi').resolve();save_protected(path,raw)
    return path,{'schema':config['schema'],'sha256':hashlib.sha256(raw).hexdigest()}


def scope_exclusions(config):
    from ops_team.telegram_cost_overlay import SCHEMA_V2
    config=deepcopy(config);config['schema']=SCHEMA_V2
    config['scope_exclusions']={'categories':['WAREHOUSE_RENT','ELECTRICITY_WATER','VEHICLE_WAIT','OTHER_OUT_OF_PACKAGE'],
        'window':{'start':'2026-10-10','end':'2026-10-20'},'source':'USER_CONFIRMED_IN_CODEX',
        'confirmed_date':'2026-10-27','evidence_sha256':'e'*64}
    return config


def synthetic_owner_rules(state,data):
    from ops_team.telegram_rule_layer import SCHEMA
    from ops_team.telegram_manual_cost_policy import FILENAME as BASE
    from ops_team.telegram_vehicle_forecast import FILENAME as FORECAST
    from ops_team.telegram_cost_overlay import FILENAME as OVERLAY
    def sha(path):return hashlib.sha256(load_protected(state.root/path)).hexdigest()
    pair=data.vehicle_pair
    return {'schema':SCHEMA,'base_sha256':sha(BASE),'forecast_sha256':sha(FORECAST),'overlay_sha256':sha(OVERLAY),
        'booking_digest':pair['booking_digest'],'raw_digest':pair['raw_digest'],'vehicle_catalog_sha256':pair['vehicle_catalog_sha256'],
        'handling_catalog_sha256':data.handling_catalog_sha256,'window':{'start':'2026-10-10','end':'2026-10-20'},
        'direct_gxt_two_legs':True,'booked_class_basis':'BOOKED_TARIFF_CLASS',
        'handling_parts':[],'booked_trips':[],'opening_ftl':[],
        'provenance':{'source':'USER_CONFIRMED_IN_CODEX','confirmed_date':'2026-10-27','evidence_sha256':'e'*64}}


def stage_rule_layer(tmp_path,rules):
    from ops_team.telegram_rule_layer import SCHEMA
    raw=json.dumps(rules,ensure_ascii=False).encode();path=(tmp_path/'SYN_RULES.dpapi').resolve();save_protected(path,raw)
    return path,{'schema':SCHEMA,'sha256':hashlib.sha256(raw).hexdigest()}


@pytest.mark.parametrize('mutation',['owner','booking','raw','base','forecast','overlay','catalog','scope','basis','hash','cancel'])
def test_owner_rule_layer_rejects_mismatch_atomically(tmp_path,mutation):
    from ops_team.telegram_rule_layer import ingest_rules,FILENAME
    from ops_team.telegram_cost_overlay import ingest_overlay
    state,facts,data,source,v,cfg=forecast_fixture(tmp_path)
    try:
        _,config=stage_synthetic_cost_overlay(tmp_path,state,cfg);path,desc=stage_overlay(tmp_path,config);ingest_overlay(path,desc,state,123)
        rules=synthetic_owner_rules(state,data);path,desc=stage_rule_layer(tmp_path,rules);ingest_rules(path,desc,state,123)
        before=(state.root/FILENAME).read_bytes()
        if mutation=='owner':state.set_control('paired',dict(chat_id=GROUP_ID,owner_id=999))
        elif mutation in ('booking','raw','base','forecast','overlay','catalog'):
            key={'booking':'booking_digest','raw':'raw_digest','base':'base_sha256','forecast':'forecast_sha256','overlay':'overlay_sha256','catalog':'vehicle_catalog_sha256'}[mutation];rules[key]='f'*64
        elif mutation=='scope':rules['window']['end']='2026-11-01'
        elif mutation=='basis':rules['booked_class_basis']='PHYSICAL_CAPACITY'
        path,desc=stage_rule_layer(tmp_path,rules)
        if mutation=='hash':desc['sha256']='f'*64
        cancel=threading.Event()
        if mutation=='cancel':cancel.set()
        with pytest.raises(ValueError):ingest_rules(path,desc,state,123,cancel=cancel)
        assert (state.root/FILENAME).read_bytes()==before
    finally:state.close()


def test_daily_handling_is_per_ghn_day_no_borrow_and_explicit_parts_only():
    from ops_team.supra.vehicle_cost import vehicle_cost_by_day
    rows=[]
    for dt,do,weight in [('10/10/2026','D1','5'),('10/10/2026','D2','5'),('11/10/2026','D3',''),('12/10/2026','D4','7')]:
        b=book('SYN_GHN',date=dt,do=do);b[8]=weight;rows.append(b)
    data=parsed(rows,[event('SYN_GHN')]);v=vehicle_evidence(data,'2026-10',CUTOFF)
    rules={'handling_parts':[]}
    out=vehicle_cost_by_day(data,v,'2026-10-10 2026-10-12',rules)
    assert out['eligible_transfer_ghn']==2 and out['unresolved_ghn']==1 and Decimal(out['eligible_vnd'])==1200
    assert out['group_evidence'][0]['day']=='2026-10-11' and 'missing_weight' in out['group_evidence'][0]['issues']
    limited=vehicle_cost_by_day(data,v,'2026-10-10 2026-10-11',rules)
    assert Decimal(limited['eligible_vnd'])==500 and limited['unresolved_ghn']==1
    rows[1][8]='6';data=parsed(rows,[event('SYN_GHN')]);v=vehicle_evidence(data,'2026-10',CUTOFF)
    held=vehicle_cost_by_day(data,v,'2026-10-10',rules);assert held['unresolved_ghn']==1
    refs=data.rows.iloc[:2]['_source'].tolist();rules={'handling_parts':[{'order_id':'SYN_GHN','day':'2026-10-10','sources':refs}]}
    accepted=vehicle_cost_by_day(data,v,'2026-10-10',rules)
    assert Decimal(accepted['eligible_vnd'])==1100 and accepted['unresolved_ghn']==0
    repeat=book('SYN_GHN',date='10/10/2026',do='D5');repeat[8]='5'
    data=parsed(rows+[repeat],[event('SYN_GHN')]);v=vehicle_evidence(data,'2026-10',CUTOFF)
    dayrows=data.rows.loc[data.rows.dispatch_date.astype(str).eq('2026-10-10')]
    rules={'handling_parts':[{'order_id':'SYN_GHN','day':'2026-10-10','sources':dayrows['_source'].tolist()}]}
    assert Decimal(vehicle_cost_by_day(data,v,'2026-10-10',rules)['eligible_vnd'])==1100


def test_daily_dc_overlap_counts_each_event_only_once(tmp_path):
    from ops_team.supra.vehicle_cost import vehicle_cost_by_day
    from ops_team.supra.manual_cost_report import _calculate
    state,facts,_,source,_,cfg=forecast_fixture(tmp_path)
    try:
        rows=[book('SYN_SPAN',mode=1,date='10/10/2026',do='D1'),book('SYN_SPAN',mode=1,date='11/10/2026',do='D2')]
        rows[0][8]='5';rows[1][8]='7'
        data=parsed(rows,[event('SYN_SPAN',pl=REP)]);v=vehicle_evidence(data,'2026-10',CUTOFF)
        rules={'handling_parts':[],'booked_trips':[],'opening_ftl':[]};scope=vehicle_scope(v,'2026-10-10 2026-10-11')
        dc=vehicle_cost_by_day(data,v,'2026-10-10 2026-10-11',rules)
        out=_calculate(data,v,synthetic_cost_policy(cfg),scope,'a'*64,dc,{'available':False},rules,'b'*64)
        assert Decimal(dc['eligible_vnd'])==1200 and Decimal(out['dc_included_vnd'])==1200
        assert Decimal(out['dc_incremental_vnd'])==0 and out['points']['counts']['point_bc']==2
        assert out['total_cost_vnd'] is None
    finally:state.close()


def test_booked_class_on_other_route_does_not_create_ftl_mixed_class(tmp_path):
    from ops_team.supra.manual_cost_report import _calculate
    state,facts,_,source,_,cfg=forecast_fixture(tmp_path)
    try:
        ftl=book('SYN_FTL',mode=3);ftl[11]='0.1T'
        other=book('SYN_OTHER',mode=0);other[11]='0.2T'
        data=parsed([ftl,other],[event('SYN_FTL',pl=REP),event('SYN_OTHER')])
        v=vehicle_evidence(data,'2026-10',CUTOFF);scope=vehicle_scope(v,'2026-10-10');dc=vehicle_cost_status(data,v,'2026-10-10')
        old=_calculate(data,v,synthetic_cost_policy(cfg),scope,'a'*64,dc,{'available':False})
        rules={'handling_parts':[],'booked_trips':[],'opening_ftl':[]}
        out=_calculate(data,v,synthetic_cost_policy(cfg),scope,'a'*64,dc,{'available':False},rules,'b'*64)
        assert old['ftl']['eligible_groups']==0 and out['ftl']['eligible_groups']==1
    finally:state.close()


def test_booked_trip_override_counts_only_explicit_group_and_preserves_other_holds(tmp_path):
    from ops_team.telegram_vehicle_forecast import _calculate
    state,facts,data,source,v,cfg=forecast_fixture(tmp_path)
    try:
        cfg=multi_fixture(cfg);scope=vehicle_scope(v,'2026-10-10 2026-10-20')
        baseline=_calculate(data,v,scope,cfg,'a'*64)
        rows=data.rows.loc[data.rows.order_id.isin(['MIXED_A','MIXED_B'])]
        override={'day':'2026-10-12','vehicle':'99D12345','sources':rows['_source'].tolist(),
            'trips':[{'class_kg':'7000','units':1},{'class_kg':'5000','units':1}]}
        new=_calculate(data,v,scope,cfg,'a'*64,{'booked_trips':[override]})
        assert new['held_vehicle_days']==baseline['held_vehicle_days']-1
        assert new['eligible_vehicle_days']==baseline['eligible_vehicle_days']+1
        assert Decimal(new['eligible_pool_vnd'])-Decimal(baseline['eligible_pool_vnd'])==Decimal('2111110')
        assert new['owner_confirmed_booked_trips'] and new['booked_class_basis']=='BOOKED_TARIFF_CLASS'
        wrong=deepcopy(override);wrong['sources']=wrong['sources'][:1]
        assert _calculate(data,v,scope,cfg,'a'*64,{'booked_trips':[wrong]})['held_vehicle_days']==baseline['held_vehicle_days']
    finally:state.close()


def test_booked_override_import_requires_exact_sources_and_classes(tmp_path):
    from ops_team.telegram_rule_layer import ingest_rules
    from ops_team.telegram_cost_overlay import ingest_overlay
    state,facts,data,source,v,cfg=forecast_fixture(tmp_path)
    try:
        _,config=stage_synthetic_cost_overlay(tmp_path,state,cfg);path,desc=stage_overlay(tmp_path,config);ingest_overlay(path,desc,state,123)
        rules=synthetic_owner_rules(state,data)
        rows=data.rows.loc[data.rows.order_id.isin(['MIXED_A','MIXED_B'])]
        rules['booked_trips']=[{'day':'2026-10-12','vehicle':'99D12345','sources':rows['_source'].tolist(),
            'trips':[{'class_kg':'7000','units':1},{'class_kg':'5000','units':1}]}]
        path,desc=stage_rule_layer(tmp_path,rules);assert ingest_rules(path,desc,state,123)['state']=='SAVED_MANUAL_ONLY'
        rules['booked_trips'][0]['sources'].pop();path,desc=stage_rule_layer(tmp_path,rules)
        with pytest.raises(ValueError):ingest_rules(path,desc,state,123)
    finally:state.close()


def test_explicit_handling_and_opening_rules_bind_exact_source_rows(tmp_path,monkeypatch):
    from ops_team.telegram_rule_layer import ingest_rules
    from ops_team.telegram_cost_overlay import ingest_overlay
    first=book('SYN_SPLIT',date='10/10/2026',do='D1');first[8]='5'
    second=book('SYN_SPLIT',date='10/10/2026',do='D2');second[8]='6'
    opening=book('SYN_OPEN',mode=1,date='11/10/2026');opening[8]='9'
    state,facts,data,source,v,cfg=forecast_fixture(tmp_path,custom_books=[first,second,opening],
        custom_raws=[event('SYN_SPLIT'),event('SYN_OPEN',date='2026-10-11',pl='99B12345')])
    try:
        _,config=stage_synthetic_cost_overlay(tmp_path,state,cfg);path,desc=stage_overlay(tmp_path,config);ingest_overlay(path,desc,state,123)
        rules=synthetic_owner_rules(state,data)
        refs=data.rows.loc[data.rows.order_id.eq('SYN_SPLIT'),'_source'].tolist()
        opening_refs=data.rows.loc[data.rows.order_id.eq('SYN_OPEN'),'_source'].tolist()
        raw_ref=f"Raw#{data.vehicle_pair['raw_digest'][:12]} dòng 3"
        rules['handling_parts']=[{'order_id':'SYN_SPLIT','day':'2026-10-10','sources':refs}]
        rules['opening_ftl']=[{'order_id':'SYN_OPEN','day':'2026-10-11','booking_sources':opening_refs,'raw_sources':[raw_ref],
                              'class_kg':'8000','pricing':'ACTUAL_KM_UNPRICED'}]
        path,desc=stage_rule_layer(tmp_path,rules);ingest_rules(path,desc,state,123)
        a=responder(state);monkeypatch.setattr(a,'_scoped',lambda:(data,source,facts,'2026-10',[PIN['tab']],CUTOFF,0))
        monkeypatch.setattr(a,'_get_agent',lambda **kw:pytest.fail('manual rules cannot call model'))
        from ops_team.supra.booking_quantity import quantity_summary
        before_data=data.rows.copy(deep=True);before_qty=quantity_summary(data,data.rows)
        main=a.answer(Incoming(GROUP_ID,123,1,None,'chiphi','2026-10-10 2026-10-20'))
        report=a._code_details[(GROUP_ID,None,123)]['report'];audit=a.answer(Incoming(GROUP_ID,123,2,None,'details','audit'))
        assert report['dc']['grain']=='GHN_BOOKING_E_DAY' and report['opening_unpriced_events']==1
        assert data.rows.equals(before_data) and quantity_summary(data,data.rows)==before_qty
        assert 'khai trương' in main and main in audit and desc['sha256'] in audit
        xe=a.answer(Incoming(GROUP_ID,123,5,None,'xe','2026-10-10 2026-10-20'))
        xe_audit=a.answer(Incoming(GROUP_ID,123,6,None,'details','audit'))
        assert 'đợt GHN/ngày' in xe and 'L/GHN/ngày xuất' in xe_audit and desc['sha256'] in xe_audit
        wrong=deepcopy(rules);wrong['handling_parts'][0]['sources'].pop();path,bad=stage_rule_layer(tmp_path,wrong)
        with pytest.raises(ValueError):ingest_rules(path,bad,state,123)
        wrong=deepcopy(rules);wrong['opening_ftl'][0]['raw_sources']=[f"Raw#{data.vehicle_pair['raw_digest'][:12]} dòng 2"]
        path,bad=stage_rule_layer(tmp_path,wrong)
        with pytest.raises(ValueError):ingest_rules(path,bad,state,123)
        from ops_team.telegram_rule_layer import FILENAME
        stale=deepcopy(rules);stale['booking_digest']='f'*64
        save_protected(state.root/FILENAME,json.dumps(stale,ensure_ascii=False).encode())
        msg=a.answer(Incoming(GROUP_ID,123,3,None,'chiphi','2026-10-10 2026-10-20'))
        assert 'Chưa tính được' in msg and not a._code_details[(GROUP_ID,None,123)]['report']['available']
    finally:state.close()


def test_direct_two_legs_only_unambiguous_gxt_and_opening_ftl_remains_unpriced(tmp_path):
    from ops_team.supra.manual_cost_report import _calculate
    from ops_team.supra.vehicle_cost import vehicle_cost_by_day
    state,facts,_,source,_,cfg=forecast_fixture(tmp_path)
    rules={'opening_ftl':[],'booked_trips':[],'handling_parts':[]}
    try:
        policy=synthetic_cost_policy(cfg)
        data=parsed([book('SYN_DIRECT',mode=2,ag='99A12345')],[event('SYN_DIRECT',pl='99B12345')])
        v=vehicle_evidence(data,'2026-10',CUTOFF);scope=vehicle_scope(v,'2026-10-10')
        old=_calculate(data,v,policy,scope,'a'*64,vehicle_cost_status(data,v,'2026-10-10'),{'available':False})
        revised=_calculate(data,v,policy,scope,'a'*64,vehicle_cost_status(data,v,'2026-10-10'),{'available':False},rules,'b'*64)
        assert old['gxt']['eligible_vehicle_days']==0 and revised['gxt']['eligible_vehicle_days']==1
        assert revised['gxt']['two_leg_upstream_vnd'] is None
        other=book('SYN_DIRECT',mode=2,ag='99A12345',date='09/10/2026',do='D2')
        data=parsed([book('SYN_DIRECT',mode=2),other],[event('SYN_DIRECT',pl='99B12345')])
        v=vehicle_evidence(data,'2026-10',CUTOFF)
        revised=_calculate(data,v,policy,scope,'a'*64,vehicle_cost_status(data,v,'2026-10-10'),{'available':False},rules,'b'*64)
        assert revised['gxt']['eligible_vehicle_days']==0
        bc=book('SYN_OPEN',mode=1);gxt=book('SYN_GXT',mode=0)
        data=parsed([bc,gxt],[event('SYN_OPEN',pl='99B12345'),event('SYN_GXT',pl='99B12345',trip=124)])
        v=vehicle_evidence(data,'2026-10',CUTOFF);before=deepcopy(v)
        rules['opening_ftl']=[{'order_id':'SYN_OPEN','day':'2026-10-10','pricing':'ACTUAL_KM_UNPRICED'}]
        revised=_calculate(data,v,policy,scope,'a'*64,vehicle_cost_by_day(data,v,'2026-10-10',rules),{'available':False},rules,'b'*64)
        assert revised['opening_unpriced_events']==1 and revised['points']['eligible_vnd'] is None
        assert revised['ftl']['eligible_vnd'] is None and revised['dc_overlap_unknown_sources']
        assert revised['gxt']['eligible_vehicle_days']==0 and revised['gxt']['held'] and v==before
    finally:state.close()


def test_two_leg_direct_gxt_uses_same_guard_for_dc_and_last_mile(tmp_path):
    from ops_team.supra.vehicle_cost import vehicle_cost_by_day
    from ops_team.supra.manual_cost_report import _calculate
    state,facts,_,source,_,cfg=forecast_fixture(tmp_path)
    rules={'opening_ftl':[],'booked_trips':[],'handling_parts':[],'direct_gxt_two_legs':True}
    try:
        booking=book('SYN_ROUTE',mode=2,ag='99A12345')
        data=parsed([booking],[event('SYN_ROUTE',pl='99B12345')]);v=vehicle_evidence(data,'2026-10',CUTOFF)
        dc=vehicle_cost_by_day(data,v,'2026-10-10',rules)
        report=_calculate(data,v,synthetic_cost_policy(cfg),vehicle_scope(v,'2026-10-10'),'a'*64,dc,{'available':False},rules,'b'*64)
        assert dc['eligible_transfer_ghn']==1 and Decimal(dc['eligible_vnd'])==250 and dc['direct_excluded_ghn']==0
        assert report['gxt']['eligible_vehicle_days']==1 and report['gxt']['two_leg_upstream_vnd'] is None
        assert Decimal(report['dc_incremental_vnd'])==250 and report['dc_included_vnd']=='0'
        data=parsed([booking],[event('OTHER',pl='99B12345')]);v=vehicle_evidence(data,'2026-10',CUTOFF)
        dc=vehicle_cost_by_day(data,v,'2026-10-10',rules)
        assert dc['unresolved_ghn']==1 and dc['direct_excluded_ghn']==0 and 'two_leg_unresolved' in dc['group_evidence'][0]['issues']
        next_day=book('SYN_ROUTE',mode=2,ag='99A12345',date='11/10/2026',do='D2');next_day[8]=''
        data=parsed([booking,next_day],[event('SYN_ROUTE',pl='99B12345')]);v=vehicle_evidence(data,'2026-10',CUTOFF)
        dc=vehicle_cost_by_day(data,v,'2026-10-10 2026-10-11',rules)
        assert dc['eligible_vnd'] is None and dc['direct_excluded_ghn']==0
        assert any('missing_weight' in x['issues'] and x['day']=='2026-10-11' for x in dc['group_evidence'])
        policy=synthetic_cost_policy(cfg);policy['selectors']['point_bc'].append('V0');policy['selectors']['gxt'].remove('V0')
        data=parsed([booking,book('SYN_BC_PHYSICAL',mode=0)],[event('SYN_ROUTE',pl='99B12345'),event('SYN_BC_PHYSICAL',pl='99B12345',trip=124)])
        v=vehicle_evidence(data,'2026-10',CUTOFF)
        dc=vehicle_cost_by_day(data,v,'2026-10-10',rules,policy)
        report=_calculate(data,v,policy,vehicle_scope(v,'2026-10-10'),'a'*64,dc,{'available':False},rules,'b'*64)
        assert any('two_leg_unresolved' in x['issues'] for x in dc['group_evidence'])
        assert report['gxt']['eligible_vehicle_days']==0
    finally:state.close()


@pytest.mark.parametrize('mutation',['unknown','km','duplicate','scope','proof','schema','cancel'])
def test_exclusion_overlay_rejects_unknown_or_unbound_policy_atomically(tmp_path,mutation):
    from ops_team.telegram_cost_overlay import ingest_overlay,FILENAME,SCHEMA
    state,facts,data,source,v,cfg=forecast_fixture(tmp_path)
    try:
        _,config=stage_synthetic_cost_overlay(tmp_path,state,cfg);config=scope_exclusions(config)
        path,d=stage_overlay(tmp_path,config);ingest_overlay(path,d,state,123);before=(state.root/FILENAME).read_bytes()
        if mutation=='unknown':config['scope_exclusions']['categories']=['UNCONFIRMED']
        elif mutation=='km':config['scope_exclusions']['categories']=['REMOTE_NCC_KM']
        elif mutation=='duplicate':config['scope_exclusions']['categories']=['VEHICLE_WAIT','VEHICLE_WAIT']
        elif mutation=='scope':config['scope_exclusions']['window']['end']='2026-11-01'
        elif mutation=='proof':config['scope_exclusions']['evidence_sha256']='bad'
        path,d=stage_overlay(tmp_path,config)
        if mutation=='schema':d['schema']=SCHEMA
        cancel=threading.Event()
        if mutation=='cancel':cancel.set()
        with pytest.raises(ValueError):ingest_overlay(path,d,state,123,cancel=cancel)
        assert (state.root/FILENAME).read_bytes()==before
    finally:state.close()


@pytest.mark.parametrize('mutation',['owner','base_hash','forecast_hash','ratio','province_overlap','bad_vat','hash','cancel'])
def test_overlay_import_rejects_bad_binding_atomically(tmp_path,mutation):
    from ops_team.telegram_cost_overlay import ingest_overlay,FILENAME
    state,facts,data,source,v,cfg=forecast_fixture(tmp_path)
    try:
        policy,config=stage_synthetic_cost_overlay(tmp_path,state,cfg)
        path,d=stage_overlay(tmp_path,config);ingest_overlay(path,d,state,123);before=(state.root/FILENAME).read_bytes()
        if mutation=='owner':state.set_control('paired',dict(chat_id=GROUP_ID,owner_id=999))
        elif mutation=='base_hash':config['base_policy_sha256']='f'*64
        elif mutation=='forecast_hash':config['forecast_sha256']='f'*64
        elif mutation=='ratio':config['upstream_ratio']='1.1'
        elif mutation=='province_overlap':config['revenue']['prices'][1]['province']=config['revenue']['prices'][0]['province']
        elif mutation=='bad_vat':config['revenue']['vat']='INCLUDED'
        path,d=stage_overlay(tmp_path,config)
        if mutation=='hash':d['sha256']='f'*64
        cancel=threading.Event()
        if mutation=='cancel':cancel.set()
        with pytest.raises(ValueError):ingest_overlay(path,d,state,123,cancel=cancel)
        assert (state.root/FILENAME).read_bytes()==before
    finally:state.close()


@pytest.mark.parametrize(('ratio','upstream','warehouse'), [('0','0','0'),('0.25','25.00','10.00'),('1','100','40')])
def test_overlay_revenue_and_allocations_preserve_incomplete_components(tmp_path,ratio,upstream,warehouse):
    from ops_team.supra.manual_cost_overlay import calculate_overlay
    state,facts,_,source,_,cfg=forecast_fixture(tmp_path)
    try:
        policy,config=stage_synthetic_cost_overlay(tmp_path,state,cfg)
        config['upstream_ratio']=ratio;config['warehouse_ratio']=ratio
        first=book('SYN_ONE',do='D1');second=book('SYN_TWO',do='D2');second[9]='Unknown province'
        bad=book('SYN_BAD',do='D3');bad[7]='bad'
        data=parsed([first,second,bad],[event('SYN_ONE')]);v=vehicle_evidence(data,'2026-10',CUTOFF)
        report={'scope':vehicle_scope(v,'2026-10-10'),'upstream':{'available':True,'eligible_pool_vnd':'100'},
            'warehouse':{'shared_pool_vnd':'40'},'points':{'eligible_vnd':'11'},'ftl':{'eligible_vnd':None},
            'gxt':{'vehicle_supra_vnd':'7','wage_supra_vnd':'3'},'dc_incremental_vnd':'5'}
        out=calculate_overlay(data,v,report,policy,config,'a'*64)
        assert out['allocations']=={'upstream_vnd':upstream,'warehouse_vnd':warehouse}
        assert out['revenue']['eligible_units']==10 and out['revenue']['eligible_vnd']=='20'
        assert not out['revenue']['complete'] and out['revenue']['component_vnd'] is None
        assert len(out['revenue']['unpriced_sources'])==1 and out['revenue']['quarantined_rows']==1
        expected=Decimal('26')+Decimal(upstream)+Decimal(warehouse)
        assert Decimal(out['known_cost_subtotal'])==expected and Decimal(out['revenue_minus_known_costs'])==Decimal('20')-expected
        assert out['components']['ftl'] is None and out['total_cost_vnd'] is None and out['gross_profit_vnd'] is None
    finally:state.close()


def test_overlay_trace_and_missing_plate_gxt_only(tmp_path,monkeypatch):
    from ops_team.telegram_cost_overlay import ingest_overlay
    state,facts,data,source,v,cfg=forecast_fixture(tmp_path)
    try:
        policy,config=stage_synthetic_cost_overlay(tmp_path,state,cfg);path,d=stage_overlay(tmp_path,config);ingest_overlay(path,d,state,123)
        a=responder(state);plan=a._plan_code();state.set_control('approved_plan',plan);state.set_control('schedule_enabled',True)
        monkeypatch.setattr(a,'_scoped',lambda:(data,source,facts,'2026-10',[PIN['tab']],CUTOFF,0))
        monkeypatch.setattr(a,'_get_agent',lambda **kw:pytest.fail('manual calculation must not call a model'))
        main=a.answer(Incoming(GROUP_ID,123,1,None,'chiphi','2026-10-10 2026-10-20'))
        record=a._code_details[(GROUP_ID,None,123)];audit=a.answer(Incoming(GROUP_ID,123,2,None,'details','audit'))
        assert 'Doanh thu dự kiến' in main and 'Chi phí đã tính' in main and 'Chưa là lợi nhuận cuối' in main
        assert main in audit and d['sha256'] in audit and record['report']['overlay']['available']
        from ops_team.supra.manual_cost_report import render_cost_report
        partial=deepcopy(record['report']);partial['overlay']['revenue']['complete']=False
        assert 'Doanh thu dự kiến phần đủ nguồn, chưa đủ toàn phạm vi' in render_cost_report(partial)
        assert state.control('approved_plan')==plan and state.control('schedule_enabled') and a._plan_code()==plan
    finally:state.close()


def test_overlay_date_price_and_cross_order_warning_do_not_drop_valid_quantity(tmp_path):
    from ops_team.supra.manual_cost_overlay import calculate_overlay
    state,facts,_,source,_,cfg=forecast_fixture(tmp_path)
    try:
        policy,config=stage_synthetic_cost_overlay(tmp_path,state,cfg)
        first=book('SYN_ONE',do='D1');second=book('SYN_TWO',do='D1')
        data=parsed([first,second],[event('SYN_ONE')]);v=vehicle_evidence(data,'2026-10',CUTOFF)
        report={'scope':vehicle_scope(v,'2026-10-10'),'upstream':{'available':False},'warehouse':{'shared_pool_vnd':'40'},
            'points':{'eligible_vnd':None},'ftl':{'eligible_vnd':None},'gxt':{'vehicle_supra_vnd':None,'wage_supra_vnd':None},'dc_incremental_vnd':None}
        out=calculate_overlay(data,v,report,policy,config,'a'*64)
        assert out['revenue']['eligible_units']==20 and out['revenue']['eligible_vnd']=='40'
        assert out['revenue']['conflicting_key_rows']==2 and out['revenue']['quarantined_rows']==0
        config['revenue']['prices'][0]['to']='2026-10-09'
        out=calculate_overlay(data,v,report,policy,config,'a'*64)
        assert out['revenue']['eligible_vnd'] is None and len(out['revenue']['unpriced_sources'])==2
    finally:state.close()


def test_missing_plate_classification_does_not_assign_other_scope_to_gxt(tmp_path):
    from ops_team.supra.manual_cost_overlay import calculate_overlay
    state,facts,_,source,_,cfg=forecast_fixture(tmp_path)
    try:
        policy,config=stage_synthetic_cost_overlay(tmp_path,state,cfg)
        data=parsed([book('SYN_GXT',mode=0),book('SYN_FTL',mode=3)],
                    [event('SYN_GXT',pl=''),event('SYN_FTL',pl=''),event('OTHER',pl='')])
        v=vehicle_evidence(data,'2026-10',CUTOFF)
        report={'scope':vehicle_scope(v,'2026-10-10'),'upstream':{'available':False},'warehouse':{'shared_pool_vnd':'40'},
            'points':{'eligible_vnd':None},'ftl':{'eligible_vnd':None},'gxt':{'vehicle_supra_vnd':None,'wage_supra_vnd':None},'dc_incremental_vnd':None}
        out=calculate_overlay(data,v,report,policy,config,'a'*64)
        assert out['missing_s_gxt_rows']==1 and len(out['missing_s_gxt_sources'])==1
    finally:state.close()


@pytest.mark.parametrize('mutation', ['selector', 'duplicate_selector', 'hash', 'rate', 'owner', 'cancel'])
def test_cost_policy_rejects_bad_binding_atomically(tmp_path, mutation):
    from ops_team.telegram_manual_cost_policy import ingest_policy, SCHEMA, FILENAME
    state,facts,data,source,v,cfg=forecast_fixture(tmp_path)
    try:
        policy=synthetic_cost_policy(cfg)
        from ops_team.telegram_vehicle_forecast import ingest_forecast
        forecast_path,forecast_desc=stage_forecast(tmp_path,policy['forecast_binding']);ingest_forecast(forecast_path,forecast_desc,state,123)
        def stage_policy(p):
            raw=json.dumps(p,ensure_ascii=False).encode();path=(tmp_path/'SYN_POLICY.dpapi').resolve();save_protected(path,raw)
            return path,{'schema':SCHEMA,'sha256':hashlib.sha256(raw).hexdigest()}
        path,desc=stage_policy(policy);ingest_policy(path,desc,state,123);before=(state.root/FILENAME).read_bytes()
        if mutation=='selector':policy['selectors']['point_bc']=['V3']
        elif mutation=='duplicate_selector':policy['selectors']['gxt'].append('V1')
        elif mutation=='rate':policy['rates']['ftl_vnd']='-1'
        elif mutation=='owner':policy['forecast_binding']['owner_id']=124
        path,desc=stage_policy(policy)
        if mutation=='hash':desc['sha256']='f'*64
        cancel=threading.Event()
        if mutation=='cancel':cancel.set()
        with pytest.raises(ValueError):ingest_policy(path,desc,state,123,cancel=cancel)
        assert (state.root/FILENAME).read_bytes()==before
    finally:state.close()


def test_manual_cost_command_uses_policy_trace_without_model_or_schedule_changes(tmp_path,monkeypatch):
    from ops_team.telegram_manual_cost_policy import ingest_policy, SCHEMA
    from ops_team.telegram_vehicle_forecast import ingest_forecast
    state,facts,data,source,v,cfg=forecast_fixture(tmp_path)
    try:
        policy=synthetic_cost_policy(cfg)
        path,desc=stage_forecast(tmp_path,policy['forecast_binding']);ingest_forecast(path,desc,state,123)
        raw=json.dumps(policy,ensure_ascii=False).encode();path=(tmp_path/'COST_SYN.dpapi').resolve();save_protected(path,raw)
        desc={'schema':SCHEMA,'sha256':hashlib.sha256(raw).hexdigest()};ingest_policy(path,desc,state,123)
        a=responder(state);plan=a._plan_code();state.set_control('approved_plan',plan);state.set_control('schedule_enabled',True)
        monkeypatch.setattr(a,'_scoped',lambda:(data,source,facts,'2026-10',[PIN['tab']],CUTOFF,0))
        monkeypatch.setattr(a,'_get_agent',lambda **kw:pytest.fail('manual cost must never call a model'))
        main=a.answer(Incoming(GROUP_ID,123,1,None,'chiphi','2026-10-10 2026-10-20'))
        assert 'CHI PHÍ DỰ KIẾN' in main and 'Nhân sự bốc xếp tại kho GXT' in main
        assert 'FTL tải đã chốt' in main and 'Chưa đủ tổng chi phí' in main
        audit=a.answer(Incoming(GROUP_ID,123,2,None,'details','audit'))
        assert main in audit and desc['sha256'] in audit
        assert '99A12345' not in main+audit and 'F01' not in main+audit
        assert state.control('approved_plan')==plan and state.control('schedule_enabled') and a._plan_code()==plan
        assert 'Chỉ owner' in a.answer(Incoming(GROUP_ID,456,3,None,'chiphi',''))
    finally:state.close()


def test_manual_cost_components_keep_scope_grain_and_dc_overlap_separate(tmp_path):
    from ops_team.supra.manual_cost_report import _calculate
    b0=book('GXT',mode=0);b1=book('SYNBC',mode=1);b2=book('SYNLC',mode=4);b3=book('FTL',mode=3,ag='99D12345')
    for i,b in enumerate((b0,b1,b2,b3)):b[6]='STORE_SYN_'+str(i)
    b3[11]='0.1T'
    data=parsed([b0,b1,b2,b3],[event('GXT'),event('OTHER',grams='3750',trip=124)])
    v=vehicle_evidence(data,'2026-10',CUTOFF);scope=vehicle_scope(v,'2026-10-10')
    state,facts,_,source,_,cfg=forecast_fixture(tmp_path)
    try:
        result=_calculate(data,v,synthetic_cost_policy(cfg),scope,'a'*64,
            vehicle_cost_status(data,v,'2026-10-10'),{'available':False,'reason':'NOT_CONFIGURED'})
        assert result['points']['counts']=={'point_bc':1,'point_lc':1} and result['points']['eligible_vnd']=='30'
        assert result['ftl']['eligible_groups']==1 and result['ftl']['eligible_trips']==1 and result['ftl']['eligible_vnd']=='30'
        assert Decimal(result['dc']['eligible_vnd'])==Decimal('750')
        assert Decimal(result['dc_included_vnd'])==Decimal('500') and Decimal(result['dc_incremental_vnd'])==Decimal('250')
        assert Decimal(result['gxt']['vehicle_supra_vnd'])==Decimal('10')
        assert Decimal(result['gxt']['wage_supra_vnd'])==Decimal('5')
        assert result['warehouse']['shared_pool_vnd']=='50' and result['warehouse']['supra_vnd'] is None
        assert result['total_cost_vnd'] is None and result['gross_profit_vnd'] is None
    finally:state.close()


def test_bc_tariff_selector_is_independent_of_physical_vehicle_mode(tmp_path):
    from ops_team.telegram_manual_cost_policy import validate_policy
    state,facts,data,source,v,cfg=forecast_fixture(tmp_path)
    try:
        policy=synthetic_cost_policy(cfg)
        policy['selectors']['point_bc'].append('V0')
        policy['selectors']['gxt'].remove('V0')
        assert validate_policy(policy,data,source,facts,123,state.control('paired')) is policy
        policy['selectors']['gxt'].append('V0')
        with pytest.raises(ValueError):validate_policy(policy,data,source,facts,123,state.control('paired'))
    finally:state.close()


def test_bc_point_does_not_add_a_gxt_vehicle_day(tmp_path):
    from ops_team.supra.manual_cost_report import _calculate
    data=parsed([book('SYN_BC',mode=0)],[event('SYN_BC')])
    v=vehicle_evidence(data,'2026-10',CUTOFF);scope=vehicle_scope(v,'2026-10-10')
    state,facts,_,source,_,cfg=forecast_fixture(tmp_path)
    try:
        policy=synthetic_cost_policy(cfg);policy['selectors']['point_bc'].append('V0');policy['selectors']['gxt'].remove('V0')
        result=_calculate(data,v,policy,scope,'a'*64,vehicle_cost_status(data,v,'2026-10-10'),{'available':False,'reason':'NOT_CONFIGURED'})
        assert result['points']['counts']['point_bc']==1
        assert result['gxt']['eligible_vehicle_days']==0 and not result['gxt']['held']
    finally:state.close()


def test_missing_point_cannot_prove_dc_package_overlap(tmp_path):
    from ops_team.supra.manual_cost_report import _calculate
    b=book('SYN_BC',mode=1);b[6]=''
    data=parsed([b],[event('SYN_BC',pl=REP)])
    v=vehicle_evidence(data,'2026-10',CUTOFF);scope=vehicle_scope(v,'2026-10-10')
    state,facts,_,source,_,cfg=forecast_fixture(tmp_path)
    try:
        result=_calculate(data,v,synthetic_cost_policy(cfg),scope,'a'*64,vehicle_cost_status(data,v,'2026-10-10'),{'available':False,'reason':'NOT_CONFIGURED'})
        assert result['points']['held'] and result['dc_overlap_unknown_sources']
        assert result['dc_incremental_vnd'] is None
    finally:state.close()


def test_dc_unresolved_diagnostic_requires_actual_accepted_package_sources(tmp_path):
    from ops_team.supra.manual_cost_report import _calculate
    state,facts,_,source,_,cfg=forecast_fixture(tmp_path)
    try:
        bc=book('SYN_BC',mode=1);bc[8]='';bc[6]='STORE_BC'
        gxt=book('SYN_GXT',mode=0);gxt[8]='';gxt[6]='STORE_GXT'
        data=parsed([bc,gxt],[event('SYN_BC',pl=REP),event('SYN_GXT')])
        v=vehicle_evidence(data,'2026-10',CUTOFF);scope=vehicle_scope(v,'2026-10-10')
        dc=vehicle_cost_status(data,v,'2026-10-10');before=deepcopy(dc)
        out=_calculate(data,v,synthetic_cost_policy(cfg),scope,'a'*64,dc,{'available':False,'reason':'NOT_CONFIGURED'})
        assert out['dc_scope']['unresolved_ghn']==2 and out['dc_scope']['covered_in_packages']==1
        assert out['dc_scope']['pending_incremental']==1 and out['dc']==before
        bc[6]='';data=parsed([bc,gxt],[event('SYN_BC',pl=REP),event('SYN_GXT')])
        v=vehicle_evidence(data,'2026-10',CUTOFF);scope=vehicle_scope(v,'2026-10-10')
        out=_calculate(data,v,synthetic_cost_policy(cfg),scope,'a'*64,vehicle_cost_status(data,v,'2026-10-10'),{'available':False,'reason':'NOT_CONFIGURED'})
        assert out['dc_scope']['covered_in_packages']==0 and out['dc_scope']['pending_incremental']==2
    finally:state.close()


def test_excluded_scope_changes_only_presentation_and_preserves_ncc_km(tmp_path,monkeypatch):
    from ops_team.telegram_cost_overlay import ingest_overlay
    from ops_team.supra.manual_cost_report import render_cost_report
    state,facts,data,source,v,cfg=forecast_fixture(tmp_path)
    try:
        _,config=stage_synthetic_cost_overlay(tmp_path,state,cfg)
        path,d=stage_overlay(tmp_path,config);ingest_overlay(path,d,state,123)
        a=responder(state);monkeypatch.setattr(a,'_scoped',lambda:(data,source,facts,'2026-10',[PIN['tab']],CUTOFF,0))
        a.answer(Incoming(GROUP_ID,123,1,None,'chiphi','2026-10-10 2026-10-20'))
        before=deepcopy(a._code_details[(GROUP_ID,None,123)]['report']['overlay'])
        config=scope_exclusions(config);path,d=stage_overlay(tmp_path,config);ingest_overlay(path,d,state,123)
        main=a.answer(Incoming(GROUP_ID,123,2,None,'chiphi','2026-10-10 2026-10-20'))
        report=a._code_details[(GROUP_ID,None,123)]['report'];after=report['overlay']
        assert before['known_cost_subtotal']==after['known_cost_subtotal'] and before['revenue_minus_known_costs']==after['revenue_minus_known_costs']
        assert all(x in main for x in ('thuê kho','điện/nước','chờ xe','phụ phí ngoài gói','Không có nghĩa là không phát sinh'))
        detail=render_cost_report(report,details=True);audit=render_cost_report(report,details=True,audit=True,answered_text=main)
        assert 'Phí km NCC vẫn chờ' in detail and main in audit and d['sha256'] in audit and 'e'*64 in audit
        assert report['total_cost_vnd'] is None and report['gross_profit_vnd'] is None
        config['scope_exclusions']['window']['end']='2026-10-15';path,d=stage_overlay(tmp_path,config);ingest_overlay(path,d,state,123)
        a.answer(Incoming(GROUP_ID,123,3,None,'chiphi','2026-10-10 2026-10-20'))
        outside=a._code_details[(GROUP_ID,None,123)]['report']['overlay']
        assert outside['scope_exclusions'] is None and outside['known_cost_subtotal']==after['known_cost_subtotal']
    finally:state.close()


def test_point_day_can_count_while_cross_range_ghn_dc_is_held(tmp_path):
    from ops_team.supra.manual_cost_report import _calculate
    data=parsed([book('SYN_SPAN',mode=1,date='10/10/2026'),book('SYN_SPAN',mode=1,date='11/10/2026')],[event('SYN_SPAN',pl=REP)])
    v=vehicle_evidence(data,'2026-10',CUTOFF);scope=vehicle_scope(v,'2026-10-10')
    state,facts,_,source,_,cfg=forecast_fixture(tmp_path)
    try:
        out=_calculate(data,v,synthetic_cost_policy(cfg),scope,'a'*64,vehicle_cost_status(data,v,'2026-10-10'),{'available':False,'reason':'NOT_CONFIGURED'})
        assert out['points']['counts']['point_bc']==1
        assert out['dc']['boundary_ghn']==1 and out['dc']['component_vnd'] is None
    finally:state.close()


def test_gxt_membership_uses_all_pinned_booking_and_raw_day(tmp_path):
    from ops_team.supra.manual_cost_report import _calculate
    old=book('SYN_OLD',mode=0,date='09/10/2026')
    data=parsed([old],[event('SYN_OLD'),event('OTHER',grams='3750',trip=124)])
    v=vehicle_evidence(data,'2026-10',CUTOFF);scope=vehicle_scope(v,'2026-10-10')
    state,facts,_,source,_,cfg=forecast_fixture(tmp_path)
    try:
        out=_calculate(data,v,synthetic_cost_policy(cfg),scope,'a'*64,
            vehicle_cost_status(data,v,'2026-10-10'),{'available':False,'reason':'NOT_CONFIGURED'})
        assert out['gxt']['eligible_vehicle_days']==1 and Decimal(out['gxt']['vehicle_supra_vnd'])==10
    finally:state.close()


def test_gxt_duplicate_order_on_different_trips_is_held_not_double_wage(tmp_path):
    from ops_team.supra.manual_cost_report import _calculate
    data=parsed([book('SYN_DUP')],[event('SYN_DUP',trip=123),event('SYN_DUP',trip=124)])
    v=vehicle_evidence(data,'2026-10',CUTOFF);scope=vehicle_scope(v,'2026-10-10')
    state,facts,_,source,_,cfg=forecast_fixture(tmp_path)
    try:
        out=_calculate(data,v,synthetic_cost_policy(cfg),scope,'a'*64,
            vehicle_cost_status(data,v,'2026-10-10'),{'available':False,'reason':'NOT_CONFIGURED'})
        assert out['gxt']['eligible_vehicle_days']==0 and len(out['gxt']['held'])==1
        assert out['gxt']['wage_supra_vnd'] is None
    finally:state.close()


def test_gxt_transfer_multiple_upstream_ag_warns_but_direct_mismatch_holds(tmp_path):
    from ops_team.supra.manual_cost_report import _calculate
    state,facts,_,source,_,cfg=forecast_fixture(tmp_path)
    try:
        for mode,expected in ((0,1),(2,0)):
            data=parsed([book('SYN_ORDER',mode=mode,ag='99A12345'),book('SYN_ORDER',mode=mode,ag='99C12345')],
                        [event('SYN_ORDER',pl='99B12345')])
            v=vehicle_evidence(data,'2026-10',CUTOFF);scope=vehicle_scope(v,'2026-10-10')
            out=_calculate(data,v,synthetic_cost_policy(cfg),scope,'a'*64,
                vehicle_cost_status(data,v,'2026-10-10'),{'available':False,'reason':'NOT_CONFIGURED'})
            assert out['gxt']['eligible_vehicle_days']==expected
            if mode==0:assert out['gxt']['upstream_multi_ag_warning_groups']==1
            else:assert out['gxt']['issue_counts']['direct_vehicle_mismatch']==1
    finally:state.close()


def test_ftl_blank_and_invalid_booking_weights_preserve_distinct_status(tmp_path):
    from ops_team.supra.manual_cost_report import _calculate
    state,facts,_,source,_,cfg=forecast_fixture(tmp_path)
    try:
        for value,expected in (('',1),('#REF!',0),('3.5',0),('2.5',1)):
            first=book('SYN_FTL',mode=3);first[11]='0.1T'
            second=book('SYN_FTL',mode=3,do='DO2');second[11]='0.1T';second[8]=value
            data=parsed([first,second],[event('SYN_FTL',pl=REP)])
            v=vehicle_evidence(data,'2026-10',CUTOFF);scope=vehicle_scope(v,'2026-10-10')
            out=_calculate(data,v,synthetic_cost_policy(cfg),scope,'a'*64,
                vehicle_cost_status(data,v,'2026-10-10'),{'available':False,'reason':'NOT_CONFIGURED'})
            assert out['ftl']['eligible_groups']==expected
    finally:state.close()


def test_manual_cost_details_identifies_unpriced_ncc_district_from_catalog(tmp_path):
    from ops_team.supra.manual_cost_report import _calculate,render_cost_report
    data=parsed([book('SYN_NCC',mode=3)],[event('SYN_NCC',pl=REP)])
    v=vehicle_evidence(data,'2026-10',CUTOFF);scope=vehicle_scope(v,'2026-10-10')
    state,facts,_,source,_,cfg=forecast_fixture(tmp_path)
    try:
        policy=synthetic_cost_policy(cfg);policy['selectors']['ftl']=[]
        out=_calculate(data,v,policy,scope,'a'*64,vehicle_cost_status(data,v,'2026-10-10'),{'available':False,'reason':'NOT_CONFIGURED'})
        details=render_cost_report(out,details=True)
        assert 'District 3 (1 GHN)' in details and 'Cần chốt cước NCC' in details
        assert '10/10/2026' in details and 'gross' not in details
    finally:state.close()


def test_multi_tariff_counts_classes_once_and_holds_mixed_once(tmp_path):
    from ops_team.telegram_vehicle_forecast import ingest_forecast,manual_forecast
    state,facts,data,source,v,cfg=forecast_fixture(tmp_path)
    try:
        cfg=multi_fixture(cfg);path,desc=stage_forecast(tmp_path,cfg);ingest_forecast(path,desc,state,123)
        result=manual_forecast(state,data,v,source,facts,123,'2026-10-10 2026-10-20')
        a,b=result['tariff_breakdown']
        assert (a['vehicle_days'],b['vehicle_days'])==(2,1)
        assert (a['amount_vnd'],b['amount_vnd'])==('2469134','876543')
        assert result['eligible_pool_vnd']=='3345677' and result['held_vehicle_days']==5
        assert result['class_audit']['mixed_t_groups']==1 and result['excluded_other_class_days']==0
        assert result['full_pool_vnd'] is None and result['total_cost_vnd'] is None
        main=vehicle_view(v,'2026-10-10 2026-10-20',forecast_record=result)
        assert all(s in main for s in ('7T: 2 xe/ngày','5T: 1 xe/ngày','chuyến/ngày','không gồm VAT','không gồm bốc xếp DC','phát sinh chưa tính'))
        assert '99A12345' not in main
    finally:state.close()


def test_multi_tariff_manual_trace_keeps_exact_answer_and_does_not_change_schedule(tmp_path,monkeypatch):
    from ops_team.telegram_vehicle_forecast import ingest_forecast
    state,facts,data,source,v,cfg=forecast_fixture(tmp_path)
    try:
        a=responder(state);plan=a._plan_code();state.set_control('approved_plan',plan);state.set_control('schedule_enabled',True)
        path,desc=stage_forecast(tmp_path,multi_fixture(cfg));ingest_forecast(path,desc,state,123)
        monkeypatch.setattr(a,'_scoped',lambda:(data,source,facts,'2026-10',[PIN['tab']],CUTOFF,0))
        monkeypatch.setattr(a,'_get_agent',lambda **kw:pytest.fail('manual forecast must not call model'))
        main=a.answer(Incoming(GROUP_ID,123,1,None,'xe','2026-10-10 2026-10-20'))
        trace=a._code_details[(GROUP_ID,None,123)]
        audit=a.answer(Incoming(GROUP_ID,123,2,None,'details','audit'))
        assert main in audit and trace['forecast_record']['scenario_sha256'] in audit
        assert 'Chi tiết các bảng giá' in audit and '876543' in audit
        assert state.control('approved_plan')==plan and state.control('schedule_enabled') and a._plan_code()==plan
    finally:state.close()


def test_multi_tariff_unpriced_class_prevents_complete_pool(tmp_path):
    from ops_team.telegram_vehicle_forecast import ingest_forecast,manual_forecast
    state,facts,data,source,v,cfg=forecast_fixture(tmp_path)
    try:
        cfg=multi_fixture(cfg);cfg['tariffs']=cfg['tariffs'][:1]
        path,desc=stage_forecast(tmp_path,cfg);ingest_forecast(path,desc,state,123)
        result=manual_forecast(state,data,v,source,facts,123,'2026-10-11')
        assert result['excluded_other_class_days']==1 and result['held_vehicle_days']==0
        assert result['full_pool_vnd'] is None and result['total_cost_vnd'] is None
    finally:state.close()


@pytest.mark.parametrize('mutation',['duplicate','bad_unit','trip_hours','bad_inclusion','missing_inclusion','class_basis','schema_mismatch'])
def test_multi_tariff_rejects_invalid_config_without_overwrite(tmp_path,mutation):
    from ops_team.telegram_vehicle_forecast import ingest_forecast,FILENAME
    state,facts,data,source,v,cfg=forecast_fixture(tmp_path)
    try:
        cfg=multi_fixture(cfg);path,desc=stage_forecast(tmp_path,cfg);ingest_forecast(path,desc,state,123)
        before=(state.root/FILENAME).read_bytes()
        if mutation=='duplicate':cfg['tariffs'][1]['declared_class_kg']=cfg['tariffs'][0]['declared_class_kg']
        elif mutation=='bad_unit':cfg['tariffs'][0]['rate']['unit']='PER_KM'
        elif mutation=='trip_hours':cfg['tariffs'][1]['shift_hours']='10'
        elif mutation=='bad_inclusion':cfg['tariffs'][0]['inclusions']['vat']='MAYBE'
        elif mutation=='missing_inclusion':del cfg['tariffs'][0]['inclusions']['dc_handling']
        elif mutation=='class_basis':cfg['vehicle']['class_basis']='UNCONFIRMED'
        path,desc=stage_forecast(tmp_path,cfg)
        if mutation=='schema_mismatch':desc['schema']='supra-shift-forecast-owner-v1'
        with pytest.raises(ValueError):ingest_forecast(path,desc,state,123)
        assert (state.root/FILENAME).read_bytes()==before
    finally:state.close()


def test_manual_forecast_requires_confirmed_basis_deduplicates_and_holds_ambiguous(tmp_path):
    from ops_team.telegram_vehicle_forecast import ingest_forecast,manual_forecast
    state,facts,data,source,v,cfg=forecast_fixture(tmp_path)
    try:
        before=state.facts();path,desc=stage_forecast(tmp_path,cfg)
        assert ingest_forecast(path,desc,state,123)['state']=='SAVED_MANUAL_ONLY'
        result=manual_forecast(state,data,v,source,facts,123,'2026-10-10 2026-10-20')
        assert data.vehicle_pair['vehicle_catalog']['transfer_class_kg']!=cfg['vehicle']['declared_class_kg']
        assert result['eligible_vehicle_days']==2 and result['held_vehicle_days']==5
        assert result['excluded_other_class_days']==1 and result['unassigned_rows']==1
        assert Decimal(result['eligible_pool_vnd'])==Decimal('2469134') and result['full_pool_vnd'] is None
        assert result['total_cost_vnd'] is None and result['gross_profit_vnd'] is None
        assert state.facts()==before and not state.control('schedule_enabled',False)
        assert manual_forecast(state,data,v,source,facts,123,'2026-10-10 2026-10-20 V1')['reason']=='VEHICLE_ALLOCATION_NOT_AVAILABLE'
        assert manual_forecast(state,data,v,source,facts,123,'2026-10-01 2026-10-20')['reason']=='OUTSIDE_APPROVED_WINDOW'
        assert result['class_audit']['mixed_t_groups']==1 and result['class_audit']['missing_or_invalid_t']==1
        assert result['warning_vehicle_days']==3
        first=next(g for g in result['group_evidence'] if g['date']=='2026-10-10')
        assert not first['issues'] and set(first['warnings'])=={'same_vehicle_day_other_route_records','multiple_bookings_one_vehicle_day'}
        issues={issue for group in result['group_evidence'] for issue in group['issues']}
        assert {'unknown_or_mixed_class','multiple_or_missing_vehicles','mixed_or_unknown_route'}<=issues
    finally:state.close()


@pytest.mark.parametrize('mutation',['class_unconfirmed','owner','group','paired_owner','paired_group','booking_pin','raw_pin','catalog','handling','route','window','unit','method','hash','cancel'])
def test_manual_forecast_import_rejects_unapproved_or_unbound_config_atomically(tmp_path,mutation):
    import threading
    from ops_team.telegram_vehicle_forecast import ingest_forecast,FILENAME
    state,facts,data,source,v,cfg=forecast_fixture(tmp_path)
    try:
        path,d=stage_forecast(tmp_path,cfg);ingest_forecast(path,d,state,123);before=(state.root/FILENAME).read_bytes()
        cfg=deepcopy(cfg);cancel=threading.Event()
        if mutation=='class_unconfirmed':cfg['vehicle']['class_basis']='OWNER_CONFIRMED_ROUTE_AG_CLASS'
        elif mutation=='owner':cfg['owner_id']=124
        elif mutation=='group':cfg['group_id']=-123
        elif mutation=='paired_owner':state.set_control('paired',dict(chat_id=GROUP_ID,owner_id=124))
        elif mutation=='paired_group':state.set_control('paired',dict(chat_id=-123,owner_id=123))
        elif mutation=='booking_pin':cfg['booking_pin']['gid']=1
        elif mutation=='raw_pin':cfg['raw_pin']['gid']=1
        elif mutation=='catalog':cfg['vehicle_catalog_sha256']='f'*64
        elif mutation=='handling':cfg['handling_catalog_sha256']='f'*64
        elif mutation=='route':cfg['route_selector']['routes'][0]['district']='OTHER'
        elif mutation=='window':cfg['window']['end']='2026-11-01'
        elif mutation=='unit':cfg['rate']['unit']='PER_DAY'
        elif mutation=='method':cfg['time']['counting']='GUESS_FROM_WEIGHT'
        elif mutation=='cancel':cancel.set()
        path,d=stage_forecast(tmp_path,cfg)
        if mutation=='hash':d['sha256']='f'*64
        with pytest.raises(ValueError):ingest_forecast(path,d,state,123,cancel=cancel)
        assert (state.root/FILENAME).read_bytes()==before
    finally:state.close()


def test_manual_forecast_recomputes_on_configuration_or_source_identity_change(tmp_path):
    from ops_team.telegram_vehicle_forecast import ingest_forecast,manual_forecast
    state,facts,data,source,v,cfg=forecast_fixture(tmp_path)
    try:
        path,d=stage_forecast(tmp_path,cfg);ingest_forecast(path,d,state,123)
        first=manual_forecast(state,data,v,source,facts,123,'2026-10-10 2026-10-20')
        cfg['rate']['amount_vnd']='2345678';cfg['version']='synthetic-v2'
        path,d=stage_forecast(tmp_path,cfg);ingest_forecast(path,d,state,123)
        second=manual_forecast(state,data,v,source,facts,123,'2026-10-10 2026-10-20')
        assert first['scenario_sha256']!=second['scenario_sha256'] and Decimal(second['eligible_pool_vnd'])==Decimal('4691356')
        changed=deepcopy(data);changed.vehicle_pair['booking_digest']='e'*64
        v2=vehicle_evidence(changed,'2026-10',CUTOFF)
        third=manual_forecast(state,changed,v2,source,facts,123,'2026-10-10 2026-10-20')
        assert third['booking_digest']=='e'*64 and third['scenario_sha256']==second['scenario_sha256']
        changed.handling_catalog_sha256='f'*64
        assert manual_forecast(state,changed,v2,source,facts,123,'2026-10-10 2026-10-20')['reason']=='SCENARIO_NOT_VALID_FOR_CURRENT_SOURCE'
    finally:state.close()


def test_confirmed_capacity_invalid_t_is_held_and_never_assumed_target_class(tmp_path):
    from ops_team.telegram_vehicle_forecast import ingest_forecast,manual_forecast
    state,facts,data,source,v,cfg=forecast_fixture(tmp_path)
    try:
        path,d=stage_forecast(tmp_path,cfg);ingest_forecast(path,d,state,123)
        changed=deepcopy(data);changed.rows.loc[changed.rows.order_id.eq('F01'),'source_capacity_raw']='NOT_A_CAPACITY'
        changed.vehicle_pair['booking_digest']='e'*64;v2=vehicle_evidence(changed,'2026-10',CUTOFF)
        result=manual_forecast(state,changed,v2,source,facts,123,'2026-10-10 2026-10-20')
        assert result['eligible_vehicle_days']==1 and result['held_vehicle_days']==6
        assert Decimal(result['eligible_pool_vnd'])==Decimal('1234567') and result['full_pool_vnd'] is None
        assert result['class_audit']['missing_or_invalid_t']==2
    finally:state.close()


def test_undated_booking_cannot_be_assigned_to_range_or_promoted_to_full_pool(tmp_path):
    from ops_team.telegram_vehicle_forecast import ingest_forecast,manual_forecast
    state,facts,data,source,v,cfg=forecast_fixture(tmp_path)
    try:
        path,d=stage_forecast(tmp_path,cfg);ingest_forecast(path,d,state,123)
        changed=deepcopy(data);changed.rows=changed.rows.loc[changed.rows.order_id.isin(['F01','F02'])].copy()
        missing=changed.rows.iloc[[0]].copy();missing.loc[:,'dispatch_date']=None;missing.loc[:,'order_id']='UNDATED'
        changed.rows=pd.concat([changed.rows,missing],ignore_index=True);changed.vehicle_pair['booking_digest']='e'*64
        v2=vehicle_evidence(changed,'2026-10',CUTOFF)
        result=manual_forecast(state,changed,v2,source,facts,123,'2026-10-10 2026-10-20')
        assert result['eligible_vehicle_days']==1 and result['held_vehicle_days']==0 and result['undated_unscoped']
        assert result['full_pool_vnd'] is None and Decimal(result['eligible_pool_vnd'])==Decimal('1234567')
        assert 'thiếu ngày chưa gán kỳ' in vehicle_view(v2,'2026-10-10 2026-10-20',forecast_record=result)
    finally:state.close()


def test_manual_forecast_is_visible_only_in_manual_views_and_exact_trace(tmp_path,monkeypatch):
    from ops_team.telegram_vehicle_forecast import ingest_forecast
    state,facts,data,source,v,cfg=forecast_fixture(tmp_path)
    try:
        a=responder(state);plan=a._plan_code();state.set_control('approved_plan',plan);state.set_control('schedule_enabled',True)
        path,d=stage_forecast(tmp_path,cfg);ingest_forecast(path,d,state,123)
        assert state.control('approved_plan')==plan and state.control('schedule_enabled') and a._plan_code()==plan
        monkeypatch.setattr(a,'_scoped',lambda:(data,source,facts,'2026-10',[PIN['tab']],CUTOFF,0))
        monkeypatch.setattr(a,'_get_agent',lambda **kw:pytest.fail('manual view must not call a model'))
        query='2026-10-10 2026-10-20';main=a.answer(Incoming(GROUP_ID,123,1,None,'xe',query))
        captured=deepcopy(a._code_details[(GROUP_ID,None,123)])
        assert len(main)<=1000 and 'DỰ KIẾN xe 7T dùng chung' in main and '2.469.134,000đ' in main
        assert '2 xe/ngày' in main and '5 nhóm' in main and '1 dòng giữ riêng' in main
        assert 'chưa là ca thực chạy hay phân bổ Supra' in main and 'gói/VAT chưa rõ' in main
        assert captured['forecast_record']['full_pool_vnd'] is None and captured['forecast_record']['total_cost_vnd'] is None
        monkeypatch.setattr(a,'_scoped',lambda:pytest.fail('details must not reload sources'))
        details=a.answer(Incoming(GROUP_ID,123,2,None,'details',''));audit=a.answer(Incoming(GROUP_ID,123,3,None,'details','audit'))
        assert len(details)<=1800 and 'DỰ KIẾN xe 7T dùng chung' in details and 'Bốc xếp theo ngày xuất Booking' in details
        assert main in audit and captured['forecast_record']['scenario_sha256'] in audit
        assert 'Căn cứ áp giá: cột T cho xe AG' in audit and 'không được coi là đã giải quyết' in audit
        assert not any(x in main+details+audit for x in ('99A12345','99D12345','F01','MIXED_A'))
        assert state.control('approved_plan')==plan and state.control('schedule_enabled')
    finally:state.close()


def test_vehicle_details_actions_give_copyable_request_and_supported_handoff():
    v=presentation_evidence();original=deepcopy(v)
    action=vehicle_view(v,details=True,source_pin=RAW_PIN)
    url=f"https://docs.google.com/spreadsheets/d/{RAW_PIN['spreadsheet_id']}/edit#gid={RAW_PIN['gid']}"
    assert len(action)<=1800 and '10/10/2026–11/10/2026' in action and '12/10–14/10' not in action
    assert all(s in action for s in ('DỰ KIẾN xe','DỰ KIẾN giao','lương/kho/phát sinh','Tổng dự kiến/lãi lỗ',
        'bảng giá cũ','không điền 0','Dự kiến không là thực chi','Gửi Codex kiểm tra','Telegram chưa nhập riêng'))
    audit=vehicle_view(v,details=True,audit=True,source_pin=RAW_PIN)
    assert all(s in audit for s in ('load_date(B)','trip_code(E)','order_code(F)','weight(J, gram)','type(P)','truck_plate_number(S)'))
    assert url in audit.splitlines() and '/import raw' not in action and '](' not in action
    assert 'range=S' not in action
    assert v['raw_digest'] not in action and 'Dự kiến không là thực chi' in action
    assert v==original


def test_vehicle_row_links_require_pin_digest_freshness_and_actual_raw_reference():
    v=evidence([book(mode=3)],[event(),event(pl='99C12345',trip=124)])
    url=f"https://docs.google.com/spreadsheets/d/{RAW_PIN['spreadsheet_id']}/edit#gid={RAW_PIN['gid']}"
    text=vehicle_view(v,details=True,source_pin=RAW_PIN)
    assert len(text)<=1800 and url in vehicle_view(v,details=True,audit=True,source_pin=RAW_PIN).splitlines()
    assert sum('&range=S' in line for line in text.splitlines())<=2
    if '&range=S' in text:assert 'nếu Sheet đã đổi, đối chiếu đúng chuyến trước' in text
    assert not any(s in text for s in ('SYN_PRIVATE_ORDER','99A12345','99B12345','99C12345','range=AG'))
    for key in RAW_PIN:
        wrong={**RAW_PIN,key:'WRONG_PIN'}
        assert 'https://' not in vehicle_view(v,details=True,source_pin=wrong)
    malformed=deepcopy(v)
    malformed['mappings'][0]['source']='Raw#'+'e'*12+' dòng 2'
    malformed['mappings'][1]['source']='Booking AG dòng 3'
    fallback=vehicle_view(malformed,details=True,source_pin=RAW_PIN)
    assert 'range=S' not in fallback
    assert url in vehicle_view(malformed,details=True,audit=True,source_pin=RAW_PIN).splitlines()
    alias=v['mappings'][0]['raw_vehicle_alias']
    scoped=vehicle_view(v,'2026-10-10 '+alias,details=True,source_pin=RAW_PIN)
    assert url+'&range=S3' not in scoped


def test_vehicle_details_action_audit_branch_preserves_answer_trace_and_owner_guard(tmp_path,monkeypatch):
    from ops_team.telegram_agent import TelegramAgent
    state=BusinessState(tmp_path/'vault');facts=pins(state)
    try:
        p=payload([book(mode=3)],[event(),event(pl='99C12345',trip=124)])
        path,d=stage(tmp_path,p);ingest_minimized_snapshot(path,d,state,123)
        a=responder(state)
        monkeypatch.setattr(a,'_get_agent',lambda **kw:pytest.fail('details must never call model'))
        main=a.answer(Incoming(GROUP_ID,123,1,None,'xe','2026-10-10'))
        key=(GROUP_ID,None,123);captured=deepcopy(a._code_details[key]);before=state.facts()
        assert captured['response']==main and captured['source_pin']==RAW_PIN
        monkeypatch.setattr(a,'_load',lambda:pytest.fail('details must use the captured trace, not reload source'))
        action=a.answer(Incoming(GROUP_ID,123,2,None,'details',''))
        audit=a.answer(Incoming(GROUP_ID,123,3,None,'details','audit'))
        assert len(action)<=1800 and 'DỰ KIẾN xe' in action
        assert captured['evidence']['raw_digest'] not in action and captured['evidence']['raw_digest'] in audit
        assert main in audit and 'expected_representative_mismatch' in audit
        assert a._code_details[key]==captured and state.facts()==before
        for mode in ('','audit'):
            assert 'Chỉ chủ sở hữu' in a.answer(Incoming(GROUP_ID,456,4,None,'details',mode))
        a._code_details[(GROUP_ID,42,123)]={'kind':'OTHER_CODE_TRACE'}
        monkeypatch.setattr(TelegramAgent,'detail_text',staticmethod(lambda record:'UNCHANGED_OTHER_DETAILS'))
        assert a.answer(Incoming(GROUP_ID,123,5,42,'details','audit'))=='UNCHANGED_OTHER_DETAILS'
        a.answer(Incoming(GROUP_ID,123,6,None,'reset',''))
        assert 'Em chưa giữ chi tiết' in a.answer(Incoming(GROUP_ID,123,7,None,'details','audit'))
    finally:state.close()


def test_fleet_agent_same_pair_changed_cutoff_rebuilds_context(tmp_path,monkeypatch):
    from dataclasses import asdict
    from ops_team.providers import ModelConfig,FREE_MODELS
    from ops_team.schema import AGENT_NAMES
    state=BusinessState(tmp_path/'vault');facts=pins(state)
    try:
        data=parsed([book(),book('NEW',date='20/10/2026',do='DO2')],[event(),event('NEW',date='2026-10-20',trip=456)])
        a=responder(state)
        a.settings['models']={n:asdict(ModelConfig('Groq',FREE_MODELS[0],'FAKE_VEHICLE_KEY',free_pilot=True,free_account_confirmed=True)) for n in AGENT_NAMES}
        cutoff=[datetime(2026,10,11,23,59,tzinfo=TZ)]
        monkeypatch.setattr(a,'_scoped',lambda:(data,dict(tab=PIN['tab'],digest='b'*64),facts,'2026-10',[PIN['tab']],cutoff[0],0))
        first=a._get_agent(fleet=True);assert first._context['metrics']['vehicle.groups']['value']==1
        key=first._context['cache_identity'];cutoff[0]=datetime(2026,10,21,23,59,tzinfo=TZ)
        second=a._get_agent(fleet=True)
        assert second is not first and second._context['metrics']['vehicle.groups']['value']==2
        assert second._context['cache_identity'][:3]==key[:3] and second._context['cache_identity']!=key
        assert a._get_agent(fleet=True) is second
        a.clear();assert a._vehicle_cache is None and a._vehicle_cache_key is None
    finally:state.close()


def test_fleet_actual_provider_adapter_caps_history_and_aggregate_privacy(monkeypatch):
    from types import SimpleNamespace
    import ops_team.providers as providers
    import ops_team.telegram_agent as telegram
    from ops_team.telegram_agent import TelegramAgent
    from ops_team.providers import ModelConfig,FREE_MODELS
    from ops_team.schema import AGENT_NAMES
    from ops_team.limits import estimate_tokens
    ctx=fleet_context(evidence());tick=[1000.0];bodies=[];reservations=[]
    clock=SimpleNamespace(monotonic=lambda:tick[0]);monkeypatch.setattr(providers,'time',clock);monkeypatch.setattr(telegram,'time',clock)
    async def fetch(url,headers,body,timeout):
        assert estimate_tokens(body)<=7500 and body['max_completion_tokens']==1500
        p=json.loads(body['messages'][0]['content']);bodies.append(p)
        assert p['source_provenance']==ctx['source_provenance'] and set(p['metrics'])==set(ctx['metrics'])
        encoded=json.dumps(p)
        assert not any(s in encoded for s in (REP,'99A12345','99B12345','SYN_PRIVATE_ORDER','Province Synthetic'))
        assert p['agent']=='Fleet' and len(p['conversation'])<=3
        answer=dict(summary='Tỷ lệ theo bản ghi nguồn cần đối chiếu trước phân bổ.',observations=[dict(text='Nguồn chưa xác minh tải thực tế.',evidence_ids=['vehicle.groups'])],
            hypotheses=[],proposals=['Kiểm tra phạm vi dữ liệu Raw.'],requests=[dict(to='Operations',question='Anh có mẫu số của AG không?',evidence_ids=[])],
            disagreements=[],selected_agents=[],audit_decisions=[])
        return 200,json.dumps(dict(model='SYNTHETIC',choices=[dict(finish_reason='stop',message=dict(content=json.dumps(answer)))])).encode(),None
    monkeypatch.setattr(providers,'_fetch_async',fetch)
    configs={n:ModelConfig('Groq',FREE_MODELS[0],'FAKE_VEHICLE_KEY',free_pilot=True,free_account_confirmed=True) for n in AGENT_NAMES}
    agent=TelegramAgent(configs,ctx);agent._budget.clock=clock.monotonic
    agent._budget.sleep=lambda seconds:tick.__setitem__(0,tick[0]+seconds)
    acquire=agent._budget.acquire
    def reserve(tokens,deadline):reservations.append(tokens);return acquire(tokens,deadline)
    agent._budget.acquire=reserve
    for i in range(4):
        text=agent.answer(Incoming(GROUP_ID,123,i,None,'fleet','Đối chiếu xe theo nguồn này.'))
        assert 'Tỷ lệ' in text and ctx['source_provenance']['raw_retrieved_at'] in text
    assert len(bodies)==4 and len(bodies[3]['conversation'])==3 and max(reservations)<=7500
    assert agent._budget.attempts==4


def _rule_layer_fixture(tmp_path,window):
    from ops_team.telegram_rule_layer import ingest_rules
    from ops_team.telegram_cost_overlay import ingest_overlay
    first=book('SYN_SPAN',date='10/10/2026',do='D1');first[8]='5'
    second=book('SYN_SPAN',date='11/10/2026',do='D2');second[8]='7'
    state,facts,data,source,v,cfg=forecast_fixture(tmp_path,custom_books=[first,second],
        custom_raws=[event('SYN_SPAN'),event('SYN_SPAN',date='2026-10-11',trip=124)])
    cfg['window']=dict(window)
    _,config=stage_synthetic_cost_overlay(tmp_path,state,cfg);path,desc=stage_overlay(tmp_path,config);ingest_overlay(path,desc,state,123)
    rules=synthetic_owner_rules(state,data);rules['window']=dict(window)
    path,desc=stage_rule_layer(tmp_path,rules);ingest_rules(path,desc,state,123)
    return state,facts,data,source,rules,desc['sha256']


def test_scheduled_report_and_model_context_use_owner_rule_layer_like_chiphi(tmp_path,monkeypatch):
    from ops_team.telegram_rule_layer import FILENAME
    state,facts,data,source,rules,sha=_rule_layer_fixture(tmp_path,{'start':'2026-10-01','end':'2026-10-31'})
    try:
        legacy=booking_context(data,'2026-10',[PIN['tab']],CUTOFF,source)
        assert legacy['metrics']['booking.dc_handling_eligible_vnd']['value'] is None  # GHN-wide grain sees two L totals
        a=responder(state);monkeypatch.setattr(a,'_scoped',lambda:(data,source,facts,'2026-10',[PIN['tab']],CUTOFF,0))
        monkeypatch.setattr(a,'_get_agent',lambda **kw:pytest.fail('code paths cannot call model'))
        context=a._booking_context(data,'2026-10',[PIN['tab']],CUTOFF,source)
        m=context['metrics']
        assert Decimal(m['booking.dc_handling_eligible_vnd']['value'])==1200 and m['booking.dc_handling_eligible_transfer_ghn']['value']==2
        assert m['booking.dc_handling_eligible_transfer_ghn']['unit']=='đợt GHN/ngày'
        assert context['handling_quality']['grain']=='GHN_BOOKING_E_DAY' and context['source_provenance']['owner_rules_sha256']==sha
        assert 'eligible_events' not in context['handling_quality']  # no raw GHN list in model-facing quality record
        a.answer(Incoming(GROUP_ID,123,1,None,'chiphi','2026-10-01 2026-10-31'))
        report=a._code_details[(GROUP_ID,None,123)]['report']
        assert Decimal(report['dc']['eligible_vnd'])==Decimal(m['booking.dc_handling_eligible_vnd']['value'])
        text=a._booking_report(data,source,facts,'2026-10',[PIN['tab']],CUTOFF)
        assert '1.200,000' in text and 'đợt GHN/ngày' in text
        stale=deepcopy(rules);stale['booking_digest']='f'*64
        save_protected(state.root/FILENAME,json.dumps(stale,ensure_ascii=False).encode())
        held=a._booking_context(data,'2026-10',[PIN['tab']],CUTOFF,source)
        assert held['handling_quality']['reason']=='OWNER_RULES_INVALID_OR_STALE'
        assert all(held['metrics'][k]['value'] is None for k in ('booking.dc_handling_component_vnd','booking.dc_handling_eligible_vnd'))
        assert 'không còn khớp' in a._booking_report(data,source,facts,'2026-10',[PIN['tab']],CUTOFF)
        (state.root/FILENAME).unlink()
        assert a._booking_context(data,'2026-10',[PIN['tab']],CUTOFF,source)['metrics']==legacy['metrics']
    finally:state.close()


def test_rule_window_shorter_than_report_scope_holds_handling_na(tmp_path,monkeypatch):
    state,facts,data,source,rules,sha=_rule_layer_fixture(tmp_path,{'start':'2026-10-10','end':'2026-10-20'})
    try:
        a=responder(state);monkeypatch.setattr(a,'_scoped',lambda:(data,source,facts,'2026-10',[PIN['tab']],CUTOFF,0))
        context=a._booking_context(data,'2026-10',[PIN['tab']],CUTOFF,source)
        assert context['handling_quality']['reason']=='OWNER_RULE_WINDOW_NOT_COVERING_SCOPE'
        assert context['metrics']['booking.dc_handling_eligible_vnd']['value'] is None
        assert context['metrics']['booking.dc_handling_unresolved_ghn']['value'] is None
        text=a._booking_report(data,source,facts,'2026-10',[PIN['tab']],CUTOFF)
        assert '2026-10-10 đến 2026-10-20' in text and 'N/A' in text
    finally:state.close()


def test_missing_checklist_names_field_day_and_sheet_rows_without_ids(tmp_path,monkeypatch):
    from ops_team.telegram_cost_overlay import ingest_overlay
    state,facts,data,source,v,cfg=forecast_fixture(tmp_path)
    try:
        _,config=stage_synthetic_cost_overlay(tmp_path,state,cfg);path,desc=stage_overlay(tmp_path,config);ingest_overlay(path,desc,state,123)
        a=responder(state);monkeypatch.setattr(a,'_scoped',lambda:(data,source,facts,'2026-10',[PIN['tab']],CUTOFF,0))
        monkeypatch.setattr(a,'_get_agent',lambda **kw:pytest.fail('checklist cannot call model'))
        main=a.answer(Incoming(GROUP_ID,123,1,None,'chiphi','2026-10-10 2026-10-20'))
        assert main.startswith('Kết luận: mới tính được phần đủ nguồn') and '/thieu' in main.split('\n')[0]
        text=a.answer(Incoming(GROUP_ID,123,2,None,'thieu','2026-10-10 2026-10-20'))
        assert text.startswith('CẦN ANH BỔ SUNG — 10/10/2026–20/10/2026')
        assert 'Loại tải Booking (cột T) chưa có giá FTL' in text and 'Booking dòng 4' in text
        assert 'Một đợt có cả dòng tính bốc xếp và dòng đi thẳng — Booking dòng 11, 12' in text
        assert 'không tính là 0' in text
        assert not any(x in text for x in ('MIXED_ROUTE','OTHER_CLASS','GoogleViewer','99D12345','dddddddddddd'))
        assert 'Chỉ owner' in a.answer(Incoming(GROUP_ID,456,3,None,'thieu','2026-10-10 2026-10-20'))
        assert 'ngoài chính sách chi phí' in a.answer(Incoming(GROUP_ID,123,4,None,'thieu','2026-10-01 2026-10-31'))
    finally:state.close()


def test_missing_checklist_booking_only_fallback_lists_weight_rows(tmp_path,monkeypatch):
    first=book('SYN_SPAN',date='10/10/2026',do='D1');first[8]='5'
    second=book('SYN_SPAN',date='10/10/2026',do='D2');second[8]='7'
    state,facts,data,source,v,cfg=forecast_fixture(tmp_path,custom_books=[first,second],custom_raws=[event('SYN_SPAN')])
    try:
        a=responder(state);monkeypatch.setattr(a,'_scoped',lambda:(data,source,facts,'2026-10',[PIN['tab']],CUTOFF,0))
        text=a.answer(Incoming(GROUP_ID,123,1,None,'thieu',''))
        assert text.startswith('CẦN ANH BỔ SUNG') and 'Kg cột L thiếu, lỗi hoặc mâu thuẫn — Booking dòng 2, 3' in text
        assert 'SYN_SPAN' not in text
    finally:state.close()
