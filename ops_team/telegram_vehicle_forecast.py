"""Owner-confirmed, manual-only shift scenario; no model or scheduled output."""
from collections import defaultdict
from datetime import date
from decimal import Decimal,localcontext
from pathlib import Path
import hashlib,json,re,sys

from .telegram_state import GROUP_ID
from .telegram_secrets import load_protected,save_protected
from .supra.booking_vehicles import RAW_PIN,plate,capacity_kg,classify
from .supra.booking_handling import geography,validate_catalog

SCHEMA='supra-shift-forecast-owner-v1'
MULTI_SCHEMA='supra-shift-forecast-owner-v2'
FILENAME='manual-shift-forecast.dpapi'


def _pin(value,expected):
    return (isinstance(value,dict) and set(value)=={'spreadsheet_id','tab','gid','range'} and
            all(str(value[k])==str(expected[k]) for k in value))


def validate_forecast(config,data,source,facts,owner_id,paired):
    """Validate owner/group, both pinned sources, catalogs, routes and explicit class basis."""
    try:
        keys={'schema','version','owner_id','group_id','booking_pin','raw_pin','period','window',
            'vehicle_catalog_sha256','handling_catalog_sha256','route_selector','vehicle','time','rate','inclusions','provenance'}
        if not isinstance(config,dict) or config.get('schema') not in (SCHEMA,MULTI_SCHEMA):raise ValueError
        if config['schema']==MULTI_SCHEMA:keys=(keys-{'vehicle','time','rate','inclusions'})|{'vehicle','time','tariffs'}
        if set(config)!=keys:raise ValueError
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',config['version']):raise ValueError
        if (type(owner_id) is not int or type(config['owner_id']) is not int or config['owner_id']!=owner_id or
                type(config['group_id']) is not int or config['group_id']!=GROUP_ID or not isinstance(paired,dict) or
                paired.get('owner_id')!=owner_id or paired.get('chat_id')!=GROUP_ID):raise ValueError
        pair=data.vehicle_pair
        if not pair or facts.get('source_id') not in ('snapshot','browser_snapshot'):raise ValueError
        ranges=json.loads(facts['sheets_ranges'])
        if set(ranges)!={'supra_booking'} or source['tab']!=facts['sheets_tab'] or str(source['gid'])!=str(facts['sheets_gid']) or source['ranges']!=[ranges['supra_booking']]:raise ValueError
        booking={'spreadsheet_id':facts['sheets_id'],'tab':source['tab'],'gid':source['gid'],'range':source['ranges'][0]}
        raw={k:pair['data_supra'][k] for k in ('spreadsheet_id','tab','gid','range')}
        if not _pin(config['booking_pin'],booking) or not _pin(config['raw_pin'],raw):raise ValueError
        if any(str(raw[k])!=str(RAW_PIN[k]) for k in RAW_PIN):raise ValueError
        c=pair['vehicle_catalog'];h=data.handling_catalog
        if (config['vehicle_catalog_sha256']!=pair['vehicle_catalog_sha256'] or
                config['handling_catalog_sha256']!=data.handling_catalog_sha256 or not h):raise ValueError
        from .supra.booking_vehicles import validate_vehicle_catalog
        validate_vehicle_catalog(c,booking,raw,config['vehicle_catalog_sha256'])
        validate_catalog(h,booking,data.handling_policy,config['handling_catalog_sha256'])
        from .supra.booking_handling import period_floor
        if (c['period']!=h['period'] or not re.fullmatch(r'20[0-9]{2}-(?:0[1-9]|1[0-2])',config['period'])
                or not str(period_floor(c['period']))[:7]<=config['period']<=c['period']):raise ValueError
        w=config['window']
        if not isinstance(w,dict) or set(w)!={'start','end'}:raise ValueError
        start,end=date.fromisoformat(w['start']),date.fromisoformat(w['end'])
        if start>end or start.strftime('%Y-%m')!=config['period'] or end.strftime('%Y-%m')!=config['period']:raise ValueError
        sel=config['route_selector']
        if not isinstance(sel,dict) or set(sel)!={'scope','routes'} or sel['scope']!='DISTRICT' or not isinstance(sel['routes'],list) or len(sel['routes'])!=2:raise ValueError
        by_id={r['rule_id']:r for r in c['routing']};ids=set();provinces=set();districts=set()
        for item in sel['routes']:
            if not isinstance(item,dict) or set(item)!={'rule_id','province','district'} or item['rule_id'] in ids:raise ValueError
            r=by_id[item['rule_id']];ids.add(item['rule_id'])
            p=geography(item['province'],province=True);d=geography(item['district'])
            if (not p or not d or r['scope']!='DISTRICT' or r['mode']!='TRANSFER_GXT' or
                    geography(r['province'],province=True)!=p or geography(r['district'])!=d or
                    not (r['from']<=w['start']<=w['end']<=r['to'])):raise ValueError
            provinces.add(p);districts.add(d)
        if len(provinces)!=1 or len(districts)!=2:raise ValueError
        vehicle=config['vehicle'];timing=config['time']
        if config['schema']==MULTI_SCHEMA:
            if vehicle!={'source':'BOOKING_AG','class_basis':'OWNER_CONFIRMED_BOOKING_T_AG'} or timing!={'source':'BOOKING_E','counting':'DISTINCT_DATE_AG'}:raise ValueError
            tariffs=config['tariffs'];seen=set()
            if not isinstance(tariffs,list) or not 1<=len(tariffs)<=20:raise ValueError
            for t in tariffs:
                if not isinstance(t,dict) or set(t)!={'declared_class_kg','units_per_vehicle_day','shift_hours','rate','inclusions'}:raise ValueError
                cls=t['declared_class_kg'];units=t['units_per_vehicle_day'];rate=t['rate'];inc=t['inclusions']
                if not isinstance(cls,str) or not re.fullmatch(r'[1-9][0-9]{0,6}',cls) or cls in seen:raise ValueError
                seen.add(cls)
                if not isinstance(units,str) or not re.fullmatch(r'(?:[1-9]|1[0-9]|2[0-4])',units):raise ValueError
                if not isinstance(rate,dict) or set(rate)!={'amount_vnd','currency','unit'} or rate['currency']!='VND' or rate['unit'] not in ('PER_SHIFT','PER_TRIP') or not isinstance(rate['amount_vnd'],str) or not re.fullmatch(r'[1-9][0-9]{0,14}',rate['amount_vnd']):raise ValueError
                if rate['unit']=='PER_SHIFT':
                    if not isinstance(t['shift_hours'],str) or not re.fullmatch(r'(?:[1-9]|1[0-9]|2[0-4])',t['shift_hours']):raise ValueError
                elif t['shift_hours'] is not None:raise ValueError
                if not isinstance(inc,dict) or set(inc)!={'fuel','driver_wage','tolls','vat','dc_handling'} or any(v not in ('INCLUDED','EXCLUDED','UNKNOWN') for v in inc.values()):raise ValueError
        else:
            rate=config['rate']
            _validate_v1(vehicle,timing,rate,config['inclusions'])
        prov=config['provenance']
        if not isinstance(prov,dict) or set(prov)!={'source','confirmed_date'} or prov['source']!='USER_CONFIRMED_IN_CODEX':raise ValueError
        date.fromisoformat(prov['confirmed_date'])
        return config
    except (ValueError,TypeError,KeyError,AttributeError,OverflowError):
        raise ValueError('Kịch bản ca xe chưa khớp owner, nguồn, catalog, phạm vi, tuyến hoặc căn cứ tải trọng đã chốt.') from None


def _validate_v1(vehicle,timing,rate,inclusions):
        if (not isinstance(vehicle,dict) or set(vehicle)!={'source','declared_class_kg','class_basis'} or vehicle['source']!='BOOKING_AG' or
                vehicle['class_basis']!='OWNER_CONFIRMED_BOOKING_T_AG' or
                not isinstance(vehicle['declared_class_kg'],str) or not re.fullmatch(r'[1-9][0-9]{0,6}',vehicle['declared_class_kg'])):raise ValueError
        if (not isinstance(timing,dict) or set(timing)!={'source','counting','shifts_per_vehicle_day','shift_hours'} or
                timing['source']!='BOOKING_E' or timing['counting']!='DISTINCT_DATE_AG' or
                not isinstance(timing['shifts_per_vehicle_day'],str) or not re.fullmatch(r'(?:[1-9]|1[0-9]|2[0-4])',timing['shifts_per_vehicle_day']) or
                not isinstance(timing['shift_hours'],str) or not re.fullmatch(r'(?:[1-9]|1[0-9]|2[0-4])',timing['shift_hours'])):raise ValueError
        if (not isinstance(rate,dict) or set(rate)!={'amount_vnd','currency','unit'} or rate['currency']!='VND' or rate['unit']!='PER_SHIFT' or
                not isinstance(rate['amount_vnd'],str) or not re.fullmatch(r'[1-9][0-9]{0,14}',rate['amount_vnd'])):raise ValueError
        if inclusions!={'fuel':'UNKNOWN','driver_wage':'UNKNOWN','tolls':'UNKNOWN','vat':'UNKNOWN'}:raise ValueError


def ingest_forecast(path,descriptor,state,owner_id,*,cancel=None):
    def stop():
        if cancel is not None and cancel.is_set():raise ValueError('Đã hủy; kịch bản trước giữ nguyên.')
    stop();p=Path(path)
    if not p.is_absolute() or p.suffix.lower()!='.dpapi' or p.stat().st_size>1024*1024:raise ValueError('Cần file DPAPI kịch bản tuyệt đối, tối đa 1 MB.')
    raw=load_protected(p);sha=hashlib.sha256(raw).hexdigest()
    if not isinstance(descriptor,dict) or set(descriptor)!={'schema','sha256'} or descriptor['schema'] not in (SCHEMA,MULTI_SCHEMA) or descriptor['sha256']!=sha:raise ValueError('Descriptor/hash kịch bản sai.')
    config=json.loads(raw)
    if config.get('schema')!=descriptor['schema']:raise ValueError('Schema không khớp.')
    facts={k:r['value'] for k,r in state.facts().items()}
    from .telegram_snapshot import load_snapshot
    data,source=load_snapshot(state,facts)
    validate_forecast(config,data,source,facts,owner_id,state.control('paired'))
    stop();save_protected(state.root/FILENAME,raw)
    return {'state':'SAVED_MANUAL_ONLY','scenario_sha256':sha,'plan_or_schedule_changed':False}


def _calculate(data,evidence,scope,config,sha,rules=None):
    from .supra.booking_vehicles import day
    multi=config['schema']==MULTI_SCHEMA
    tariffs=(config['tariffs'] if multi else [{'declared_class_kg':config['vehicle']['declared_class_kg'],
        'units_per_vehicle_day':config['time']['shifts_per_vehicle_day'],'shift_hours':config['time']['shift_hours'],
        'rate':config['rate'],'inclusions':config['inclusions']}])
    by_class={Decimal(t['declared_class_kg']):t for t in tariffs}
    start,end=scope['start'],scope['end'];w=config['window']
    if scope['aliases']:return {'available':False,'reason':'VEHICLE_ALLOCATION_NOT_AVAILABLE','scenario_sha256':sha}
    if not start or not end:return {'available':False,'reason':'NO_DATE_RANGE','scenario_sha256':sha}
    if start<w['start'] or end>w['end']:return {'available':False,'reason':'OUTSIDE_APPROVED_WINDOW','scenario_sha256':sha}
    c=data.vehicle_pair['vehicle_catalog'];representatives={plate(x) for x in c['representative_plates']}
    selected={r['rule_id'] for r in config['route_selector']['routes']}
    route_rows=[r for r in c['routing'] if r['rule_id'] in selected]
    routes={(geography(r['province'],province=True),geography(r['district'])) for r in route_rows}
    provinces={p for p,_ in routes};rows=[];by_order=defaultdict(list);by_asset=defaultdict(list);undated_sources=[]
    for row in data.rows.to_dict('records'):
        d=row['dispatch_date']
        if d is None or d!=d:
            p=geography(row.get('source_province',''),province=True);district=geography(row.get('source_district',''))
            cap=capacity_kg(row.get('source_capacity_raw',''))
            if ((p,district) in routes or not p or (p in provinces and not district)) and (multi or cap is None or Decimal(cap) in by_class):
                undated_sources.append(row['_source'])
            continue
        if not (start<=str(d)<=end):continue
        row['forecast_date']=str(d);row['forecast_plate']=plate(row.get('source_booking_plate',''))
        row['forecast_geo']=(geography(row.get('source_province',''),province=True),geography(row.get('source_district','')))
        rows.append(row)
        if row['order_id']:by_order[row['order_id']].append(row)
        if row['forecast_plate'] and row['forecast_plate'] not in representatives:by_asset[(str(d),row['forecast_plate'])].append(row)
    groups=defaultdict(list);unassigned=[]
    def active_override(r):
        sid=r.get('source_store_id','');d=r['forecast_date']
        return bool(sid and any(x['scope']=='STORE' and x['store_id']==sid and x['from']<=d<=x['to'] for x in c['routing']))
    def selected_route(r):
        return (r['forecast_geo'] in routes and not active_override(r) and
                classify(r,c,day(r['forecast_date']))=='TRANSFER_GXT')
    for row in rows:
        p,d=row['forecast_geo'];is_candidate=(p,d) in routes
        # Missing geography may conceal an eligible route; preserve it as unassigned.
        unknown_geo=not p or (p in provinces and not d)
        if not is_candidate and not unknown_geo:continue
        pl=row['forecast_plate']
        if not row['order_id'] or not pl or pl in representatives:
            unassigned.append(row['_source']);continue
        issues=set()
        if unknown_geo or not selected_route(row):issues.add('ambiguous_route')
        related=by_order[row['order_id']]
        plates={r['forecast_plate'] for r in related if r['forecast_plate'] and r['forecast_plate'] not in representatives}
        if len(plates)!=1 or any(not r['forecast_plate'] or r['forecast_plate'] in representatives for r in related):issues.add('multiple_or_missing_vehicles')
        if any(not selected_route(r) for r in related):issues.add('mixed_or_unknown_route')
        groups[(row['forecast_date'],pl)].append((row,issues))
    aliases={x['plate']:x['alias'] for x in evidence['inventory']}
    eligible=[];held=[];other=[];class_audit={'missing_or_invalid_t':0,'mixed_t_groups':0};counts=defaultdict(int)
    overrides={(x['day'],plate(x['vehicle'])):x for x in rules['booked_trips']} if rules else {}
    for key,items in sorted(groups.items()):
        rs=[r for r,_ in items];issues={flag for _,flags in items for flag in flags}
        capacities=[capacity_kg(r.get('source_capacity_raw','')) for r in rs]
        parsed={Decimal(x) for x in capacities if x is not None}
        warnings=[]
        if any(not selected_route(r) for r in by_asset[key]):warnings.append('same_vehicle_day_other_route_records')
        if len({r['order_id'] for r in rs if r['order_id']})>1:warnings.append('multiple_bookings_one_vehicle_day')
        if any(x is None for x in capacities):class_audit['missing_or_invalid_t']+=1
        if len(parsed)>1:class_audit['mixed_t_groups']+=1
        override=overrides.get(key)
        if override and (set(override['sources'])!={r['_source'] for r in rs} or {Decimal(t['class_kg']) for t in override['trips']}!=parsed or any(Decimal(t['class_kg']) not in by_class for t in override['trips'])):
            override=None
        # Owner rule 27/10/2026 (active with the rule layer): several booked T on one vehicle/day
        # are one trip per distinct T, each at its own tariff; an unpriced T holds the group.
        per_class=bool(rules) and not override and len(parsed)>1 and not any(x is None for x in capacities)
        if per_class and not parsed<=set(by_class):issues.add('unpriced_class')
        if any(x is None for x in capacities) or (len(parsed)!=1 and not override and not per_class):issues.add('unknown_or_mixed_class')
        elif not override and next(iter(parsed)) not in by_class:
            other.append(key);continue
        cls=str(next(iter(parsed))) if len(parsed)==1 and not any(x is None for x in capacities) else None
        record={'date':key[0],'vehicle_alias':aliases.get(key[1]),'issues':sorted(issues),'warnings':warnings,'sources':sorted({r['_source'] for r in rs}),'declared_class_kg':cls}
        (held if issues else eligible).append(record)
        if override:record['warnings'].append('owner_confirmed_booked_trips')
        if per_class:record['warnings'].append('owner_rule_one_trip_per_booked_class')
        if not issues:
            if per_class:
                for klass in parsed:counts[klass]+=1
            elif override:
                for trip in override['trips']:counts[Decimal(trip['class_kg'])]+=trip['units']
            else:counts[Decimal(cls)]+=1
    with localcontext() as ctx:
        ctx.prec=100
        breakdown=[dict(declared_class_kg=t['declared_class_kg'],vehicle_days=counts[Decimal(t['declared_class_kg'])],
            units_per_vehicle_day=t['units_per_vehicle_day'],shift_hours=t['shift_hours'],rate_vnd=t['rate']['amount_vnd'],unit=t['rate']['unit'],
            inclusions=dict(t['inclusions']),amount_vnd=str(Decimal(counts[Decimal(t['declared_class_kg'])])*Decimal(t['units_per_vehicle_day'])*Decimal(t['rate']['amount_vnd']))) for t in tariffs]
        amount=sum((Decimal(t['amount_vnd']) for t in breakdown),Decimal(0))
    partial=bool(held or unassigned or undated_sources or (multi and other))
    subtotal=str(amount) if eligible or (groups and not partial) else None
    return dict(available=True,scope=scope,scenario_sha256=sha,booking_digest=data.vehicle_pair['booking_digest'],raw_digest=data.vehicle_pair['raw_digest'],
        vehicle_catalog_sha256=data.vehicle_pair['vehicle_catalog_sha256'],handling_catalog_sha256=data.handling_catalog_sha256,
        rate_vnd=tariffs[0]['rate']['amount_vnd'],shift_hours=tariffs[0]['shift_hours'],shifts_per_vehicle_day=tariffs[0]['units_per_vehicle_day'],
        declared_class_kg=tariffs[0]['declared_class_kg'],class_basis=config['vehicle']['class_basis'],inclusions=dict(tariffs[0]['inclusions']),
        tariff_breakdown=breakdown if multi else None,
        owner_confirmed_booked_trips=bool(rules and any('owner_confirmed_booked_trips' in r['warnings'] and not r['issues'] for r in eligible)),
        booked_class_basis='BOOKED_TARIFF_CLASS' if rules else None,
        eligible_vehicle_days=len(eligible),held_vehicle_days=len(held),unassigned_rows=len(unassigned),excluded_other_class_days=len(other),
        undated_unscoped=bool(undated_sources),warning_vehicle_days=sum(bool(r['warnings']) for r in eligible+held),
        eligible_pool_vnd=subtotal,full_pool_vnd=None if partial else subtotal,total_cost_vnd=None,gross_profit_vnd=None,
        class_audit=class_audit,group_evidence=eligible+held,unassigned_sources=unassigned,undated_sources=undated_sources)


def manual_forecast(state,data,evidence,source,facts,owner_id,text='',*,rules=None):
    from .supra.vehicle_report import vehicle_scope
    path=state.root/FILENAME
    if not path.exists():return {'available':False,'reason':'NOT_CONFIGURED'}
    try:
        raw=load_protected(path);sha=hashlib.sha256(raw).hexdigest();config=json.loads(raw)
        validate_forecast(config,data,source,facts,owner_id,state.control('paired'))
        if (data.vehicle_pair['booking_digest']!=evidence['booking_digest'] or data.vehicle_pair['raw_digest']!=evidence['raw_digest'] or
                data.vehicle_pair['vehicle_catalog_sha256']!=evidence['catalog_sha256']):raise ValueError
        return _calculate(data,evidence,vehicle_scope(evidence,text),config,sha,rules)
    except (ValueError,TypeError,KeyError,AttributeError,OverflowError,OSError):
        return {'available':False,'reason':'SCENARIO_NOT_VALID_FOR_CURRENT_SOURCE'}


def main():
    import argparse
    from .telegram_secrets import load_settings
    from .telegram_state import BusinessState
    state=None
    try:
        parser=argparse.ArgumentParser(description='Protected manual shift forecast; descriptor on stdin')
        parser.add_argument('--minimized',required=True);args=parser.parse_args()
        descriptor=sys.stdin.buffer.read(1024*1024+1)
        if len(descriptor)>1024*1024:raise ValueError
        settings=load_settings();state=BusinessState()
        result=ingest_forecast(args.minimized,json.loads(descriptor),state,settings['owner_id'])
        print(json.dumps(result));return 0
    except Exception:
        print('{"state":"REJECTED","reason":"Check approved owner/group, source, catalogs, class basis and descriptor; prior scenario is unchanged."}')
        return 1
    finally:
        if state:state.close()


if __name__=='__main__':sys.exit(main())
