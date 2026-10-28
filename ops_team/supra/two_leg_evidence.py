"""Conservative shared resolution of an owner-approved direct-GXT leg split."""
from collections import defaultdict
from .booking_vehicles import classify,day,plate


def direct_gxt_resolver(data,evidence,policy=None):
    orders=defaultdict(list);mappings=defaultdict(list);by_vehicle=defaultdict(list);groups={}
    catalog=data.vehicle_pair['vehicle_catalog']
    for row in data.rows.to_dict('records'):
        if row['order_id']:orders[row['order_id']].append(row)
    for m in evidence.get('mappings',[]):
        mappings[m['order']].append(m)
        by_vehicle[(str(m['day']),m['raw_plate'])].append(m)
    for g in evidence.get('groups',[]):groups[(g['day'],g['plate'])]=g
    cache={}
    def resolve(order,booking_day):
        key=(order,str(booking_day))
        if key in cache:return cache[key]
        result='UNKNOWN';d=day(booking_day);everything=orders.get(order,[])
        # Event scope (owner grain GHN + Booking E day): a GHN booked on several days is
        # resolved per day; its Raw legs are those loaded from E until its next E day.
        rows=[r for r in everything if day(r['dispatch_date'])==d]
        if not rows or d is None:cache[key]=result;return result
        vehicles={plate(r.get('source_booking_plate','')) for r in rows}
        if len(vehicles)!=1 or '' in vehicles or any(classify(r,catalog,d)!='GXT_DIRECT' for r in rows):
            cache[key]=result;return result
        later=sorted({x for r in everything if (x:=day(r['dispatch_date'])) is not None and x>d})
        until=str(later[0]) if later else None
        ag=next(iter(vehicles))
        observed=[m for m in mappings.get(order,[]) if m['day'] is not None and str(m['day'])>=str(d) and (until is None or str(m['day'])<until)]
        locations={(str(m['day']),m['raw_plate']) for m in observed}
        if not observed or len(locations)!=1:
            cache[key]=result;return result
        raw_day,raw_plate=next(iter(locations));group=groups.get((raw_day,raw_plate))
        if (not raw_plate or raw_day<str(d) or not group or group.get('share') is None or
                any(m['mode']!='GXT_DIRECT' or set(m['flags'])-{'direct_vehicle_mismatch','multiple_booking_vehicles'} or ag not in m['booking_plates'] for m in observed) or
                any(m['mode'] not in ('GXT_DIRECT','TRANSFER_GXT') or 'unknown_or_conflicting_mode' in m['flags'] for m in by_vehicle[(raw_day,raw_plate)])):
            cache[key]=result;return result
        if policy is not None:
            from .manual_cost_report import _rule
            gxt_rules=set(policy['selectors']['gxt'])
            for mapping in by_vehicle[(raw_day,raw_plate)]:
                related=orders.get(mapping['order'],[])
                if not related or any(not (route:=_rule(row,catalog,day(raw_day))) or route['rule_id'] not in gxt_rules for row in related):
                    cache[key]=result;return result
        result='DIRECT_SAME_VEHICLE' if raw_plate==ag else 'TWO_LEGS'
        cache[key]=result;return result
    return resolve
