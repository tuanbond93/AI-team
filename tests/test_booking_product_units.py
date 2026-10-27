"""Quantity grain, restart and migration acceptance. Synthetic data, no network."""
from copy import deepcopy
from datetime import datetime, timezone
import csv
import hashlib
import json
import threading
from io import StringIO
import pandas as pd
import pytest
from ops_team.supra.booking import BOOKING_HEADERS, BookingData, booking_context, parse_booking
from ops_team.supra.booking_quantity import BUSINESS_HEADERS, RULE, SHEET_ID, product_units
from ops_team.telegram_snapshot import ingest_minimized_snapshot, ingest_snapshot, ingest_csv_file, load_snapshot
from ops_team.telegram_secrets import load_protected, save_protected
from ops_team.telegram_state import BusinessState, GROUP_ID
from ops_team.telegram_assistant import AssistantResponder, TZ
from ops_team.telegram_bridge import Incoming
from ops_team.chief.report import booking_review, render
from ops_team.chief.agent import finance_available, metric_context

AREA="'DC PHÚ THỌ'!A1:AK100000"
CUTOFF=datetime(2026,10,20,23,59,tzinfo=TZ)


def rows():
    return [
      ['01/10/2026','SYN001','delivered','01/10/2026','SO1','DO1','10','Phú Thọ'],
      ['02/10/2026','SYN001','delivered','02/10/2026','SO1','DO2','20','Phú Thọ'],
      ['15/10/2026','SYN002','delivering','15/10/2026','SO2','DO3','7','Sơn La'],
      ['31/08/2026','SYN002','delivering','31/08/2026','SO2','DO4','3','Sơn La'],
      ['03/10/2026','SYN003','delivered','03/10/2026','SO3','DO5','0','Điện Biên'],
    ]


def parsed(values=None):
    f=pd.DataFrame(values if values is not None else rows(),columns=BUSINESS_HEADERS)
    f['_source']=[f'SYNTHETIC / row {i}' for i in range(2,len(f)+2)]
    return parse_booking(f,quantity_rules=deepcopy(RULE))


def context(data=None,cutoff=CUTOFF):
    return booking_context(data or parsed(),'2026-10',['DC PHÚ THỌ'],cutoff,
        dict(tab='DC PHÚ THỌ',digest='a'*64))


def pins(state):
    facts=dict(sheets_id=SHEET_ID,sheets_tab='DC PHÚ THỌ',sheets_gid='0',
        sheets_ranges=json.dumps({'supra_booking':AREA}),source_id='browser_snapshot',period='2026-10',
        warehouses='DC PHÚ THỌ',cutoff='23:59',report_time='13:00',timezone='Asia/Ho_Chi_Minh')
    for k,v in facts.items():state.remember(k,v,123,0,GROUP_ID)
    return facts


def staging(tmp_path,values=None):
    source=dict(schema='supra-product-source-intake-v1',source_url=f'https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit#gid=0',
        spreadsheet_id=SHEET_ID,tab='DC PHÚ THỌ',gid=0,source='AUTHORIZED_BROWSER_VIEWER_CSV_EXPORT',
        retrieved_at=datetime.now(timezone.utc).isoformat(),raw_sha256='c'*64,raw_column_count=37,
        headers=BUSINESS_HEADERS,rows=[{'sheet_row':i,'values':v} for i,v in enumerate(values or rows(),2)],
        confirmed_business_rules={k:v for k,v in RULE.items() if k!='version'})
    raw=json.dumps(source,ensure_ascii=False).encode()
    path=tmp_path/'synthetic-minimized.dpapi';save_protected(path,raw)
    descriptor={k:v for k,v in source.items() if k not in ('rows','source','spreadsheet_id')}
    descriptor.update(row_count=len(source['rows']),minimized_plaintext_sha256=hashlib.sha256(raw).hexdigest())
    return path.resolve(),descriptor


@pytest.mark.parametrize('value,expected', [('10',(10,'VALID')),(0,(0,'VALID')),(12.0,(12,'VALID')),
    ('',(None,'MISSING')),(None,(None,'MISSING')),('#N/A',(None,'INVALID')),
    ('1,000',(None,'INVALID')),('1.5',(None,'INVALID')),(-1,(None,'INVALID')),
    (True,(None,'INVALID')),(float('inf'),(None,'INVALID'))])
def test_quantity_integer_contract(value,expected):
    assert product_units(value)==expected


def test_distinct_do_partial_shipments_scoped_by_each_dispatch_date_no_conversion():
    ctx=context()
    assert ctx['metrics']['booking.product_units']['value']==37
    assert ctx['metrics']['booking.eligible_product_units']['value']==37
    assert ctx['metrics']['booking.duplicate_rows']['value']==1
    assert ctx['quantity_quality']['complete']
    result=booking_review(ctx)
    assert result['totals']['product_units']==37
    assert all(result['totals'][k] is None for k in ('pieces','cartons','kg','revenue','totalCost','gp'))
    assert not finance_available(result)
    text=render(result,telegram=True)
    assert '37 sản phẩm' in text and 'Tổng doanh thu B2B: N/A' in text
    assert metric_context(result)['chief.product_units']['unit']=='sản phẩm'
    assert context(cutoff=datetime(2026,10,1,23,59,tzinfo=TZ))['metrics']['booking.product_units']['value']==10


@pytest.mark.parametrize('mutation,metric',[
    ('blank','missing_quantity_rows'),('bad','invalid_quantity_rows'),('key','missing_key_rows'),
    ('repeat','repeated_key_rows'),('date_conflict','repeated_key_rows'),('quantity_conflict','repeated_key_rows'),
])
def test_missing_invalid_and_repeated_full_keys_hold_whole_ghn_not_zero_or_dedup(mutation,metric):
    values=rows()
    if mutation in ('blank','bad'):values[0][6]='' if mutation=='blank' else '#N/A'
    elif mutation=='key':values[0][4]=''
    else:
        duplicate=deepcopy(values[0])
        if mutation=='date_conflict':duplicate[3]='18/10/2026'
        if mutation=='quantity_conflict':duplicate[6]='40'
        values.append(duplicate)
    ctx=context(parsed(values)); q=ctx['quantity_quality']
    assert ctx['metrics']['booking.product_units']['value'] is None
    assert ctx['metrics']['booking.eligible_product_units']['value']==7
    assert q[metric]>0 and q['quarantined_rows']>=2
    assert len(parsed(values).rows)==len(values)
    assert 'PRODUCT_UNITS_PARTIAL' in {i['code'] for i in booking_review(ctx)['issues']}


def test_same_so_do_across_ghn_warns_without_new_owner_rule_or_dropping_rows():
    values=rows();values[2][4:6]=['SO1','DO1']
    ctx=context(parsed(values))
    assert ctx['metrics']['booking.product_units']['value']==37
    assert ctx['quantity_quality']['conflicting_key_rows']==2
    assert ctx['quantity_quality']['quarantined_rows']==0
    assert 'DO_MULTIPLE_GHN' in {i['code'] for i in booking_review(ctx)['issues']}


def test_legacy_four_field_and_unconfirmed_mapping_are_na():
    raw=parsed().rows[['dispatch_date','ltc_date','order_id','status','_source']]
    for data in (BookingData(raw),BookingData(parsed().rows)):
        ctx=context(data)
        assert ctx['metrics']['booking.product_units']['value'] is None
        assert not ctx['quantity_quality']['available']


def test_minimized_import_restart_preserves_business_evidence_source_and_13h_plan(tmp_path,monkeypatch):
    path,descriptor=staging(tmp_path)
    state=BusinessState(tmp_path/'vault');facts=pins(state)
    config=dict(owner_id=123,bot_token='SYNTHETIC_NOT_USED',models={})
    responder=AssistantResponder(state,config)
    plan_code=responder._plan_code()
    state.set_control('approved_plan',plan_code);state.set_control('schedule_enabled',True)
    oldfacts=state.facts()
    result=ingest_minimized_snapshot(path,descriptor,state,123)
    assert result['state']=='INGESTED_NOT_SCHEDULED'
    assert state.facts()==oldfacts and state.control('approved_plan')==plan_code
    assert state.control('schedule_enabled') is True and responder._plan_code()==plan_code
    record=json.loads(load_protected(state.root/'snapshots'/(result['digest']+'.dpapi')))
    assert record['business_evidence']['rows'][0]['sheet_row']==2
    assert record['business_evidence']['rows'][0]['values']==rows()[0]
    assert record['quantity_rules']==RULE
    state.close()
    state=BusinessState(tmp_path/'vault')
    try:
        data,source=load_snapshot(state,facts)
        assert data.quantity_rules==RULE and data.rows.source_product_units.tolist()==[v[6] for v in rows()]
        assert source['digest']==result['digest']
        assert context(data)['metrics']['booking.product_units']['value']==37
        responder=AssistantResponder(state,config)
        monkeypatch.setattr(responder,'_get_agent',lambda:pytest.fail('quantity intake must use zero provider HTTP'))
        for command in ('chief','run'):
            msg=responder.answer(Incoming(GROUP_ID,123,9,None,command,''))
            assert '37 sản phẩm' in msg and 'Tổng doanh thu B2B: N/A' in msg
            assert '0 HTTP' in msg
        assert '37 sản phẩm' in responder.report()
        assert state.facts()==oldfacts and state.control('approved_plan')==plan_code
    finally:state.close()


@pytest.mark.parametrize('mutation',['hash','gid','mapping','row','cancel','stale'])
def test_minimized_rejection_never_replaces_pointer_or_plan(tmp_path,mutation):
    path,descriptor=staging(tmp_path)
    state=BusinessState(tmp_path/'vault');pins(state)
    try:
        ingest_minimized_snapshot(path,descriptor,state,123)
        pointer=(state.root/'snapshot-latest.dpapi').read_bytes()
        state.set_control('approved_plan','KEEP_PLAN');state.set_control('schedule_enabled',True)
        event=threading.Event()
        if mutation=='hash':descriptor['minimized_plaintext_sha256']='0'*64
        elif mutation=='cancel':event.set()
        elif mutation=='stale':
            previous=json.loads(load_protected(state.root/'snapshot-latest.dpapi'))
            previous['retrieved_epoch']+=100
            save_protected(state.root/'snapshot-latest.dpapi',json.dumps(previous).encode())
            pointer=(state.root/'snapshot-latest.dpapi').read_bytes()
        else:
            payload=json.loads(load_protected(path))
            if mutation=='gid':payload['gid']=1;descriptor['gid']=1
            elif mutation=='mapping':payload['confirmed_business_rules']['product_units_column']='K'
            else:payload['rows'][0]['sheet_row']=1
            raw=json.dumps(payload).encode();save_protected(path,raw)
            descriptor['minimized_plaintext_sha256']=hashlib.sha256(raw).hexdigest()
        with pytest.raises(ValueError):ingest_minimized_snapshot(path,descriptor,state,123,cancel=event)
        assert (state.root/'snapshot-latest.dpapi').read_bytes()==pointer
        assert state.control('approved_plan')=='KEEP_PLAN' and state.control('schedule_enabled')
    finally:state.close()


def test_full_csv_multiline_physical_records_keep_qty_and_strip_identity(tmp_path):
    state=BusinessState(tmp_path/'vault');facts=pins(state)
    try:
        stream=StringIO(newline='');writer=csv.writer(stream);writer.writerow(BOOKING_HEADERS)
        values=['']*37
        for key,value in zip(BUSINESS_HEADERS,rows()[0]):values[BOOKING_HEADERS.index(key)]=value
        values[BOOKING_HEADERS.index('Ghi chú')]='multiline\nnonbusiness discarded'
        values[BOOKING_HEADERS.index('Tên Tài Xế')]='PRIVATE_DRIVER_NOT_PROJECTED'
        writer.writerow(values)
        path=(tmp_path/'SYNTHETIC.csv').resolve();path.write_bytes(stream.getvalue().encode('utf-8-sig'))
        descriptor=dict(version=1,source='GOOGLE_BROWSER_VIEWER_EXPORT',source_url=f'https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit#gid=0',
            spreadsheet_id=SHEET_ID,tab='DC PHÚ THỌ',gid=0,range=AREA,retrieved_at=datetime.now(timezone.utc).isoformat(),expected_data_rows=1)
        result=ingest_csv_file(path,descriptor,state,123)
        data,source=load_snapshot(state,facts)
        assert context(data)['metrics']['booking.product_units']['value']==10
        assert 'dòng 2' in data.rows.iloc[0]['_source']
        blob=load_protected(state.root/'snapshots'/(result['digest']+'.dpapi'))
        assert b'PRIVATE_DRIVER' not in blob and b'nonbusiness discarded' not in blob
    finally:state.close()


def test_full_snapshot_missing_qty_survives_json_and_restart_as_missing_not_zero(tmp_path):
    state=BusinessState(tmp_path/'vault');facts=pins(state)
    records=[]
    for row in rows()[:2]:
        values=[None]*37
        for key,value in zip(BUSINESS_HEADERS,row):values[BOOKING_HEADERS.index(key)]=value
        records.append(values)
    records[0][9]=10.0
    records[1][9]=None
    payload=dict(version=1,source='GOOGLE_BROWSER_VIEWER_EXPORT',spreadsheet_id=SHEET_ID,tab='DC PHÚ THỌ',gid=0,
        range=AREA,retrieved_at=datetime.now(timezone.utc).isoformat(),headers=BOOKING_HEADERS,rows=records)
    imported=ingest_snapshot(payload,state,123)
    record=json.loads(load_protected(state.root/'snapshots'/(imported['digest']+'.dpapi')))
    assert record['business_evidence']['rows'][1]['values'][6] is None
    state.close();state=BusinessState(tmp_path/'vault')
    try:
        data,_=load_snapshot(state,facts)
        ctx=context(data)
        assert ctx['metrics']['booking.product_units']['value'] is None
        assert ctx['quantity_quality']['missing_quantity_rows']==1
        assert ctx['quantity_quality']['invalid_quantity_rows']==0
        assert ctx['quantity_quality']['quarantined_rows']==2
        assert ctx['metrics']['booking.eligible_product_units']['value'] is None
    finally:state.close()


def test_booking_model_context_has_product_units_and_rule_but_no_raw_ghn_so_do():
    from ops_team.telegram_agent import TelegramAgent
    from ops_team.providers import ModelConfig, FREE_MODELS
    from ops_team.schema import AGENT_NAMES
    captured=[]
    def transport(config,payload,**kwargs):
        captured.append(payload)
        return dict(summary='Synthetic quantity context',observations=[],hypotheses=[],proposals=[],requests=[],
            disagreements=[],selected_agents=[],audit_decisions=[]),'synthetic-model'
    configs={n:ModelConfig('Groq',FREE_MODELS[0],'SYNTHETIC_KEY',free_pilot=True,free_account_confirmed=True) for n in AGENT_NAMES}
    agent=TelegramAgent(configs,context(),transport=transport)
    agent._call(configs['Operations'],{'agent':'Operations','metrics':agent._context['metrics']})
    payload=captured[0]
    assert payload['metrics']['booking.product_units']['value']==37
    assert payload['quantity_quality']['rule']['version']==RULE['version']
    text=json.dumps(payload)
    assert not any(v in text for v in ('SYN001','SO1','DO1'))
    assert 'never parcels/cartons' in payload['quantity_semantics']


def test_product_unit_context_fits_real_adapter_cap_and_exact_auditor_version(monkeypatch):
    from types import SimpleNamespace
    import ops_team.providers as providers
    import ops_team.telegram_agent as telegram
    from ops_team.telegram_agent import TelegramAgent
    from ops_team.providers import ModelConfig, FREE_MODELS
    from ops_team.schema import AGENT_NAMES
    from ops_team.limits import estimate_tokens
    statuses=['delivered','delivering','transporting','ready_to_pick','picking','picked','storing',
              'return','returned','cancel','cancelled','delivery_fail','waiting','unknown']
    values=[['10/10/2026','SYNTHETIC'+str(i),status,'10/10/2026','SO'+str(i),'DO'+str(i),'10','Phú Thọ']
            for i,status in enumerate(statuses)]
    ctx=context(parsed(values));tick=[1000.0];bodies=[];reservations=[]
    fake_time=SimpleNamespace(monotonic=lambda:tick[0])
    monkeypatch.setattr(providers,'time',fake_time);monkeypatch.setattr(telegram,'time',fake_time)
    summary='Sản phẩm trong thùng được đọc theo Qty của các DO; chưa phải số kiện hay khối lượng. Cần đối chiếu khóa và các trường còn thiếu trước khi duyệt chi phí. '*3
    async def fetch(url,headers,body,timeout):
        assert estimate_tokens(body)<=7500 and body['max_completion_tokens']==1500
        data=json.loads(body['messages'][0]['content']);bodies.append(data)
        assert data['metrics']['booking.product_units']['value']==140
        assert data['metrics']['booking.product_units']['unit']=='sản phẩm'
        assert data['source_row_catalog']
        assert not any(k in data['quantity_quality'] for k in ('by_ghn','do_lines','rows'))
        answer=dict(summary=summary,observations=[{'text':'Số sản phẩm theo nguồn, chưa đối soát độc lập.','evidence_ids':['booking.product_units']}],
            hypotheses=[],proposals=['Đối chiếu chứng từ và khối lượng theo đơn.'],
            requests=[{'to':'Operations','question':'Anh có khối lượng theo đơn kèm đơn vị g/kg không?','evidence_ids':['booking.product_units']},
                      {'to':'Accounting','question':'Anh có chứng từ chi phí để đối chiếu không?','evidence_ids':[]}],
            disagreements=[],selected_agents=[],audit_decisions=[])
        if data['agent']=='Auditor':
            task=data['review_tasks'][0]
            assert task['answer']['summary']==summary and len(task['answer']['requests'])==2
            answer['audit_decisions']=[{'task_id':task['task_id'],'verdict':'unresolved',
                'reason':'Chưa có khối lượng và chứng từ chi phí.','evidence_ids':['booking.product_units']}]
        return 200,json.dumps({'model':'fake-quantity-model','choices':[{'finish_reason':'stop','message':{'content':json.dumps(answer)}}]}).encode(),None
    monkeypatch.setattr(providers,'_fetch_async',fetch)
    configs={n:ModelConfig('Groq',FREE_MODELS[0],'FAKE_PRODUCT_UNITS_KEY',free_pilot=True,free_account_confirmed=True) for n in AGENT_NAMES}
    agent=TelegramAgent(configs,ctx)
    agent._budget.clock=fake_time.monotonic
    agent._budget.sleep=lambda seconds:tick.__setitem__(0,tick[0]+seconds)
    acquire=agent._budget.acquire
    def reserve(tokens,deadline):
        reservations.append(tokens);return acquire(tokens,deadline)
    agent._budget.acquire=reserve
    for i in range(3):
        text=agent.answer(Incoming(GROUP_ID,123,i+1,None,'operations','Tôi nên đối chiếu số sản phẩm và bổ sung trường dữ liệu nào?'))
        assert 'Sản phẩm trong thùng' in text,(reservations,text)
    text=agent.answer(Incoming(GROUP_ID,123,4,None,'team','Đối chiếu số sản phẩm theo nguồn hiện tại.'))
    assert len(bodies)==5 and agent._budget.attempts==5,(reservations,text)
    assert max(reservations)<=7500 and tick[0]>1060
    audit=agent.last_presentation['audit']
    assert not audit['review_complete']
    assert audit['audit_decisions'][0]['task_id']==audit['task_id']
    assert 'Chưa có khối lượng và chứng từ chi phí' in text
