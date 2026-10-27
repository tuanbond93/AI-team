"""Protected owner allocation and pre-VAT revenue policy, manual views only."""
import hashlib
import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from .telegram_secrets import load_protected, save_protected
from .telegram_manual_cost_policy import FILENAME as BASE_FILENAME, validate_policy
from .telegram_vehicle_forecast import FILENAME as FORECAST_FILENAME
from .supra.booking_handling import geography
from .supra.manual_cost_rules import _number

SCHEMA = 'supra-manual-revenue-allocation-v1'
SCHEMA_V2 = 'supra-manual-revenue-allocation-v2'
EXCLUSION_CATEGORIES = frozenset({'WAREHOUSE_RENT','ELECTRICITY_WATER','VEHICLE_WAIT','OTHER_OUT_OF_PACKAGE'})
FILENAME = 'manual-revenue-allocation.dpapi'


def validate_overlay(config, base, base_raw, forecast_raw, data, source, facts, owner_id, paired):
    try:
        keys = {'schema','base_policy_sha256','forecast_sha256','upstream_ratio','warehouse_ratio','revenue','provenance'}
        if not isinstance(config,dict) or config.get('schema') not in (SCHEMA,SCHEMA_V2):
            raise ValueError
        if config['schema']==SCHEMA_V2:keys.add('scope_exclusions')
        if set(config)!=keys:raise ValueError
        validate_policy(base,data,source,facts,owner_id,paired)
        if config['base_policy_sha256']!=hashlib.sha256(base_raw).hexdigest() or config['forecast_sha256']!=hashlib.sha256(forecast_raw).hexdigest() or config['forecast_sha256']!=base['forecast_sha256']:
            raise ValueError
        if json.loads(forecast_raw)!=base['forecast_binding']:
            raise ValueError
        for name in ('upstream_ratio','warehouse_ratio'):
            value=_number(config[name])
            if value>1:raise ValueError
        revenue=config['revenue']
        if not isinstance(revenue,dict) or set(revenue)!={'unit','vat','prices','price_source_sha256'} or revenue['unit']!='VND_PER_PRODUCT_UNIT' or revenue['vat']!='EXCLUDED':raise ValueError
        if not isinstance(revenue['price_source_sha256'],str) or not re.fullmatch('[a-f0-9]{64}',revenue['price_source_sha256']):raise ValueError
        prices=revenue['prices']
        if not isinstance(prices,list) or len(prices)!=4:raise ValueError
        intervals={}
        period=base['forecast_binding']['period']
        for item in prices:
            if not isinstance(item,dict) or set(item)!={'province','from','to','vnd_per_unit'}:raise ValueError
            if not isinstance(item['province'],str):raise ValueError
            province=geography(item['province'],province=True)
            if not province or len(province)>100:raise ValueError
            first=date.fromisoformat(item['from']);last=date.fromisoformat(item['to'])
            if first>last or first.strftime('%Y-%m')!=period or last.strftime('%Y-%m')!=period:raise ValueError
            _number(item['vnd_per_unit'],positive=True)
            if any(first<=end and start<=last for start,end in intervals.get(province,[])):raise ValueError
            intervals.setdefault(province,[]).append((first,last))
        proof=config['provenance']
        if not isinstance(proof,dict) or set(proof)!={'source','confirmed_date','evidence_sha256'} or proof['source']!='USER_CONFIRMED_IN_CODEX':raise ValueError
        date.fromisoformat(proof['confirmed_date'])
        if not isinstance(proof['evidence_sha256'],str) or not re.fullmatch('[a-f0-9]{64}',proof['evidence_sha256']):raise ValueError
        if config['schema']==SCHEMA_V2:
            exclusion=config['scope_exclusions']
            if not isinstance(exclusion,dict) or set(exclusion)!={'categories','window','source','confirmed_date','evidence_sha256'}:raise ValueError
            cats=exclusion['categories']
            if not isinstance(cats,list) or not cats or any(not isinstance(x,str) or x not in EXCLUSION_CATEGORIES for x in cats) or len(cats)!=len(set(cats)):raise ValueError
            window=exclusion['window'];bound=base['forecast_binding']['window']
            if not isinstance(window,dict) or set(window)!={'start','end'}:raise ValueError
            start=date.fromisoformat(window['start']);end=date.fromisoformat(window['end'])
            if not date.fromisoformat(bound['start'])<=start<=end<=date.fromisoformat(bound['end']):raise ValueError
            if exclusion['source']!='USER_CONFIRMED_IN_CODEX':raise ValueError
            date.fromisoformat(exclusion['confirmed_date'])
            if not isinstance(exclusion['evidence_sha256'],str) or not re.fullmatch('[a-f0-9]{64}',exclusion['evidence_sha256']):raise ValueError
        return config
    except (ValueError,TypeError,KeyError,AttributeError,OverflowError):
        raise ValueError('Chính sách doanh thu/phân bổ không khớp owner, nguồn hoặc kỳ đã duyệt.') from None


def ingest_overlay(path,descriptor,state,owner_id,*,cancel=None):
    def stop():
        if cancel is not None and cancel.is_set():raise ValueError('Đã hủy; cấu hình trước giữ nguyên.')
    stop();p=Path(path)
    if not p.is_absolute() or p.suffix.lower()!='.dpapi' or p.stat().st_size>1024*1024:raise ValueError
    raw=load_protected(p);sha=hashlib.sha256(raw).hexdigest()
    if not isinstance(descriptor,dict) or set(descriptor)!={'schema','sha256'} or descriptor['schema'] not in (SCHEMA,SCHEMA_V2) or descriptor['sha256']!=sha:raise ValueError
    config=json.loads(raw);base_raw=load_protected(state.root/BASE_FILENAME);forecast_raw=load_protected(state.root/FORECAST_FILENAME)
    if config.get('schema')!=descriptor['schema']:raise ValueError
    base=json.loads(base_raw);facts={k:r['value'] for k,r in state.facts().items()}
    from .telegram_snapshot import load_snapshot
    data,source=load_snapshot(state,facts)
    validate_overlay(config,base,base_raw,forecast_raw,data,source,facts,owner_id,state.control('paired'))
    stop();save_protected(state.root/FILENAME,raw)
    return {'state':'SAVED_MANUAL_ONLY','overlay_sha256':sha,'plan_or_schedule_changed':False}


def main():
    import argparse,sys
    from .telegram_secrets import load_settings
    from .telegram_state import BusinessState
    state=None
    try:
        parser=argparse.ArgumentParser();parser.add_argument('--minimized',required=True);args=parser.parse_args()
        descriptor=sys.stdin.buffer.read(1024*1024+1)
        if len(descriptor)>1024*1024:raise ValueError
        settings=load_settings();state=BusinessState()
        print(json.dumps(ingest_overlay(args.minimized,json.loads(descriptor),state,settings['owner_id'])));return 0
    except Exception:
        print('{"state":"REJECTED","reason":"Kiểm tra chính sách, nguồn và owner; cấu hình trước giữ nguyên."}');return 1
    finally:
        if state:state.close()


if __name__=='__main__':
    import sys
    sys.exit(main())
