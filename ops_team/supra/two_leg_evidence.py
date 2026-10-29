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
    cache={};reasons={};splits={}
    def done(key,result,reason=None):
        cache[key]=result
        if reason:reasons[key]=reason
        return result
    def resolve(order,booking_day):
        key=(order,str(booking_day))
        if key in cache:return cache[key]
        d=day(booking_day);everything=orders.get(order,[])
        # Event scope (owner grain GHN + Booking E day): a GHN booked on several days is
        # resolved per day; its Raw legs are those loaded from E until its next E day.
        rows=[r for r in everything if day(r['dispatch_date'])==d]
        if not rows or d is None:return done(key,'UNKNOWN','no_booking_rows')
        vehicles={plate(r.get('source_booking_plate','')) for r in rows}
        if len(vehicles)!=1 or '' in vehicles:return done(key,'UNKNOWN','booking_ag_missing_or_several')
        if any(classify(r,catalog,d)!='GXT_DIRECT' for r in rows):return done(key,'UNKNOWN','booking_mixed_mode')
        later=sorted({x for r in everything if (x:=day(r['dispatch_date'])) is not None and x>d})
        until=str(later[0]) if later else None
        ag=next(iter(vehicles))
        observed=[m for m in mappings.get(order,[]) if m['day'] is not None and str(m['day'])>=str(d) and (until is None or str(m['day'])<until)]
        if observed and all(m.get('unassigned_direct') for m in observed):return done(key,'DIRECT_SAME_VEHICLE')
        if not observed:
            # Raw is a separate snapshot: a Booking day at/after the last Raw load day is not yet covered.
            # Delivery may take up to D+2 (owner SLA), so a missing S leg is only a data gap once
            # Raw covers E+2; before that the event is waiting for Raw.
            from datetime import timedelta
            last=day(evidence.get('raw_last_day'))
            return done(key,'UNKNOWN','raw_not_yet_updated' if last and last<d+timedelta(days=2) else 'no_raw_deliver_in_window')
        observed=[m for m in observed if not m.get('unassigned_direct')]
        locations={(str(m['day']),m['raw_plate']) for m in observed}
        if any(not raw_plate for _,raw_plate in locations):return done(key,'UNKNOWN','raw_plate_missing')
        if any(m['mode']!='GXT_DIRECT' or set(m['flags'])-{'direct_vehicle_mismatch','multiple_booking_vehicles'} or ag not in m['booking_plates'] for m in observed):
            return done(key,'UNKNOWN','raw_mapping_flags')
        # Owner C4 (28/10/2026): one event may be delivered by several S trucks/days; each S
        # truck/day must pass the same checks, and all must agree (all S != AG, or all S == AG).
        for raw_day,raw_plate in sorted(locations):
            group=groups.get((raw_day,raw_plate))
            # A repeated row (failed attempt redelivered) does not make the truck/day unusable (owner 29/10/2026).
            if not group or set(group.get('issues',[]))-{'duplicate_event_key'}:return done(key,'UNKNOWN','raw_truck_day_data_issue')
            if any(m['mode'] not in ('GXT_DIRECT','TRANSFER_GXT') or 'unknown_or_conflicting_mode' in m['flags'] for m in by_vehicle[(raw_day,raw_plate)]):
                return done(key,'UNKNOWN','s_truck_shared_with_other_mode')
            if policy is not None:
                from .manual_cost_report import _rule
                gxt_rules=set(policy['selectors']['gxt'])
                for mapping in by_vehicle[(raw_day,raw_plate)]:
                    related=orders.get(mapping['order'],[])
                    if not related or any(not (route:=_rule(row,catalog,day(raw_day))) or route['rule_id'] not in gxt_rules for row in related):
                        return done(key,'UNKNOWN','s_truck_route_outside_gxt_policy')
        same={raw_plate==ag for _,raw_plate in locations}
        if same=={True}:return done(key,'DIRECT_SAME_VEHICLE')
        if same=={False}:return done(key,'TWO_LEGS')
        # Owner 29/10/2026: part on the AG truck (direct), part on other S trucks (two legs):
        # split L by the event's Raw DELIVER grams per side; any unreadable gram holds the event.
        from decimal import Decimal
        from .booking_vehicles import RAW_HEADERS
        from .booking_quantity import business_key,cell
        from .booking_weight import parse_kg
        sides={True:Decimal(0),False:Decimal(0)}
        for item in data.vehicle_pair['data_supra']['rows']:
            r=dict(zip(RAW_HEADERS,item['values']))
            load=day(r['load_date'])
            if (cell(r['type']).upper()!='DELIVER' or business_key(r['order_code'])!=order or load is None or str(load)<str(d)
                    or (until is not None and str(load)>=until) or not plate(r['truck_plate_number'])):continue
            grams,status=parse_kg(r['weight'])
            if status!='VALID':return done(key,'UNKNOWN','mixed_ag_and_other_s_trucks')
            sides[plate(r['truck_plate_number'])==ag]+=grams
        total=sides[True]+sides[False]
        if not sides[False] or not total:return done(key,'UNKNOWN','mixed_ag_and_other_s_trucks')
        splits[key]=sides[False]/total
        return done(key,'SPLIT')
    resolve.reasons=reasons
    resolve.splits=splits
    return resolve
