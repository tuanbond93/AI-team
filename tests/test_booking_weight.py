"""Column L totals at GHN grain. Synthetic fixtures, isolated vaults, fake HTTP."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from io import StringIO
from pathlib import Path
import csv
import hashlib
import json
import threading
import pandas as pd
import pytest
from ops_team.supra.booking import BOOKING_HEADERS,BookingData,parse_booking,booking_context
from ops_team.supra.booking_quantity import RULE,SHEET_ID,BUSINESS_HEADERS
from ops_team.supra.booking_weight import WEIGHT_RULE,WEIGHT_HEADER,WEIGHT_BUSINESS_HEADERS,parse_kg
from ops_team.telegram_snapshot import ingest_minimized_snapshot,ingest_snapshot,ingest_csv_file,load_snapshot
from ops_team.telegram_secrets import save_protected,load_protected
from ops_team.telegram_state import BusinessState,GROUP_ID
from ops_team.telegram_assistant import AssistantResponder,TZ
from ops_team.telegram_bridge import Incoming
from ops_team.chief.report import booking_review,render
from ops_team.chief.agent import finance_available

AREA="'DC PHÚ THỌ'!A1:AK100000"
CUTOFF=datetime(2026,10,20,23,59,tzinfo=TZ)


def rows():
    return [
      ['01/10/2026','SYN001','delivered','01/10/2026','SO1','DO1','10','6,256.30','Phú Thọ'],
      ['02/10/2026','SYN001','delivered','02/10/2026','SO1','DO2','20','','Phú Thọ'],
      ['15/10/2026','SYN002','delivering','15/10/2026','SO2','DO3','7','0','Sơn La'],
      ['03/10/2026','SYN003','delivered','03/10/2026','SO3','DO4','1','0.001','Điện Biên'],
      ['04/10/2026','SYN003','delivered','04/10/2026','SO3','DO5','2','0.0010','Điện Biên'],
      ['04/10/2026','SYN004','delivered','04/10/2026','SO4','DO6','3','12.300','Lai Châu'],
    ]


def parsed(values=None,*,quantity=True,weight=True):
    f=pd.DataFrame(values if values is not None else rows(),columns=WEIGHT_BUSINESS_HEADERS)
    f['_source']=[f'SYNTHETIC / row {i}' for i in range(2,len(f)+2)]
    return parse_booking(f,quantity_rules=deepcopy(RULE) if quantity else None,weight_rules=deepcopy(WEIGHT_RULE) if weight else None)


def context(data=None,cutoff=CUTOFF):
    return booking_context(data or parsed(),'2026-10',['DC PHÚ THỌ'],cutoff,dict(tab='DC PHÚ THỌ',digest='b'*64))


def pins(state):
    facts=dict(sheets_id=SHEET_ID,sheets_tab='DC PHÚ THỌ',sheets_gid='0',sheets_ranges=json.dumps({'supra_booking':AREA}),
        source_id='browser_snapshot',period='2026-10',warehouses='DC PHÚ THỌ',cutoff='23:59',report_time='13:00',timezone='Asia/Ho_Chi_Minh')
    for k,v in facts.items():state.remember(k,v,123,0,GROUP_ID)
    return facts


def staging(tmp_path,values=None):
    p=dict(schema='supra-quantity-weight-source-intake-v2',source_url=f'https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit#gid=0',
        spreadsheet_id=SHEET_ID,tab='DC PHÚ THỌ',gid=0,source='AUTHORIZED_BROWSER_VIEWER_CSV_EXPORT',
        retrieved_at=datetime.now(timezone.utc).isoformat(),raw_sha256='d'*64,raw_column_count=37,
        headers=WEIGHT_BUSINESS_HEADERS,rows=[{'sheet_row':i,'values':v} for i,v in enumerate(values or rows(),2)],
        confirmed_business_rules={k:v for k,v in RULE.items() if k!='version'},
        confirmed_weight_rules={k:v for k,v in WEIGHT_RULE.items() if k!='version'})
    raw=json.dumps(p,ensure_ascii=False).encode();path=(tmp_path/'SYNTHETIC_WEIGHT.dpapi').resolve();save_protected(path,raw)
    d={k:v for k,v in p.items() if k not in ('rows','source','spreadsheet_id')}
    d.update(row_count=len(p['rows']),minimized_plaintext_sha256=hashlib.sha256(raw).hexdigest())
    return path,d


@pytest.mark.parametrize('value,expected,status',[
    ('6,256.30','6256.30','VALID'),('1,234,567','1234567','VALID'),('0','0','VALID'),(0.001,'0.001','VALID'),
    ('743374.002','743374.002','VALID'),('',None,'MISSING'),(None,None,'MISSING'),
    ('#REF!',None,'INVALID'),('1,234',None,'INVALID'),('1.234,56',None,'INVALID'),
    ('12,34.56',None,'INVALID'),('-1',None,'INVALID'),(True,None,'INVALID'),('NaN',None,'INVALID'),
    (float('inf'),None,'INVALID'),('1e3',None,'INVALID')])
def test_kg_lexical_decimal_contract(value,expected,status):
    v,s=parse_kg(value)
    assert s==status and (str(v) if v is not None else None)==expected


def test_repeated_same_anchors_count_once_blank_do_not_missing_zero_real_decimal():
    ctx=context();q=ctx['weight_quality']
    assert Decimal(q['weight_kg'])==Decimal('6268.601')
    assert q['complete'] and q['eligible_ghn']==4 and q['missing_ghn']==0
    assert len(parsed().rows)==6 and ctx['metrics']['booking.product_units']['value']==43
    r=booking_review(ctx)
    assert Decimal(r['totals']['kg'])==Decimal('6268.601')
    assert r['totals']['pieces'] is None and r['totals']['cartons'] is None
    assert not finance_available(r) and r['totals']['revenue'] is None
    text=render(r,telegram=True)
    assert '6.268,601' in text and 'Tổng doanh thu B2B: N/A' in text
    assert 'Khối lượng B2B' not in text and 'một tổng/GHN' in text


@pytest.mark.parametrize('case,counter',[('missing','missing_ghn'),('invalid','invalid_ghn'),('conflict','conflicting_ghn')])
def test_bad_weight_groups_hold_weight_only_and_show_eligible_subtotal(case,counter):
    values=rows()
    if case=='missing':values[0][7]=''
    elif case=='invalid':values[1][7]='#REF!'
    else:values[1][7]='6646.60'
    ctx=context(parsed(values));q=ctx['weight_quality']
    assert q['weight_kg'] is None and q[counter]==1 and q['eligible_ghn']==3
    assert Decimal(q['eligible_weight_kg'])==Decimal('12.301')
    assert ctx['metrics']['booking.product_units']['value']==43
    r=booking_review(ctx);text=render(r,telegram=True)
    assert r['totals']['kg'] is None and '12,301 kg' in text
    assert 'GHN_WEIGHT_PARTIAL' in {i['code'] for i in r['issues']}
    assert ctx['metrics']['booking.weight_'+counter]['source_count']==2


@pytest.mark.parametrize('outside',['31/08/2026','25/10/2026',''])
def test_partial_dispatch_scope_withholds_whole_ghn_weight_but_only_scoped_products(outside):
    values=rows();values[1][3]=outside
    ctx=context(parsed(values));q=ctx['weight_quality']
    assert q['weight_kg'] is None and q['partial_scope_ghn']==1
    assert Decimal(q['eligible_weight_kg'])==Decimal('12.301')
    assert ctx['metrics']['booking.product_units']['value']==23
    assert ctx['metrics']['booking.rows']['value']==5


def test_bad_j_or_duplicate_full_key_does_not_discard_independent_valid_l():
    for mutation in ('j','duplicate'):
        values=rows()
        if mutation=='j':values[0][6]='#N/A'
        else:values.append(deepcopy(values[0]))
        ctx=context(parsed(values))
        assert ctx['metrics']['booking.product_units']['value'] is None
        assert Decimal(ctx['metrics']['booking.weight_kg']['value'])==Decimal('6268.601')


def test_weight_mapping_independent_of_qty_and_legacy_does_not_invent_kg():
    assert context(parsed(quantity=False))['metrics']['booking.weight_kg']['value'] is not None
    assert context(parsed(quantity=False))['metrics']['booking.product_units']['value'] is None
    assert context(parsed(weight=False))['metrics']['booking.weight_kg']['value'] is None
    assert context(parsed(weight=False))['metrics']['booking.product_units']['value']==43


def test_decimal_sum_many_ghn_is_exact():
    values=[['01/10/2026','SYN'+str(i),'delivered','01/10/2026','SO'+str(i),'DO'+str(i),'1','0.001','Phú Thọ'] for i in range(1000)]
    assert context(parsed(values))['metrics']['booking.weight_kg']['value']=='1.000'


def test_missing_l_never_uses_ab_or_other_weight_totals():
    values=rows()[:2]
    values[0][7]=''
    f=pd.DataFrame(values,columns=WEIGHT_BUSINESS_HEADERS)
    f['Weight (từng SO)']=[100000,200000]
    f['KG/Lot']=[99999,99999]
    f['_source']=['SYNTHETIC row 2','SYNTHETIC row 3']
    ctx=context(parse_booking(f,quantity_rules=RULE,weight_rules=WEIGHT_RULE))
    assert ctx['weight_quality']['missing_ghn']==1
    assert ctx['metrics']['booking.weight_kg']['value'] is None
    assert ctx['metrics']['booking.product_units']['value']==30


def test_v2_migration_restart_and_13h_unchanged_code_reports_no_http(tmp_path,monkeypatch):
    path,d=staging(tmp_path);state=BusinessState(tmp_path/'vault');facts=pins(state)
    cfg=dict(owner_id=123,bot_token='SYNTHETIC_UNUSED',models={})
    assistant=AssistantResponder(state,cfg);plan=assistant._plan_code()
    state.set_control('approved_plan',plan);state.set_control('schedule_enabled',True)
    before=state.facts();result=ingest_minimized_snapshot(path,d,state,123)
    assert result['weight_rule_version']==WEIGHT_RULE['version']
    record=json.loads(load_protected(state.root/'snapshots'/(result['digest']+'.dpapi')))
    assert record['business_evidence']['headers']==WEIGHT_BUSINESS_HEADERS
    assert record['business_evidence']['rows'][1]['values'][7]==''
    state.close();state=BusinessState(tmp_path/'vault')
    try:
        data,source=load_snapshot(state,facts)
        assert data.weight_rules==WEIGHT_RULE and data.quantity_rules==RULE
        assert data.rows.source_ghn_weight.tolist()==[r[7] for r in rows()]
        assistant=AssistantResponder(state,cfg)
        monkeypatch.setattr(assistant,'_get_agent',lambda:pytest.fail('No AI for weight intake'))
        for command in ('chief','run'):
            text=assistant.answer(Incoming(GROUP_ID,123,9,None,command,''))
            assert '6.268,601' in text and '43 sản phẩm' in text and '0 HTTP' in text
            assert 'Tổng doanh thu B2B: N/A' in text
        assert state.facts()==before and state.control('approved_plan')==plan
        assert state.control('schedule_enabled') and assistant._plan_code()==plan
    finally:state.close()


@pytest.mark.parametrize('case',['policy','header','source','hash','cancel'])
def test_v2_invalid_migration_keeps_pointer_and_plan(tmp_path,case):
    path,d=staging(tmp_path);state=BusinessState(tmp_path/'vault');pins(state)
    try:
        ingest_minimized_snapshot(path,d,state,123)
        before=(state.root/'snapshot-latest.dpapi').read_bytes()
        event=threading.Event()
        if case=='cancel':event.set()
        elif case=='hash':d['minimized_plaintext_sha256']='0'*64
        else:
            payload=json.loads(load_protected(path))
            if case=='policy':payload['confirmed_weight_rules']['aggregation']='SUM_PER_DO'
            elif case=='header':payload['headers'][7]='Weight (từng SO)'
            else:payload['gid']=1;d['gid']=1
            raw=json.dumps(payload).encode();save_protected(path,raw)
            d['minimized_plaintext_sha256']=hashlib.sha256(raw).hexdigest()
        with pytest.raises(ValueError):ingest_minimized_snapshot(path,d,state,123,cancel=event)
        assert (state.root/'snapshot-latest.dpapi').read_bytes()==before
    finally:state.close()


def test_full_csv_multiline_and_native_numeric_none_preserve_l_source(tmp_path):
    for mode in ('csv','connector'):
        state=BusinessState(tmp_path/mode);facts=pins(state)
        try:
            records=[]
            for row in rows()[:2]:
                v=[None]*37
                for key,value in zip(WEIGHT_BUSINESS_HEADERS,row):v[BOOKING_HEADERS.index(key)]=value
                v[20]='MULTILINE\nNOT_BUSINESS';v[33]='PRIVATE_DRIVER'
                records.append(v)
            records[0][11]=6256.301;records[1][11]=None
            base=dict(version=1,source='GOOGLE_BROWSER_VIEWER_EXPORT',spreadsheet_id=SHEET_ID,tab='DC PHÚ THỌ',gid=0,
                range=AREA,retrieved_at=datetime.now(timezone.utc).isoformat())
            if mode=='csv':
                stream=StringIO(newline='');w=csv.writer(stream);w.writerow(BOOKING_HEADERS);w.writerows(records)
                path=(tmp_path/'SYNTHETIC_WEIGHT.csv').resolve();path.write_bytes(stream.getvalue().encode('utf-8-sig'))
                ingest_csv_file(path,dict(**base,source_url=f'https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit#gid=0',expected_data_rows=2),state,123)
            else:ingest_snapshot(dict(**base,headers=BOOKING_HEADERS,rows=records),state,123)
            data,_=load_snapshot(state,facts);ctx=context(data)
            assert ctx['metrics']['booking.weight_kg']['value']=='6256.301'
            assert ctx['weight_quality']['missing_ghn']==0 and ctx['metrics']['booking.product_units']['value']==30
            ptr=json.loads(load_protected(state.root/'snapshot-latest.dpapi'))
            record=load_protected(state.root/'snapshots'/(ptr['digest']+'.dpapi'))
            assert b'PRIVATE_DRIVER' not in record and b'NOT_BUSINESS' not in record
            assert 'dòng 2' in data.rows.iloc[0]['_source']
        finally:state.close()


def test_weight_real_adapter_cap_history_and_exact_auditor_version(monkeypatch):
    from types import SimpleNamespace
    import ops_team.providers as providers
    import ops_team.telegram_agent as telegram
    from ops_team.telegram_agent import TelegramAgent
    from ops_team.providers import ModelConfig,FREE_MODELS
    from ops_team.schema import AGENT_NAMES
    from ops_team.limits import estimate_tokens
    statuses=['delivered','delivering','transporting','ready_to_pick','picking','picked','storing',
              'return','returned','cancel','cancelled','delivery_fail','waiting','unknown']
    values=[['10/10/2026','SYNTHETIC'+str(i),s,'10/10/2026','SO'+str(i),'DO'+str(i),'10',
             '0.001' if i<13 else '#REF!','Phú Thọ'] for i,s in enumerate(statuses)]
    ctx=context(parsed(values));tick=[1000.0];bodies=[];reservations=[]
    fake_time=SimpleNamespace(monotonic=lambda:tick[0])
    monkeypatch.setattr(providers,'time',fake_time);monkeypatch.setattr(telegram,'time',fake_time)
    summary='L là tổng kg mỗi mã GHN, không cộng theo DO. Tổng toàn phạm vi còn thiếu vì có lỗi nguồn; subtotal chỉ gồm nhóm đủ dữ liệu. Sản phẩm J giữ quy tắc riêng. '*3
    async def fetch(url,headers,body,timeout):
        assert estimate_tokens(body)<=7500 and body['max_completion_tokens']==1500
        data=json.loads(body['messages'][0]['content']);bodies.append(data)
        assert data['metrics']['booking.weight_kg']['value'] is None
        assert data['metrics']['booking.eligible_weight_kg']['value']=='0.013'
        assert data['metrics']['booking.product_units']['value']==140
        assert data['weight_quality']['rule']['weight_column']=='L'
        assert data['source_row_catalog']
        for key,m in data['metrics'].items():
            original=ctx['metrics'][key]
            definition=dict(zip(data['metric_definition_fields'],m['definition']))
            for field in ('value','label','formula','unit'):
                assert (m[field] if field in m else definition[field])==original[field]
            evidence=data['metric_evidence_catalog'][definition['source_evidence']]
            assert evidence['source_count']==original['source_count']
            assert evidence['source_sample_only']==original['source_sample_only']
            assert [data['source_row_catalog'][r] for r in evidence['source_row_refs']]==original['sources']
        text=json.dumps(data)
        assert not any(v in text for v in ('SYNTHETIC0','SO0','DO0','source_values','group_evidence'))
        answer=dict(summary=summary,observations=[{'text':'Có lỗi L cần đối chiếu độc lập.','evidence_ids':['booking.weight_invalid_ghn']}],
            hypotheses=[],proposals=['Đối chiếu L với nguồn cân/đóng hàng.'],
            requests=[{'to':'Operations','question':'Anh có nguồn cân để kiểm tra tổng L bị lỗi không?','evidence_ids':['booking.weight_invalid_ghn']},
                      {'to':'Accounting','question':'Anh có chứng từ chi phí để đối chiếu không?','evidence_ids':[]}],
            disagreements=[],selected_agents=[],audit_decisions=[])
        if data['agent']=='Auditor':
            task=data['review_tasks'][0]
            assert task['answer']['summary']==summary and len(task['answer']['requests'])==2
            answer['audit_decisions']=[{'task_id':task['task_id'],'verdict':'unresolved',
                'reason':'Tổng kg và chứng từ chưa đối soát.','evidence_ids':['booking.weight_invalid_ghn']}]
        return 200,json.dumps({'model':'fake-weight-model','choices':[{'finish_reason':'stop','message':{'content':json.dumps(answer)}}]}).encode(),None
    monkeypatch.setattr(providers,'_fetch_async',fetch)
    configs={n:ModelConfig('Groq',FREE_MODELS[0],'FAKE_WEIGHT_KEY',free_pilot=True,free_account_confirmed=True) for n in AGENT_NAMES}
    agent=TelegramAgent(configs,ctx);agent._budget.clock=fake_time.monotonic
    agent._budget.sleep=lambda seconds:tick.__setitem__(0,tick[0]+seconds)
    acquire=agent._budget.acquire
    def reserve(tokens,deadline):reservations.append(tokens);return acquire(tokens,deadline)
    agent._budget.acquire=reserve
    for i in range(3):
        text=agent.answer(Incoming(GROUP_ID,123,i+1,None,'operations','Tôi cần đối chiếu kg và sản phẩm như thế nào?'))
        assert 'L là tổng kg' in text,(reservations,text)
    text=agent.answer(Incoming(GROUP_ID,123,4,None,'team','Đối chiếu kg và sản phẩm theo nguồn hiện tại.'))
    assert len(bodies)==5 and agent._budget.attempts==5,(reservations,text)
    assert max(reservations)<=7500 and tick[0]>1060
    audit=agent.last_presentation['audit']
    assert not audit['review_complete'] and audit['audit_decisions'][0]['task_id']==audit['task_id']
    assert 'Tổng kg và chứng từ chưa đối soát' in text
