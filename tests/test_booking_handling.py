"""DC-only owner-terms component; synthetic routes/stores, temp vault, no network."""
from copy import deepcopy
from datetime import datetime,timezone
from decimal import Decimal
from io import StringIO
import csv
import hashlib
import json
import threading
import httpx
import pandas as pd
import pytest
from ops_team.supra.booking import BOOKING_HEADERS,parse_booking,booking_context
from ops_team.supra.booking_quantity import RULE,SHEET_ID
from ops_team.supra.booking_weight import WEIGHT_RULE
from ops_team.supra.booking_handling import (HANDLING_SCHEMA,HANDLING_POLICY,HANDLING_BUSINESS_HEADERS,
    catalog_digest,validate_catalog,geography,catalog_from_owner_confirmations)
from ops_team.telegram_snapshot import ingest_minimized_snapshot,ingest_snapshot,ingest_csv_file,load_snapshot,active_handling_config
from ops_team.telegram_secrets import save_protected,load_protected
from ops_team.telegram_state import BusinessState,GROUP_ID
from ops_team.telegram_assistant import AssistantResponder,TZ
from ops_team.telegram_bridge import Incoming
from ops_team.chief.report import booking_review,render
from ops_team.chief.agent import finance_available,code_version

AREA="'DC PHÚ THỌ'!A1:AK100000"
CUTOFF=datetime(2026,10,31,23,59,tzinfo=TZ)
STORE='PRIVATE_STORE_OVERRIDE_NEVER_TO_MODEL'
PIN=dict(spreadsheet_id=SHEET_ID,tab='DC PHÚ THỌ',gid=0)


def catalog():
    c=dict(schema='supra-dc-handling-catalog-v1',version='synthetic-v1',source_pin=deepcopy(PIN),
        period='2026-10',provenance='USER_CONFIRMED_IN_CODEX',confirmed_date='2026-10-26',
        evidence_hashes=[dict(file_id='synthetic-owner.json',sha256='a'*64)],
        rates=[dict(rate_id='R1',**{'from':'2026-10-01','to':'2026-10-18'},vnd_per_kg='200',currency='VND',evidence_ref='synthetic#/rate/0'),
               dict(rate_id='R2',**{'from':'2026-10-19','to':'2026-10-31'},vnd_per_kg='100',currency='VND',evidence_ref='synthetic#/rate/1')],
        routing=[])
    for i,(scope,district,store,mode) in enumerate([('DISTRICT','H. Chuyển A','','TRANSFER'),
        ('DISTRICT','H. Thẳng B','','DIRECT'),('STORE','H. Chuyển A',STORE,'DIRECT')]):
        c['routing'].append(dict(rule_id='M'+str(i),scope=scope,province='T. Province Synthetic',district=district,
            store_id=store,routing=mode,**{'from':'2026-10-01','to':'2026-10-31'},evidence_ref='synthetic#/routing/'+str(i)))
    return c


def rows():
    return [
      ['01/10/2026','SYN001','delivered','01/10/2026','SO1','DO1','STORE1','10','1.234','Tỉnh Province Synthetic','Huyện Chuyển A'],
      ['02/10/2026','SYN001','delivered','02/10/2026','SO1','DO2','STORE1','20','','Tỉnh Province Synthetic','Huyện Chuyển A'],
      ['19/10/2026','SYN002','delivering','19/10/2026','SO2','DO3','STORE2','7','0.001','Tỉnh Province Synthetic','Huyện Chuyển A'],
      ['19/10/2026','SYN003','delivered','19/10/2026','SO3','DO4','STORE3','3','#REF!','Tỉnh Province Synthetic','Huyện Thẳng B'],
      ['10/10/2026','SYN004','delivered','10/10/2026','SO4','DO5',STORE,'2','#REF!','Tỉnh Province Synthetic','Huyện Chuyển A'],
    ]


def parsed(values=None,c=None,approved=True):
    c=deepcopy(c or catalog());f=pd.DataFrame(values if values is not None else rows(),columns=HANDLING_BUSINESS_HEADERS)
    f['_source']=[f'SYNTHETIC row {i}' for i in range(2,len(f)+2)]
    return parse_booking(f,quantity_rules=RULE,weight_rules=WEIGHT_RULE,
        handling_policy=deepcopy(HANDLING_POLICY) if approved else None,handling_catalog=c if approved else None,
        handling_catalog_sha256=catalog_digest(c) if approved else None)


def context(data=None,cutoff=CUTOFF):
    return booking_context(data or parsed(),'2026-10',['DC PHÚ THỌ'],cutoff,dict(tab='DC PHÚ THỌ',digest='b'*64))


def pins(state):
    facts=dict(sheets_id=SHEET_ID,sheets_tab='DC PHÚ THỌ',sheets_gid='0',sheets_ranges=json.dumps({'supra_booking':AREA}),
        source_id='browser_snapshot',period='2026-10',warehouses='DC PHÚ THỌ',cutoff='23:59',report_time='13:00',timezone='Asia/Ho_Chi_Minh')
    for k,v in facts.items():state.remember(k,v,123,0,GROUP_ID)
    return facts


def staging(tmp_path,values=None,c=None):
    c=deepcopy(c or catalog())
    p=dict(schema=HANDLING_SCHEMA,source_url=f'https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit#gid=0',
        spreadsheet_id=SHEET_ID,tab='DC PHÚ THỌ',gid=0,source='AUTHORIZED_BROWSER_VIEWER_CSV_EXPORT',
        retrieved_at=datetime.now(timezone.utc).isoformat(),raw_sha256='d'*64,raw_column_count=37,
        headers=HANDLING_BUSINESS_HEADERS,rows=[{'sheet_row':i,'values':v} for i,v in enumerate(values or rows(),2)],
        confirmed_business_rules={k:v for k,v in RULE.items() if k!='version'},
        confirmed_weight_rules={k:v for k,v in WEIGHT_RULE.items() if k!='version'},
        confirmed_handling_policy=HANDLING_POLICY,handling_catalog=c,handling_catalog_sha256=catalog_digest(c))
    raw=json.dumps(p,ensure_ascii=False).encode();path=(tmp_path/'SYNTHETIC_HANDLING.dpapi').resolve();save_protected(path,raw)
    d={k:v for k,v in p.items() if k not in ('rows','source','spreadsheet_id','handling_catalog')}
    d.update(row_count=len(p['rows']),minimized_plaintext_sha256=hashlib.sha256(raw).hexdigest(),handling_catalog_version=c['version'])
    return path,d


def full_records(values=None):
    records=[]
    for row in values or rows():
        out=[None]*37
        for k,v in zip(HANDLING_BUSINESS_HEADERS,row):out[BOOKING_HEADERS.index(k)]=v
        out[8]='PRIVATE_STORE_NAME_NOT_NEEDED';out[20]='multiline\nnot business';out[33]='PRIVATE_DRIVER'
        records.append(out)
    return records


def test_dc_component_uses_once_per_ghn_two_rates_and_store_precedence_exact_decimal():
    ctx=context();h=ctx['handling_quality']
    assert h['complete'] and Decimal(h['component_vnd'])==Decimal('246.900')
    assert h['eligible_transfer_ghn']==2 and h['direct_excluded_ghn']==2 and h['unresolved_ghn']==0
    assert Decimal(h['eligible_kg'])==Decimal('1.235')
    assert [Decimal(b['eligible_vnd']) for b in ctx['handling_rate_buckets']]==[Decimal('246.800'),Decimal('0.100')]
    assert ctx['metrics']['booking.product_units']['value']==42
    assert ctx['metrics']['booking.weight_kg']['value'] is None  # DIRECT bad L still affects BOOKING kg, not handling.
    r=booking_review(ctx);text=render(r,telegram=True)
    assert '246,900 VND theo điều khoản' in text and 'chưa nghiệm thu/thanh toán' in text
    assert all(r['totals'][k] is None for k in ('stage1','stage2','stage3','totalCost','revenue','gp','marginPct'))
    assert not finance_available(r)
    assert STORE not in text and 'Province Synthetic' not in text


@pytest.mark.parametrize('case,counter',[
    ('missing','missing_weight'),('invalid','invalid_weight'),('weight_conflict','conflicting_weight'),
    ('mixed_rate','mixed_rate'),('mixed_route','mixed_routing'),('unknown_route','unknown_routing'),
    ('unknown_store','unknown_routing'),('outside','partial_scope'),('unknown_date','partial_scope'),('cutoff','partial_scope')])
def test_transfer_group_guards_keep_whole_component_na_and_partial_subtotal(case,counter):
    values=rows();cutoff=CUTOFF
    if case=='missing':values[0][8]=''
    elif case=='invalid':values[1][8]='#REF!'
    elif case=='weight_conflict':values[1][8]='2.234'
    elif case=='mixed_rate':values[1][3]='19/10/2026'
    elif case=='mixed_route':values[1][10]='Huyện Thẳng B'
    elif case=='unknown_route':values[1][10]='Huyện Chuyển A gần giống'
    elif case=='unknown_store':values[1][6]=''
    elif case=='outside':values[1][3]='31/08/2026'
    elif case=='unknown_date':values[1][3]=''
    else:values[1][3]='20/10/2026';cutoff=datetime(2026,10,19,23,59,tzinfo=TZ)
    ctx=context(parsed(values),cutoff);h=ctx['handling_quality']
    assert h['component_vnd'] is None and Decimal(h['eligible_vnd'])==Decimal('0.100')
    assert h['unresolved_ghn']==1 and h['counters'][counter]==1
    assert h['direct_excluded_ghn']==2
    r=booking_review(ctx)
    assert 'DC_HANDLING_PARTIAL' in {i['code'] for i in r['issues']}
    assert r['totals']['totalCost'] is None and not finance_available(r)


@pytest.mark.parametrize('day,rate',[('01/10/2026',200),('18/10/2026',200),('19/10/2026',100),('31/10/2026',100)])
def test_e_date_boundary_inclusive_not_l_anchor_or_first_last(day,rate):
    values=rows()[:1];values[0][3]=day
    assert Decimal(context(parsed(values))['handling_quality']['component_vnd'])==Decimal('1.234')*rate


def test_direct_bad_l_not_missing_handling_all_direct_zero_and_zero_kg_transfer_valid():
    direct=context(parsed(rows()[3:]));assert direct['handling_quality']['component_vnd']=='0'
    assert direct['handling_quality']['counters']['invalid_weight']==0
    values=rows()[:1];values[0][8]='0'
    h=context(parsed(values))['handling_quality']
    assert Decimal(h['component_vnd'])==0 and h['eligible_transfer_ghn']==1


def test_j_errors_or_duplicate_do_do_not_multiply_or_block_once_ghn_component():
    values=rows();values[0][7]='#N/A';values.append(deepcopy(values[0]))
    ctx=context(parsed(values))
    assert ctx['metrics']['booking.product_units']['value'] is None
    assert Decimal(ctx['handling_quality']['component_vnd'])==Decimal('246.900')


def test_legacy_no_approval_no_handling_and_cost_version_changes_with_component():
    assert not context(parsed(approved=False))['handling_quality']['available']
    first=booking_review(context());values=rows();values[0][8]='2.234'
    second=booking_review(context(parsed(values)))
    assert first['totals']==second['totals']
    assert code_version(first)!=code_version(second)


@pytest.mark.parametrize('case',['hash','source','version','overlap_rate','overlap_route','alias_conflict','mode','currency','outside_dates','proof'])
def test_catalog_rejects_bad_or_conflicting_approval(case):
    c=catalog();sha=catalog_digest(c);source=deepcopy(PIN)
    if case=='hash':sha='0'*64
    elif case=='source':source['gid']=1
    elif case=='version':c['version']='invalid version'
    elif case=='overlap_rate':c['rates'][1]['from']='2026-10-18'
    elif case in ('overlap_route','alias_conflict'):
        extra=deepcopy(c['routing'][0]);extra['rule_id']='NEW';extra['routing']='DIRECT'
        if case=='alias_conflict':extra['district']='Huyện Chuyển A'
        c['routing'].append(extra)
    elif case=='mode':c['routing'][0]['routing']='GUESSED'
    elif case=='currency':c['rates'][0]['currency']='USD'
    elif case=='outside_dates':c['routing'][0]['to']='2026-11-01'
    else:c['evidence_hashes'][0]['sha256']='bad'
    with pytest.raises(ValueError):validate_catalog(c,source,expected_digest=sha if case=='hash' else None)


def test_aliases_exact_nfc_admin_prefix_not_fuzzy():
    assert geography(' H. Hạ Hoà ')==geography('Huyện Hạ Hòa')
    assert geography('T. Province Synthetic',province=True)==geography('Tỉnh Province Synthetic',province=True)
    assert geography('TP. Địa Bàn')==geography('Thành phố Địa Bàn')
    assert geography('H. Chuyen A')!=geography('H. Chuyển A')


def test_v3_roundtrip_restart_plan_same_and_chief_run_zero_http(tmp_path,monkeypatch):
    path,d=staging(tmp_path);state=BusinessState(tmp_path/'vault');facts=pins(state)
    cfg=dict(owner_id=123,bot_token='SYNTHETIC_UNUSED',models={})
    assistant=AssistantResponder(state,cfg);plan=assistant._plan_code()
    state.set_control('approved_plan',plan);state.set_control('schedule_enabled',True);before=state.facts()
    result=ingest_minimized_snapshot(path,d,state,123)
    assert result['handling_catalog_sha256']==d['handling_catalog_sha256']
    stored=json.loads(load_protected(state.root/'snapshots'/(result['digest']+'.dpapi')))
    assert stored['handling_catalog']==catalog() and stored['business_evidence']['headers']==HANDLING_BUSINESS_HEADERS
    assert stored['business_evidence']['rows'][4]['values'][6]==STORE
    assert STORE not in json.dumps(d)
    state.close();state=BusinessState(tmp_path/'vault')
    try:
        data,_=load_snapshot(state,facts)
        assert data.handling_catalog_sha256==d['handling_catalog_sha256']
        assert Decimal(context(data)['handling_quality']['component_vnd'])==Decimal('246.900')
        assistant=AssistantResponder(state,cfg)
        monkeypatch.setattr(assistant,'_get_agent',lambda:pytest.fail('handling component must keep Chief intake zero HTTP'))
        for command in ('chief','run'):
            text=assistant.answer(Incoming(GROUP_ID,123,9,None,command,''))
            assert '246,900 VND theo điều khoản' in text and '0 HTTP' in text and 'Tổng doanh thu B2B: N/A' in text
        assert state.facts()==before and state.control('approved_plan')==plan and state.control('schedule_enabled')
        assert assistant._plan_code()==plan
    finally:state.close()


@pytest.mark.parametrize('case',['hash','catalog_hash','catalog_version','policy','extra_descriptor','cancel','stale'])
def test_v3_rejection_does_not_replace_pointer(tmp_path,case):
    path,d=staging(tmp_path);state=BusinessState(tmp_path/'vault');pins(state)
    try:
        ingest_minimized_snapshot(path,d,state,123);event=threading.Event()
        if case=='stale':
            p=json.loads(load_protected(state.root/'snapshot-latest.dpapi'));p['retrieved_epoch']+=100
            save_protected(state.root/'snapshot-latest.dpapi',json.dumps(p).encode())
        elif case=='hash':d['minimized_plaintext_sha256']='0'*64
        elif case=='catalog_hash':d['handling_catalog_sha256']='0'*64
        elif case=='catalog_version':d['handling_catalog_version']='WRONG'
        elif case=='policy':d['confirmed_handling_policy']={**HANDLING_POLICY,'vehicle_scope':'ALL'}
        elif case=='extra_descriptor':d['handling_catalog']=catalog()
        else:event.set()
        pointer=(state.root/'snapshot-latest.dpapi').read_bytes()
        with pytest.raises(ValueError):ingest_minimized_snapshot(path,d,state,123,cancel=event)
        assert (state.root/'snapshot-latest.dpapi').read_bytes()==pointer
    finally:state.close()


@pytest.mark.parametrize('mode',['csv','connector'])
def test_same_pin_full37_refresh_preserves_catalog_after_restart_and_never_copies_i_pii(tmp_path,mode):
    path,d=staging(tmp_path);state=BusinessState(tmp_path/'vault');facts=pins(state)
    try:
        ingest_minimized_snapshot(path,d,state,123)
        base=dict(version=1,source='GOOGLE_BROWSER_VIEWER_EXPORT',spreadsheet_id=SHEET_ID,tab='DC PHÚ THỌ',gid=0,
            range=AREA,retrieved_at=datetime.now(timezone.utc).isoformat())
        records=full_records()
        if mode=='csv':
            stream=StringIO(newline='');w=csv.writer(stream);w.writerow(BOOKING_HEADERS);w.writerows(records)
            file=(tmp_path/'SYNTHETIC.csv').resolve();file.write_bytes(stream.getvalue().encode('utf-8-sig'))
            ingest_csv_file(file,dict(**base,source_url=f'https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit#gid=0',expected_data_rows=5),state,123)
        else:ingest_snapshot(dict(**base,headers=BOOKING_HEADERS,rows=records),state,123)
        state.close();state=BusinessState(tmp_path/'vault')
        data,_=load_snapshot(state,facts)
        assert data.handling_catalog==catalog() and data.handling_catalog_sha256==d['handling_catalog_sha256']
        assert Decimal(context(data)['handling_quality']['component_vnd'])==Decimal('246.900')
        ptr=json.loads(load_protected(state.root/'snapshot-latest.dpapi'))
        stored=load_protected(state.root/'snapshots'/(ptr['digest']+'.dpapi'))
        assert b'PRIVATE_STORE_NAME_NOT_NEEDED' not in stored and b'PRIVATE_DRIVER' not in stored
        assert b'not business' not in stored
        assert 'dòng 2' in data.rows.iloc[0]['_source']
    finally:state.close()


def test_sheets_connector_reuses_same_pin_approval_without_adopting_it_for_another_source(tmp_path):
    from ops_team.telegram_sheets import read_sheets
    state=BusinessState(tmp_path/'vault');facts=pins(state);path,d=staging(tmp_path)
    account=dict(type='service_account',client_email='fake@fake-project.iam.gserviceaccount.com',
        private_key='-----BEGIN PRIVATE KEY-----FAKE',token_uri='https://oauth2.googleapis.com/token')
    try:
        ingest_minimized_snapshot(path,d,state,123)
        def handle(request):
            if ':batchGet' not in request.url.path:
                return httpx.Response(200,json={'sheets':[{'properties':{'title':'DC PHÚ THỌ','sheetId':0}}]})
            return httpx.Response(200,json={'spreadsheetId':facts['sheets_id'],
                'valueRanges':[{'range':AREA,'values':[BOOKING_HEADERS,*full_records()]}]})
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            data,_=read_sheets(facts,account,client=client,token_provider=lambda _:'FAKE_ACCESS',state=state)
            assert data.handling_catalog==catalog()
            assert Decimal(context(data)['handling_quality']['component_vnd'])==Decimal('246.900')
            facts['sheets_id']='SYNTHETIC_OTHER_SHEET_1234567'
            other,_=read_sheets(facts,account,client=client,token_provider=lambda _:'FAKE_ACCESS',state=state)
            assert other.handling_catalog is None and not context(other)['handling_quality']['available']
    finally:state.close()


def test_approved_catalog_not_lost_or_corrupted_by_partial_refresh_or_bad_stored_hash(tmp_path):
    path,d=staging(tmp_path);state=BusinessState(tmp_path/'vault');facts=pins(state)
    try:
        ingest_minimized_snapshot(path,d,state,123)
        before=(state.root/'snapshot-latest.dpapi').read_bytes()
        payload=dict(version=1,source='GOOGLE_BROWSER_VIEWER_EXPORT',spreadsheet_id=SHEET_ID,tab='DC PHÚ THỌ',gid=0,
            range=AREA,retrieved_at=datetime.now(timezone.utc).isoformat(),headers=['Ngày LTC','Mã đơn GHN','Trạng thái','Ngày xuất'],
            rows=[['01/10/2026','SYN001','delivered','01/10/2026']])
        with pytest.raises(ValueError):ingest_snapshot(payload,state,123)
        assert (state.root/'snapshot-latest.dpapi').read_bytes()==before
        ptr=json.loads(load_protected(state.root/'snapshot-latest.dpapi'))
        record=json.loads(load_protected(state.root/'snapshots'/(ptr['digest']+'.dpapi')))
        record['handling_catalog_sha256']=None
        raw=json.dumps(record).encode();sha=hashlib.sha256(raw).hexdigest()
        save_protected(state.root/'snapshots'/(sha+'.dpapi'),raw)
        save_protected(state.root/'snapshot-latest.dpapi',json.dumps({'digest':sha,'retrieved_epoch':0}).encode())
        with pytest.raises(ValueError):load_snapshot(state,facts)
    finally:state.close()


def test_handling_conversation_footer_describes_available_component_without_legacy_contradiction():
    from ops_team.telegram_presentation import conversation,BOOKING_LIMIT,HANDLING_LIMIT
    answer=dict(summary='Đối chiếu phần bốc xếp theo điều khoản trước khi nghiệm thu.',observations=[],hypotheses=[],
        proposals=[],requests=[dict(to='Operations',question='Anh có bảng kê để đối chiếu không?',evidence_ids=[])],disagreements=[])
    text=conversation(answer,context(),'CHƯA QUA AUDITOR')
    assert HANDLING_LIMIT in text and BOOKING_LIMIT not in text
    old=conversation(answer,context(parsed(approved=False)),'CHƯA QUA AUDITOR')
    assert BOOKING_LIMIT in old and HANDLING_LIMIT not in old


@pytest.mark.parametrize('source_style',['viewer','short'])
def test_handling_real_adapter_cap_history_lossless_metrics_and_exact_auditor(monkeypatch,source_style):
    from types import SimpleNamespace
    import ops_team.providers as providers
    import ops_team.telegram_agent as telegram
    from ops_team.telegram_agent import TelegramAgent
    from ops_team.providers import ModelConfig,FREE_MODELS
    from ops_team.schema import AGENT_NAMES
    from ops_team.limits import estimate_tokens
    statuses=['delivered','delivering','transporting','ready_to_pick','picking','picked','storing',
              'return','returned','cancel','cancelled','delivery_fail','waiting','unknown']
    values=[['10/10/2026','SYNTHETIC'+str(i),s,'10/10/2026' if i<8 else '20/10/2026',
        'SO'+str(i),'DO'+str(i),STORE if i==13 else 'STORE'+str(i),'10','0.001' if i!=1 else '#REF!',
        'Tỉnh Province Synthetic','Huyện Chuyển A'] for i,s in enumerate(statuses)]
    data_rows=parsed(values)
    if source_style=='viewer':
        data_rows.rows['_source']=[f"GoogleViewer SYNTHETIC_SHEET_IDENTIFIER_LONG_1234567890 / 'DC PHÚ THỌ'!A1:AK100000 / dòng {i+22000} / raw#0123456789ab" for i in range(len(values))]
    ctx=context(data_rows);tick=[1000.0];bodies=[];reservations=[];operations_answers=[]
    fake_time=SimpleNamespace(monotonic=lambda:tick[0])
    monkeypatch.setattr(providers,'time',fake_time);monkeypatch.setattr(telegram,'time',fake_time)
    summary='Bốc xếp DC được tính riêng theo điều khoản owner, chưa phải tiền đã nghiệm thu hoặc thanh toán. Tổng toàn phạm vi còn nhóm cần đối chiếu; subtotal chỉ là phần đủ nguồn, không thay tổng chi phí hoặc P&L. Sản phẩm J và kg tổng L giữ quy tắc riêng. '*2
    async def fetch(url,headers,body,timeout):
        assert estimate_tokens(body)<=7500 and body['max_completion_tokens']==1500
        data=json.loads(body['messages'][0]['content']);bodies.append(data)
        assert set(data['metrics'])==set(ctx['metrics'])
        restored_provenance={**data['source_provenance'],
            'quantity_rule_version':data['quantity_quality']['rule']['version'],
            'weight_rule_version':data['weight_quality']['rule']['version'],
            'handling_policy_version':data['handling_quality']['policy_version'],
            'handling_catalog_version':data['handling_quality']['catalog_version'],
            'handling_catalog_sha256':data['handling_quality']['catalog_sha256']}
        assert restored_provenance==ctx['source_provenance']
        for key,values in data['metrics'].items():
            m=dict(zip(data['metric_fields'],values));original=ctx['metrics'][key]
            label=('Dòng trạng thái nguồn: '+key.removeprefix('booking.status.')) if m['label']=='@STATUS' else m['label']
            assert m['value']==original['value'] and label==original['label']
            assert data['formula_catalog'][m['formula_ref']]==original['formula'] and m['unit']==original['unit']
            proof=data['metric_evidence_catalog'][m['source_evidence']]
            assert proof['source_count']==original['source_count'] and proof['source_sample_only']==original['source_sample_only']
            t=data.get('source_row_template',{'prefix':'','suffix':''})
            assert [t['prefix']+data['source_row_catalog'][r]+t['suffix'] for r in proof['source_row_refs']]==original['sources']
            for f,v in data['metric_context'].items():assert original[f]==v
        text=json.dumps(data)
        assert not any(v in text for v in (STORE,'Province Synthetic','SYNTHETIC0','SO0','DO0','handling_catalog"','store_id'))
        if 'conversation' in data:
            assert data['conversation']==agent._history.get((GROUP_ID,None,123),[])
            assert len(data['conversation'])<=3
        answer=dict(summary=summary,observations=[{'text':'Subtotal chỉ gồm nhóm TRANSFER đủ nguồn, chưa là tổng component.','evidence_ids':['booking.dc_handling_eligible_vnd']}],
            hypotheses=['Nhóm L lỗi còn cần đối chiếu nguồn.'],proposals=['Đối chiếu các nhóm giữ riêng trước nghiệm thu.'],
            requests=[{'to':'Operations','question':'Anh có tổng L có căn cứ hoặc kg tách của các GHN giữ riêng không?','evidence_ids':['booking.dc_handling_unresolved_ghn']},
                      {'to':'Accounting','question':'Anh có bảng kê bốc xếp để đối chiếu điều khoản không?','evidence_ids':[]}],
            disagreements=[],selected_agents=[],audit_decisions=[])
        if data['agent']=='Auditor':
            task=data['review_tasks'][0]
            assert task['answer']==operations_answers[-1]
            answer['audit_decisions']=[{'task_id':task['task_id'],'verdict':'unresolved',
                'reason':'Nhóm giữ riêng chưa đủ chứng cứ, chưa là tiền nghiệm thu.','evidence_ids':['booking.dc_handling_unresolved_ghn']}]
        else:operations_answers.append(deepcopy(answer))
        return 200,json.dumps({'model':'fake-handling-model','choices':[{'finish_reason':'stop','message':{'content':json.dumps(answer)}}]}).encode(),None
    monkeypatch.setattr(providers,'_fetch_async',fetch)
    configs={n:ModelConfig('Groq',FREE_MODELS[0],'FAKE_HANDLING_KEY',free_pilot=True,free_account_confirmed=True) for n in AGENT_NAMES}
    agent=TelegramAgent(configs,ctx);agent._budget.clock=fake_time.monotonic
    agent._budget.sleep=lambda seconds:tick.__setitem__(0,tick[0]+seconds)
    acquire=agent._budget.acquire
    def reserve(tokens,deadline):reservations.append(tokens);return acquire(tokens,deadline)
    agent._budget.acquire=reserve
    for i in range(4):
        text=agent.answer(Incoming(GROUP_ID,123,i+1,None,'operations','Tôi cần đối chiếu phần bốc xếp và nguồn kg nào trước?'))
        assert 'Bốc xếp DC' in text,(reservations,text)
    assert len(bodies[3]['conversation'])==3
    text=agent.answer(Incoming(GROUP_ID,123,5,None,'team','Đối chiếu component bốc xếp, chưa kết luận total cost.'))
    assert len(bodies)==6 and agent._budget.attempts==6,(reservations,text)
    assert max(reservations)<=7500 and tick[0]>1060
    audit=agent.last_presentation['audit']
    assert not audit['review_complete'] and audit['audit_decisions'][0]['task_id']==audit['task_id']
    assert 'Nhóm giữ riêng chưa đủ chứng cứ' in text


def test_missing_rate_does_not_reuse_prior_price_and_missing_ghn_does_not_become_zero():
    c=catalog();c['rates'][0]['to']='2026-10-17'
    values=rows();values[0][3]='18/10/2026';values[1][3]='18/10/2026'
    h=context(parsed(values,c))['handling_quality']
    assert h['component_vnd'] is None and h['counters']['unknown_rate']==1
    values=rows();values[3][1]='?'
    h=context(parsed(values))['handling_quality']
    assert h['component_vnd'] is None and h['missing_id_rows']==1
    assert Decimal(h['eligible_vnd'])==Decimal('246.900')


@pytest.mark.parametrize('version',[1,2])
def test_v1_v2_staging_remains_readable_without_assuming_handling(version,tmp_path):
    path,d=staging(tmp_path);p=json.loads(load_protected(path))
    indexes=[0,1,2,3,4,5,7,9] if version==1 else [0,1,2,3,4,5,7,8,9]
    p['headers']=[p['headers'][i] for i in indexes]
    p['schema']='supra-product-source-intake-v1' if version==1 else 'supra-quantity-weight-source-intake-v2'
    for row in p['rows']:row['values']=[row['values'][i] for i in indexes]
    for k in ('confirmed_handling_policy','handling_catalog','handling_catalog_sha256'):p.pop(k)
    if version==1:p.pop('confirmed_weight_rules')
    raw=json.dumps(p).encode();save_protected(path,raw)
    d={k:v for k,v in p.items() if k not in ('rows','source','spreadsheet_id')}
    d.update(row_count=len(p['rows']),minimized_plaintext_sha256=hashlib.sha256(raw).hexdigest())
    state=BusinessState(tmp_path/'vault');facts=pins(state)
    try:
        ingest_minimized_snapshot(path,d,state,123);data,_=load_snapshot(state,facts)
        ctx=context(data)
        assert ctx['metrics']['booking.product_units']['value']==42
        assert not ctx['handling_quality']['available'] and 'handling_component' not in booking_review(ctx)
        assert not any(k.startswith('booking.dc_handling_') for k in ctx['metrics'])
    finally:state.close()


def test_ram_catalog_adapter_uses_confirmed_scope_dates_and_store_identity_only():
    c=catalog()
    costs=dict(schema='supra-owner-cost-confirmations-v1',deterministic_calculation_ready=True,confirmed_date='2026-10-26',rates=[])
    for rate in c['rates']:
        costs['rates'].append(dict(cost_component='DC_HANDLING',applicable_month='2026-10',currency='VND',
            vehicle_scope='TRANSFER_VEHICLES_ONLY',billing_date_basis={'column':'E'},
            billable_weight_source={'column':'L','unit':'kg','grain':'GHN_TOTAL_ACROSS_DO','aggregation':'COUNT_ONCE_PER_ELIGIBLE_GHN'},
            effective_from=rate['from'],effective_to_confirmed=rate['to'],effective_from_inclusive=True,
            effective_to_inclusive=True,rate_vnd_per_kg=int(rate['vnd_per_kg'])))
    models=dict(status='OWNER_ROUTING_AND_OCTOBER_EFFECTIVE_DATES_CONFIRMED',rules=[])
    for r in c['routing']:
        models['rules'].append(dict(matching_scope='DISTRICT' if r['scope']=='DISTRICT' else 'STORE_EXCEPTION',
            area=r['district'] if r['scope']=='DISTRICT' else 'SYNTHETIC STORE LABEL',province=r['province'],
            routing_confirmation_provenance='USER_CONFIRMED_IN_CODEX',routing_mode_confirmed=r['routing'],
            dc_handling_applicability_confirmed=r['routing']=='TRANSFER',effective_from_inclusive=True,effective_to_inclusive=True,
            effective_from_confirmed=r['from'],effective_to_confirmed=r['to']))
    store_match=dict(source_store_column='H',source_name_column='I',user_rule_area='SYNTHETIC STORE LABEL',
        source_tab=PIN['tab'],gid=0,observed_source_district=c['routing'][-1]['district'],matched_store_id=STORE)
    built=catalog_from_owner_confirmations(costs,models,store_match,PIN,version='synthetic-built',period='2026-10',evidence_hashes=c['evidence_hashes'])
    assert len(built['routing'])==3 and built['routing'][-1]['store_id']==STORE
    assert 'SYNTHETIC STORE LABEL' not in json.dumps(built)
    assert Decimal(context(parsed(c=built))['handling_quality']['component_vnd'])==Decimal('246.900')
    costs['rates'][0]['billable_weight_source']['grain']='DO'
    with pytest.raises(ValueError):catalog_from_owner_confirmations(costs,models,store_match,PIN,version='bad',period='2026-10',evidence_hashes=c['evidence_hashes'])


def test_native_numeric_h_and_blank_never_coerce_store_key_to_float_or_miss_override(tmp_path):
    c=catalog();c['routing'][-1]['store_id']='100'
    path,d=staging(tmp_path,c=c);state=BusinessState(tmp_path/'vault');facts=pins(state)
    try:
        ingest_minimized_snapshot(path,d,state,123)
        records=full_records([rows()[0],rows()[3]])
        records[0][7]=100;records[0][11]='#REF!';records[1][7]=None
        payload=dict(version=1,source='GOOGLE_SHEETS_CONNECTOR',spreadsheet_id=SHEET_ID,tab='DC PHÚ THỌ',gid=0,
            range=AREA,retrieved_at=datetime.now(timezone.utc).isoformat(),headers=BOOKING_HEADERS,rows=records)
        ingest_snapshot(payload,state,123)
        data,_=load_snapshot(state,facts)
        assert data.rows.source_store_id.tolist()==['100','']
        h=context(data)['handling_quality']
        assert h['direct_excluded_ghn']==2 and h['unresolved_ghn']==0 and h['component_vnd']=='0'
    finally:state.close()


def test_same_sheets_bytes_with_new_approved_catalog_never_reuses_old_model_metrics(tmp_path,monkeypatch):
    from dataclasses import asdict
    from ops_team.providers import ModelConfig,FREE_MODELS
    from ops_team.schema import AGENT_NAMES
    state=BusinessState(tmp_path/'vault');facts=pins(state)
    configs={n:asdict(ModelConfig('Groq',FREE_MODELS[0],'FAKE_CATALOG_CACHE_KEY',free_pilot=True,free_account_confirmed=True)) for n in AGENT_NAMES}
    responder=AssistantResponder(state,dict(owner_id=123,bot_token='SYNTHETIC_UNUSED',models=configs))
    current=[parsed()]
    source=dict(digest='b'*64,tab='DC PHÚ THỌ',gid='0',name='GOOGLE_SHEETS_READONLY',created=0)
    monkeypatch.setattr(responder,'_scoped',lambda:(current[0],source,facts,'2026-10',['DC PHÚ THỌ'],CUTOFF,[]))
    try:
        first=responder._get_agent()
        assert Decimal(first._context['metrics']['booking.dc_handling_component_vnd']['value'])==Decimal('246.900')
        changed=catalog();changed['version']='synthetic-v2';changed['rates'][0]['vnd_per_kg']='300'
        current[0]=parsed(c=changed)
        second=responder._get_agent()
        assert second is not first
        assert Decimal(second._context['metrics']['booking.dc_handling_component_vnd']['value'])==Decimal('370.300')
        assert second._context['source_provenance']['handling_catalog_sha256']==catalog_digest(changed)
    finally:state.close()
