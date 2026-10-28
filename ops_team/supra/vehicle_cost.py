"""Scoped DC handling evidence for vehicle views; no vehicle-cost allocation."""
from collections import defaultdict
from datetime import datetime,time
from zoneinfo import ZoneInfo


def raw_deliver_kg(pair):
    """Owner rule 27/10/2026: kg per (GHN, Raw load day) from DELIVER grams/1000.

    Only an unambiguous total is returned: every row valid, no repeated
    trip/plate key. It fills an event whose Booking L is blank on all rows.
    """
    from decimal import Decimal
    from .booking_vehicles import RAW_HEADERS,day,plate
    from .booking_quantity import business_key,cell
    from .booking_weight import parse_kg
    found=defaultdict(list)
    for row in pair['data_supra']['rows']:
        r=dict(zip(RAW_HEADERS,row['values']))
        if cell(r['type']).upper()!='DELIVER':continue
        order=business_key(r['order_code']);d=day(r['load_date'])
        if not order or d is None:continue
        found[(order,str(d))].append((r,f"Raw#{pair['raw_digest'][:12]} dòng {row['sheet_row']}"))
    result={}
    for key,items in found.items():
        weights=[parse_kg(r['weight']) for r,_ in items]
        trips=[(business_key(r['trip_code']),plate(r['truck_plate_number'])) for r,_ in items]
        if any(status!='VALID' for _,status in weights) or len(set(trips))!=len(trips):continue
        result[key]=(sum((kg for kg,_ in weights),Decimal(0))/Decimal(1000),[ref for _,ref in items])
    return result


def vehicle_cost_by_day(data,evidence,text,rules,policy=None):
    """Owner-confirmed GHN/date events, with source-bound split exceptions."""
    from decimal import Decimal,localcontext
    from .booking_vehicles import day,validate_vehicle_catalog
    from .booking_handling import HANDLING_POLICY,_route,validate_catalog
    from .booking_weight import WEIGHT_RULE,parse_kg
    from .vehicle_report import vehicle_scope
    if not evidence.get('available') or not getattr(data,'vehicle_pair',None):return {'available':False,'reason':'NO_PAIRED_BOOKING'}
    pair=data.vehicle_pair
    if pair['booking_digest']!=evidence['booking_digest'] or pair['raw_digest']!=evidence['raw_digest'] or pair['vehicle_catalog_sha256']!=evidence['catalog_sha256']:raise ValueError
    scope=vehicle_scope(evidence,text)
    if scope['aliases']:return {'available':False,'reason':'VEHICLE_ALLOCATION_NOT_AVAILABLE','scope':scope}
    if not scope['start'] or not scope['end']:return {'available':False,'reason':'NO_DATE_RANGE','scope':scope}
    catalog=data.handling_catalog
    if not catalog or data.handling_policy!=HANDLING_POLICY or data.weight_rules!=WEIGHT_RULE:return {'available':False,'reason':'NO_APPROVED_HANDLING_CONFIG','scope':scope}
    fee_pin=catalog['source_pin'];validate_vehicle_catalog(pair['vehicle_catalog'],fee_pin,pair['data_supra'],evidence['catalog_sha256']);validate_catalog(catalog,fee_pin,data.handling_policy,data.handling_catalog_sha256)
    from .booking_vehicles import classify
    from .two_leg_evidence import direct_gxt_resolver
    resolve_direct=direct_gxt_resolver(data,evidence,policy)
    start,end=scope['start'],scope['end'];events=defaultdict(list);unassigned=[];scoped_rows=0;missing_ids=0;undated=0
    for r in data.rows.to_dict('records'):
        d=day(r['dispatch_date']);oid=r['order_id']
        if d is None:
            unassigned.append(r['_source']);undated+=1;continue
        if not start<=str(d)<=end:continue
        scoped_rows+=1
        if not oid:unassigned.append(r['_source']);missing_ids+=1;continue
        events[(oid,str(d))].append(r)
    exception={(x['order_id'],x['day']):x for x in rules['handling_parts']}
    counters={k:0 for k in ('partial_scope','unknown_routing','mixed_routing','unknown_rate','mixed_rate','missing_weight','invalid_weight','conflicting_weight','two_leg_unresolved')}
    buckets={r['rate_id']:dict(rate_vnd_per_kg=r['vnd_per_kg'],effective_from=r['from'],effective_to=r['to'],eligible_ghn=0,eligible_kg=Decimal(0),eligible_vnd=Decimal(0),sources=[]) for r in catalog['rates']}
    held=[];eligible=[];direct=0;issuesrc=[];raw_kg=raw_deliver_kg(pair);raw_filled=0
    with localcontext() as ctx:
        ctx.prec=140
        for key,rows in sorted(events.items()):
            refs=[r['_source'] for r in rows];modes={_route(r,catalog) for r in rows};issues=[]
            if None in modes:issues.append('unknown_routing')
            if len(modes-{None})>1:issues.append('mixed_routing')
            physical={classify(r,pair['vehicle_catalog'],day(key[1])) for r in rows}
            if physical=={'GXT_DIRECT'}:
                resolution=resolve_direct(key[0],key[1])
                if resolution=='TWO_LEGS':modes={'TRANSFER'}
                elif resolution!='DIRECT_SAME_VEHICLE':issues.append('two_leg_unresolved')
            if not issues and modes=={'DIRECT'}:
                direct+=1;continue
            matches=[r for r in catalog['rates'] if r['from']<=key[1]<=r['to']]
            if len(matches)!=1:issues.append('unknown_rate')
            weights=[parse_kg(r['source_ghn_weight']) for r in rows];values={v for v,s in weights if s=='VALID'}
            if any(s=='INVALID' for v,s in weights):issues.append('invalid_weight')
            override=exception.get(key);filled=None
            if not values and all(s=='MISSING' for v,s in weights) and not override and key in raw_kg:
                filled=raw_kg[key];values={filled[0]}
            if not values and 'invalid_weight' not in issues:issues.append('missing_weight')
            if len(values)>1 and not override:issues.append('conflicting_weight')
            if override and {r['_source'] for r,(v,s) in zip(rows,weights) if s=='VALID'}!=set(override['sources']):issues.append('conflicting_weight')
            if issues:
                held.append(dict(order_id=key[0],day=key[1],issues=sorted(set(issues)),sources=refs));issuesrc+=refs
                for flag in set(issues):counters[flag]+=1
                continue
            kg=sum(values,Decimal(0)) if override else next(iter(values))
            rate=matches[0];b=buckets[rate['rate_id']];amount=kg*Decimal(rate['vnd_per_kg'])
            b['eligible_ghn']+=1;b['eligible_kg']+=kg;b['eligible_vnd']+=amount;b['sources']+=refs
            if filled:raw_filled+=1;b['sources']+=filled[1]
            eligible.append(dict(order_id=key[0],day=key[1],sources=refs+(filled[1] if filled else []),kg=str(kg),amount_vnd=str(amount),rate_id=rate['rate_id'],
                weight_source='RAW_DELIVER_SAME_DAY' if filled else 'BOOKING_L'))
        accepted=[b for b in buckets.values() if b['eligible_ghn']]
        known=bool(accepted) or bool(direct and not held and not unassigned)
        total=str(sum((b['eligible_vnd'] for b in accepted),Decimal(0))) if known else None
        weight=str(sum((b['eligible_kg'] for b in accepted),Decimal(0))) if known else None
    complete=bool(scoped_rows) and not held and not unassigned
    return dict(available=True,complete=complete,component_vnd=total if complete else None,eligible_vnd=total,eligible_kg=weight,
        eligible_transfer_ghn=len(eligible),direct_excluded_ghn=direct,unresolved_ghn=len(held),missing_id_rows=missing_ids,unscoped_dates=undated,
        counters=counters,rate_buckets=[{**b,'eligible_kg':str(b['eligible_kg']) if b['eligible_ghn'] else None,'eligible_vnd':str(b['eligible_vnd']) if b['eligible_ghn'] else None} for b in buckets.values()],
        issue_sources=issuesrc+unassigned,sources_by_metric={},group_evidence=held,eligible_events=eligible,scope=scope,booking_rows=scoped_rows,
        boundary_ghn=0,booking_digest=pair['booking_digest'],handling_catalog_sha256=data.handling_catalog_sha256,total_cost_vnd=None,gross_profit_vnd=None,
        reason='OWNER_TERMS_COMPLETE' if complete else 'PARTIAL_EVENT_NOT_WHOLE_SCOPE',grain='GHN_BOOKING_E_DAY',raw_weight_filled_events=raw_filled)


def vehicle_cost_status(data, evidence, text=''):
    """Reuse the approved handling engine, holding GHNs that cross either boundary."""
    from .booking_vehicles import day,validate_vehicle_catalog
    from .booking_handling import handling_summary
    from .vehicle_report import vehicle_scope
    if not evidence.get('available') or not getattr(data,'vehicle_pair',None):
        return {'available':False,'reason':'NO_PAIRED_BOOKING'}
    pair=data.vehicle_pair
    if (pair['booking_digest']!=evidence['booking_digest'] or pair['raw_digest']!=evidence['raw_digest']
            or pair['vehicle_catalog_sha256']!=evidence['catalog_sha256']):
        raise ValueError('Nguồn xe và Booking không cùng phiên bản; chưa tính bốc xếp.')
    scope=vehicle_scope(evidence,text)
    if scope['aliases']:
        return {'available':False,'reason':'VEHICLE_ALLOCATION_NOT_AVAILABLE','scope':scope}
    if not scope['start'] or not scope['end']:
        return {'available':False,'reason':'NO_DATE_RANGE','scope':scope}
    catalog=getattr(data,'handling_catalog',None)
    if catalog is None:
        return {'available':False,'reason':'NO_APPROVED_HANDLING_CONFIG','scope':scope}
    pin=pair['vehicle_catalog']['source_pins']['booking'];fee_pin=catalog.get('source_pin',{})
    if set(pin)!={'spreadsheet_id','tab','gid'} or set(fee_pin)!=set(pin) or any(str(pin[k])!=str(fee_pin[k]) for k in pin):
        raise ValueError('Catalog bốc xếp không cùng nguồn Booking đã duyệt.')
    validate_vehicle_catalog(pair['vehicle_catalog'],fee_pin,pair['data_supra'],evidence['catalog_sha256'])
    start,end=day(scope['start']),day(scope['end'])
    def in_range(d):
        return d is not None and d==d and start<=d<=end
    scoped=data.rows.loc[data.rows.dispatch_date.map(in_range)]
    ids=set(scoped.order_id)-{''}
    whole=defaultdict(list)
    for row in data.rows.loc[data.rows.order_id.isin(ids)].to_dict('records'):
        whole[row['order_id']].append(row)
    boundary={key for key,rows in whole.items() if any(not in_range(row['dispatch_date']) for row in rows)}
    eligible=scoped.loc[~scoped.order_id.isin(boundary)]
    # The original engine sees only complete GHNs, but still checks their full
    # records, approved rates and owner policy without any changed dates/kg.
    cutoff=datetime.combine(end,time(23,59),ZoneInfo('Asia/Ho_Chi_Minh'))
    full_check=handling_summary(data,scoped,evidence['period'],cutoff)
    result=handling_summary(data,eligible,evidence['period'],cutoff) if boundary and full_check['available'] else full_check
    if not result['available']:
        return {'available':False,'reason':result['reason'],'scope':scope}
    prior={item['order_id']:item for item in full_check['group_evidence']}
    for key in sorted(boundary):
        refs=[row['_source'] for row in whole[key]]
        issues=set(prior.get(key,{}).get('issues',[]))|{'partial_scope'}
        result['issue_sources'].extend(refs)
        result['group_evidence'].append({'order_id':key,'issues':sorted(issues),'sources':refs})
        for issue in issues:result['counters'][issue]+=1
    if boundary:
        result['unresolved_ghn']+=len(boundary)
        result['complete']=False
        result['component_vnd']=None
        result['reason']='PARTIAL_COMPONENT_NOT_WHOLE_SCOPE'
    result.update(scope=scope,booking_rows=len(scoped),boundary_ghn=len(boundary),
        booking_digest=pair['booking_digest'],handling_catalog_sha256=data.handling_catalog_sha256,
        total_cost_vnd=None,gross_profit_vnd=None)
    return result


def booking_rule_handling(data,evidence,period,cutoff,rules,rules_sha,policy):
    """Rule-layer DC result for the whole booking scope (period start..cutoff).

    Used by scheduled/model booking contexts so they match /chiphi and /xe.
    A scope outside the approved rule window holds the component as N/A
    instead of falling back to the legacy GHN-wide grain.
    """
    from calendar import monthrange
    from datetime import date
    from .booking_handling import HANDLING_POLICY,catalog_digest
    first=date.fromisoformat(period+'-01')
    last=min(date(first.year,first.month,monthrange(first.year,first.month)[1]),cutoff.date())
    text=f'{first} {last}'
    result=vehicle_cost_by_day(data,evidence,text,rules,policy)
    if not result.get('available'):return None
    catalog=data.handling_catalog
    result.update(policy_version=HANDLING_POLICY['version'],catalog_version=catalog['version'],catalog_sha256=catalog_digest(catalog),
        verification='OWNER_TERMS_CALCULATION_NOT_SETTLED_COST',rules_sha256=rules_sha,rules_window=dict(rules['window']))
    if not rules['window']['start']<=str(first)<=str(last)<=rules['window']['end']:
        result.update(complete=False,component_vnd=None,eligible_vnd=None,eligible_kg=None,eligible_transfer_ghn=None,direct_excluded_ghn=None,
            unresolved_ghn=None,reason='OWNER_RULE_WINDOW_NOT_COVERING_SCOPE',
            rate_buckets=[{**b,'eligible_kg':None,'eligible_vnd':None,'eligible_ghn':0} for b in result['rate_buckets']])
    return result
