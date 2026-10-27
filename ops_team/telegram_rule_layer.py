"""Optional owner-confirmed, source-bound manual rules; never schedules work."""
import hashlib,json,re
from pathlib import Path
from datetime import date
from .telegram_secrets import load_protected,save_protected
from .telegram_manual_cost_policy import FILENAME as BASE_FILENAME,validate_policy
from .telegram_vehicle_forecast import FILENAME as FORECAST_FILENAME
from .telegram_cost_overlay import FILENAME as OVERLAY_FILENAME,validate_overlay
from .supra.booking_vehicles import plate,capacity_kg,RAW_HEADERS,day,classify
from .supra.booking_quantity import business_key,cell
from .supra.booking_handling import geography
from .supra.manual_cost_rules import _number
from .supra.booking_weight import parse_kg

SCHEMA='supra-owner-rule-exceptions-v1'
FILENAME='manual-owner-rules.dpapi'


def validate_rules(config,state,data,source,facts,owner_id):
    try:
        required={'schema','base_sha256','forecast_sha256','overlay_sha256','booking_digest','raw_digest','vehicle_catalog_sha256','handling_catalog_sha256','window','direct_gxt_two_legs','booked_class_basis','handling_parts','booked_trips','opening_ftl','provenance'}
        if not isinstance(config,dict) or set(config)!=required or config['schema']!=SCHEMA:raise ValueError
        base_raw=load_protected(state.root/BASE_FILENAME);forecast_raw=load_protected(state.root/FORECAST_FILENAME);overlay_raw=load_protected(state.root/OVERLAY_FILENAME)
        for field,raw in (('base_sha256',base_raw),('forecast_sha256',forecast_raw),('overlay_sha256',overlay_raw)):
            if config[field]!=hashlib.sha256(raw).hexdigest():raise ValueError
        base=json.loads(base_raw);overlay=json.loads(overlay_raw)
        validate_policy(base,data,source,facts,owner_id,state.control('paired'))
        validate_overlay(overlay,base,base_raw,forecast_raw,data,source,facts,owner_id,state.control('paired'))
        pair=data.vehicle_pair
        for field,value in (('booking_digest',pair['booking_digest']),('raw_digest',pair['raw_digest']),('vehicle_catalog_sha256',pair['vehicle_catalog_sha256']),('handling_catalog_sha256',data.handling_catalog_sha256)):
            if config[field]!=value:raise ValueError
        w=config['window'];bound=base['forecast_binding']['window']
        if not isinstance(w,dict) or set(w)!={'start','end'} or not date.fromisoformat(bound['start'])<=date.fromisoformat(w['start'])<=date.fromisoformat(w['end'])<=date.fromisoformat(bound['end']):raise ValueError
        if config['direct_gxt_two_legs'] is not True or config['booked_class_basis']!='BOOKED_TARIFF_CLASS':raise ValueError
        proof=config['provenance']
        if not isinstance(proof,dict) or set(proof)!={'source','confirmed_date','evidence_sha256'} or proof['source']!='USER_CONFIRMED_IN_CODEX' or not re.fullmatch('[a-f0-9]{64}',proof['evidence_sha256']):raise ValueError
        date.fromisoformat(proof['confirmed_date'])
        rows=data.rows.to_dict('records');by_ref={r['_source']:r for r in rows}
        if len(by_ref)!=len(rows):raise ValueError
        seen=set()
        for item in config['handling_parts']:
            if not isinstance(item,dict) or set(item)!={'order_id','day','sources'} or not isinstance(item['sources'],list) or len(item['sources'])<2 or len(set(item['sources']))!=len(item['sources']):raise ValueError
            key=(item['order_id'],item['day'])
            if key in seen or not w['start']<=item['day']<=w['end']:raise ValueError
            seen.add(key);related=[r for r in rows if r['order_id']==key[0] and str(r['dispatch_date'])==key[1]]
            values=[parse_kg(r['source_ghn_weight']) for r in related]
            nonblank={r['_source'] for r,(value,status) in zip(related,values) if status!='MISSING'}
            if nonblank!=set(item['sources']) or any(status=='INVALID' for value,status in values) or len({value for value,status in values if status=='VALID'})<2:raise ValueError
        seen=set()
        for item in config['booked_trips']:
            if not isinstance(item,dict) or set(item)!={'day','vehicle','sources','trips'} or not isinstance(item['sources'],list) or len(set(item['sources']))!=len(item['sources']) or not item['sources']:raise ValueError
            key=(item['day'],plate(item['vehicle']))
            if not key[1] or key in seen or not w['start']<=key[0]<=w['end']:raise ValueError
            routes={(geography(r['province'],province=True),geography(r['district'])) for r in base['forecast_binding']['route_selector']['routes']}
            seen.add(key);related=[r for r in rows if str(r['dispatch_date'])==key[0] and plate(r.get('source_booking_plate',''))==key[1] and
                (geography(r.get('source_province',''),province=True),geography(r.get('source_district',''))) in routes and classify(r,pair['vehicle_catalog'],day(key[0]))=='TRANSFER_GXT']
            if {r['_source'] for r in related}!=set(item['sources']):raise ValueError
            actual={capacity_kg(r.get('source_capacity_raw','')) for r in related}
            trips=item['trips']
            if None in actual or not isinstance(trips,list) or len(trips)!=2:raise ValueError
            classes=set()
            for trip in trips:
                if not isinstance(trip,dict) or set(trip)!={'class_kg','units'} or type(trip['units']) is not int or trip['units']!=1 or trip['class_kg'] in classes:raise ValueError
                _number(trip['class_kg'],positive=True)
                classes.add(trip['class_kg'])
            from decimal import Decimal
            if len({Decimal(x) for x in classes})!=2 or {Decimal(x) for x in actual}!={Decimal(x) for x in classes}:raise ValueError
            allowed={Decimal(t['declared_class_kg']) for t in base['forecast_binding']['tariffs']}
            if not {Decimal(x) for x in classes}<=allowed:raise ValueError
        seen=set()
        for item in config['opening_ftl']:
            if not isinstance(item,dict) or set(item)!={'order_id','day','booking_sources','raw_sources','class_kg','pricing'} or item['pricing']!='ACTUAL_KM_UNPRICED':raise ValueError
            key=(item['order_id'],item['day'])
            if key in seen or not w['start']<=key[1]<=w['end']:raise ValueError
            seen.add(key);related=[r for r in rows if r['order_id']==key[0] and str(r['dispatch_date'])==key[1]]
            vehicles={plate(r.get('source_booking_plate','')) for r in related}
            if not related or len(vehicles)!=1 or '' in vehicles or set(item['booking_sources'])!={r['_source'] for r in related} or any(capacity_kg(r.get('source_capacity_raw',''))!=item['class_kg'] for r in related):raise ValueError
            from .supra.manual_cost_report import _rule
            if any(not (route:=_rule(row,pair['vehicle_catalog'])) or route['rule_id'] not in base['selectors']['point_bc'] for row in related):raise ValueError
            raw_refs=set()
            for row in pair['data_supra']['rows']:
                values=dict(zip(RAW_HEADERS,row['values']))
                if business_key(values['order_code'])==key[0] and str(day(values['load_date']))==key[1] and cell(values['type']).upper()=='DELIVER':
                    raw_refs.add(f"Raw#{pair['raw_digest'][:12]} dòng {row['sheet_row']}")
            if not isinstance(item['raw_sources'],list) or not item['raw_sources'] or len(set(item['raw_sources']))!=len(item['raw_sources']) or set(item['raw_sources'])!=raw_refs:raise ValueError
        if any(not isinstance(config[k],list) for k in ('handling_parts','booked_trips','opening_ftl')):raise ValueError
        return config
    except (ValueError,TypeError,KeyError,AttributeError,OSError,OverflowError):
        raise ValueError('Quy tắc nguồn hoặc ngoại lệ chưa khớp owner, dữ liệu và cấu hình đã duyệt.') from None


def ingest_rules(path,descriptor,state,owner_id,*,cancel=None):
    def stop():
        if cancel is not None and cancel.is_set():raise ValueError('Đã hủy; quy tắc trước giữ nguyên.')
    stop();p=Path(path)
    if not p.is_absolute() or p.suffix.lower()!='.dpapi' or p.stat().st_size>1024*1024:raise ValueError
    raw=load_protected(p);sha=hashlib.sha256(raw).hexdigest()
    if not isinstance(descriptor,dict) or set(descriptor)!={'schema','sha256'} or descriptor['schema']!=SCHEMA or descriptor['sha256']!=sha:raise ValueError
    from .telegram_snapshot import load_snapshot
    facts={k:r['value'] for k,r in state.facts().items()};data,source=load_snapshot(state,facts)
    validate_rules(json.loads(raw),state,data,source,facts,owner_id)
    stop();save_protected(state.root/FILENAME,raw)
    return {'state':'SAVED_MANUAL_ONLY','rules_sha256':sha,'plan_or_schedule_changed':False}


def load_rules(state,data,source,facts,owner_id):
    path=state.root/FILENAME
    if not path.exists():return None,None
    raw=load_protected(path);rules=json.loads(raw)
    validate_rules(rules,state,data,source,facts,owner_id)
    return rules,hashlib.sha256(raw).hexdigest()


def main():
    import argparse,sys
    from .telegram_secrets import load_settings
    from .telegram_state import BusinessState
    state=None
    try:
        parser=argparse.ArgumentParser();parser.add_argument('--minimized',required=True);args=parser.parse_args()
        desc=sys.stdin.buffer.read(1024*1024+1)
        if len(desc)>1024*1024:raise ValueError
        state=BusinessState();settings=load_settings()
        print(json.dumps(ingest_rules(args.minimized,json.loads(desc),state,settings['owner_id'])));return 0
    except Exception:
        print('{"state":"REJECTED","reason":"Kiểm tra owner, nguồn và quy tắc; cấu hình trước giữ nguyên."}');return 1
    finally:
        if state:state.close()


if __name__=='__main__':
    import sys
    sys.exit(main())
