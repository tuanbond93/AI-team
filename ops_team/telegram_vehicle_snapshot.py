"""Atomic paired business-source import; no settings/credentials/network access."""
from pathlib import Path
from datetime import datetime
import json,re,hashlib,time
import pandas as pd
from .telegram_secrets import load_protected,save_protected
from .telegram_snapshot import assert_pin
from .supra.booking import parse_booking
from .supra.booking_quantity import RULE
from .supra.booking_weight import WEIGHT_RULE
from .supra.booking_handling import HANDLING_POLICY,validate_catalog,geography
from .supra.booking_vehicles import (PAIR_SCHEMA,BOOK_HEADERS,BOOK_HEADERS_V2,RAW_HEADERS,RAW_PIN,VEHICLE_POLICY,
    digest,validate_vehicle_catalog)


def _read_record(state):
    p=state.root/'snapshot-latest.dpapi'
    if not p.exists():return None
    sha=json.loads(load_protected(p))['digest']
    if not re.fullmatch('[a-f0-9]{64}',sha):raise ValueError('Pointer paired source sai.')
    raw=load_protected(state.root/'snapshots'/(sha+'.dpapi'))
    if hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('Paired snapshot đổi hash.')
    return json.loads(raw)


def validate_pair(pair,source):
    if not isinstance(pair,dict) or set(pair)!={'schema','booking_digest','booking_retrieved_at','data_supra','raw_digest','vehicle_policy','vehicle_catalog','vehicle_catalog_sha256'}:
        raise ValueError('Paired source schema sai.')
    if pair['schema']!=PAIR_SCHEMA or pair['vehicle_policy']!=VEHICLE_POLICY:raise ValueError('Paired vehicle policy sai.')
    if pair['booking_retrieved_at']!=source['retrieved_at']:raise ValueError('Paired Booking timestamp không khớp nguồn.')
    raw=pair['data_supra']
    _source(raw,RAW_HEADERS,25,raw=True)
    if digest(raw)!=pair['raw_digest'] or not re.fullmatch('[a-f0-9]{64}',pair['booking_digest']):raise ValueError('Paired digest sai.')
    if not isinstance(pair['vehicle_catalog_sha256'],str) or not re.fullmatch('[a-f0-9]{64}',pair['vehicle_catalog_sha256']):raise ValueError('Thiếu vehicle catalog hash.')
    validate_vehicle_catalog(pair['vehicle_catalog'],source,raw,pair['vehicle_catalog_sha256'])
    return pair


def active_vehicle_pair(state,source):
    r=_read_record(state)
    if r is None or not r.get('vehicle_pair'):return None
    if any(str(r['source'][k])!=str(source[k]) for k in ('spreadsheet_id','tab','gid','range')):return None
    return validate_pair(r['vehicle_pair'],r['source'])


def vehicle_plan_identity(state):
    r=_read_record(state)
    if not r or not r.get('vehicle_pair'):return None
    pair=validate_pair(r['vehicle_pair'],r['source']);raw=pair['data_supra']
    return dict(raw_pin={k:raw[k] for k in ('spreadsheet_id','tab','gid','range')},
        policy_version=VEHICLE_POLICY['version'],catalog_sha256=pair['vehicle_catalog_sha256'])


def _source(s,headers,count,*,raw=False):
    keys={'source_url','spreadsheet_id','tab','gid','range','retrieved_at','raw_sha256','raw_column_count','headers','rows'}
    allowed=headers if isinstance(headers,tuple) else (headers,)
    if not isinstance(s,dict) or set(s)!=keys or s['headers'] not in allowed or s['raw_column_count']!=count:raise ValueError('Nguồn paired sai header/schema.')
    if raw and (any(str(s[k])!=str(RAW_PIN[k]) for k in RAW_PIN) or not re.fullmatch(r"'raw_current'!A1:Y[1-9][0-9]*",s['range'])):raise ValueError('Raw pin/range ngoài nguồn được owner cho phép.')
    if not re.fullmatch('[a-f0-9]{64}',s['raw_sha256']):raise ValueError('Nguồn paired thiếu raw hash.')
    from urllib.parse import urlparse,parse_qs
    u=urlparse(s['source_url']);gids=parse_qs(u.query).get('gid',[])+parse_qs(u.fragment).get('gid',[])
    if u.scheme!='https' or u.hostname!='docs.google.com' or ('/d/'+s['spreadsheet_id']+'/') not in u.path or not gids or any(g!=str(s['gid']) for g in gids):raise ValueError('URL paired không khớp source.')
    t=datetime.fromisoformat(s['retrieved_at'])
    if t.tzinfo is None or t.timestamp()>time.time()+60:raise ValueError('Thời điểm nhận paired sai; không sửa timestamp.')
    end=int(re.search(r'([0-9]+)$',s['range'])[1])
    if end>100000 or not isinstance(s['rows'],list) or len(s['rows'])>100000:raise ValueError('Nguồn paired vượt giới hạn.')
    positions=[]
    for r in s['rows']:
        if (not isinstance(r,dict) or set(r)!={'sheet_row','values'} or type(r['sheet_row']) is not int or not 2<=r['sheet_row']<=end or
                not isinstance(r['values'],list) or len(r['values'])!=len(s['headers']) or any(type(v) not in (str,int,float,type(None)) for v in r['values'])):raise ValueError('Dòng paired sai cấu trúc.')
        positions.append(r['sheet_row'])
    if positions!=sorted(set(positions)):raise ValueError('Vị trí paired phải duy nhất, tăng dần.')
    return t


def ingest_vehicle_pair(path,descriptor,state,owner_id,*,cancel=None):
    def stop():
        if cancel is not None and cancel.is_set():raise ValueError('Đã hủy paired import; pointer giữ nguyên.')
    stop();p=Path(path)
    if not p.is_absolute() or p.suffix.lower()!='.dpapi' or p.stat().st_size>40*1024*1024:raise ValueError('Cần DPAPI paired business path tuyệt đối, tối đa40MB.')
    b=load_protected(p)
    dk={'schema','booking','data_supra','confirmed_business_rules','confirmed_weight_rules','confirmed_handling_policy',
        'handling_catalog_sha256','handling_catalog_version','vehicle_policy','vehicle_catalog_sha256','vehicle_catalog_version','minimized_plaintext_sha256'}
    if not isinstance(descriptor,dict) or set(descriptor)!=dk or hashlib.sha256(b).hexdigest()!=descriptor['minimized_plaintext_sha256']:raise ValueError('Paired descriptor/hash sai.')
    payload=json.loads(b)
    pk=dk-{'minimized_plaintext_sha256','handling_catalog_version','vehicle_catalog_version'}|{'handling_catalog','vehicle_catalog'}
    if not isinstance(payload,dict) or set(payload)!=pk or payload['schema']!=PAIR_SCHEMA or descriptor['schema']!=PAIR_SCHEMA:raise ValueError('Paired payload schema sai.')
    expected={'confirmed_business_rules':{k:v for k,v in RULE.items() if k!='version'},
        'confirmed_weight_rules':{k:v for k,v in WEIGHT_RULE.items() if k!='version'},
        'confirmed_handling_policy':HANDLING_POLICY,'vehicle_policy':VEHICLE_POLICY}
    for key,val in expected.items():
        if payload[key]!=val or descriptor[key]!=val:raise ValueError('Paired policy không khớp owner.')
    for key,headers,count in (('booking',(BOOK_HEADERS,BOOK_HEADERS_V2),37),('data_supra',RAW_HEADERS,25)):
        s=payload[key];_source(s,headers,count,raw=key=='data_supra')
        if descriptor[key]!={k:v for k,v in s.items() if k!='rows'}|{'row_count':len(s['rows'])}:raise ValueError('Paired source metadata không khớp descriptor.')
    book=payload['booking'];raw=payload['data_supra'];facts={k:r['value'] for k,r in state.facts().items()}
    assert_pin(book,facts)
    from .supra.booking_quantity import owner_quantity_rule
    if owner_quantity_rule(book)!=RULE:raise ValueError('Primary paired ngoài owner pin.')
    for key,validator,args in (('handling_catalog',validate_catalog,(book,HANDLING_POLICY)),('vehicle_catalog',validate_vehicle_catalog,(book,raw))):
        c=payload[key];sha=payload[key+'_sha256']
        if not isinstance(c,dict) or c.get('version')!=descriptor[key+'_version'] or sha!=descriptor[key+'_sha256'] or not isinstance(sha,str) or not re.fullmatch('[a-f0-9]{64}',sha):raise ValueError('Catalog paired version/hash sai.')
        validator(c,*args,sha) if key=='vehicle_catalog' else validator(c,*args,expected_digest=sha)
    # Physical vehicle rules cannot silently remove the prior fee exception.
    for v in payload['vehicle_catalog']['routing']:
        matches=[h for h in payload['handling_catalog']['routing'] if h['scope']==v['scope'] and h['store_id']==v['store_id'] and
            geography(h['province'],province=True)==geography(v['province'],province=True) and geography(h['district'])==geography(v['district']) and h['from']==v['from'] and h['to']==v['to']]
        if len(matches)!=1 or (matches[0]['routing']=='TRANSFER')!=v['handling_eligible']:raise ValueError('Vehicle mode không được thay điều kiện bốc xếp đã duyệt.')
    previous=_read_record(state)
    stamp=datetime.fromisoformat(book['retrieved_at'])
    if previous:
        if stamp<datetime.fromisoformat(previous['source']['retrieved_at']):raise ValueError('Không thay primary mới bằng paired cũ.')
        if previous.get('vehicle_pair') and datetime.fromisoformat(raw['retrieved_at'])<datetime.fromisoformat(previous['vehicle_pair']['data_supra']['retrieved_at']):raise ValueError('Không thay raw mới bằng paired cũ.')
    f=pd.DataFrame([r['values'] for r in book['rows']],columns=book['headers'],dtype=object)
    f['_source']=[f"GoogleViewer#{book['raw_sha256'][:12]} dòng {r['sheet_row']}" for r in book['rows']]
    safe=parse_booking(f,quantity_rules=dict(RULE),weight_rules=dict(WEIGHT_RULE),handling_policy=HANDLING_POLICY,
        handling_catalog=payload['handling_catalog'],handling_catalog_sha256=payload['handling_catalog_sha256'],vehicle_fields=True)
    source={k:v for k,v in book.items() if k not in ('rows','headers','raw_column_count','raw_sha256')};source['channel']='GOOGLE_BROWSER_VIEWER_EXPORT'
    pair=dict(schema=PAIR_SCHEMA,booking_digest=digest(book),booking_retrieved_at=book['retrieved_at'],data_supra=raw,raw_digest=digest(raw),
        vehicle_policy=VEHICLE_POLICY,vehicle_catalog=payload['vehicle_catalog'],vehicle_catalog_sha256=payload['vehicle_catalog_sha256'])
    record=dict(schema_version=5,source=source,actor=owner_id,provenance='OWNER_PAIRED_BUSINESS_SOURCES / UNVERIFIED',
        quantity_rules=RULE,weight_rules=WEIGHT_RULE,handling_policy=HANDLING_POLICY,handling_catalog=payload['handling_catalog'],
        handling_catalog_sha256=payload['handling_catalog_sha256'],vehicle_pair=pair,rows=safe.rows.to_dict('records'),
        business_evidence={'headers':book['headers'],'rows':book['rows']},export={'sha256':book['raw_sha256'],'observed_data_rows':len(book['rows'])})
    encoded=json.dumps(record,ensure_ascii=False,default=str,allow_nan=False).encode();sha=hashlib.sha256(encoded).hexdigest()
    stop();save_protected(state.root/'snapshots'/(sha+'.dpapi'),encoded)
    stop();save_protected(state.root/'snapshot-latest.dpapi',json.dumps({'digest':sha,'retrieved_epoch':stamp.timestamp()}).encode())
    return {'state':'INGESTED_NOT_SCHEDULED','digest':sha,'booking_rows':len(book['rows']),'raw_rows':len(raw['rows']),
        'raw_digest':pair['raw_digest'],'vehicle_catalog_sha256':pair['vehicle_catalog_sha256'],'requires_plan_review':True}
