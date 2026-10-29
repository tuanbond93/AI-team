"""Manual cost report, calculated only from owner-bound paired evidence."""
from collections import defaultdict
from decimal import Decimal, localcontext
import hashlib
import json

from .booking_handling import geography
from .booking_vehicles import day, plate, capacity_kg, RAW_HEADERS
from .booking_quantity import cell, business_key
from .booking_weight import parse_kg
from .manual_cost_rules import warehouse_labor_pool, delivery_points, ftl_base_trips, gxt_daily_cost
from .vehicle_report import vehicle_scope, vehicle_scope_quality
from .vehicle_cost import vehicle_cost_status
from ..telegram_secrets import load_protected
from ..telegram_manual_cost_policy import FILENAME, validate_policy
from ..telegram_vehicle_forecast import FILENAME as FORECAST_FILENAME, manual_forecast


def _rule(row, catalog, when=None):
    d = when if when is not None else row.get('dispatch_date')
    if d is None or d != d:
        return None
    active = [r for r in catalog['routing'] if r['from'] <= str(d) <= r['to']]
    province = geography(row.get('source_province', ''), province=True)
    district = geography(row.get('source_district', ''))
    store = row.get('source_store_id', '')
    matching_store = [r for r in active if r['scope'] == 'STORE' and r['store_id'] == store and store]
    if matching_store:
        matches = [r for r in matching_store if geography(r['province'], province=True) == province and geography(r['district']) == district]
        return matches[0] if len(matches) == len(matching_store) == 1 else None
    if not store and any(r['scope'] == 'STORE' and geography(r['province'], province=True) == province and geography(r['district']) == district for r in active):
        return None
    matches = [r for r in active if r['scope'] == 'DISTRICT' and geography(r['province'], province=True) == province and geography(r['district']) == district]
    return matches[0] if len(matches) == 1 else None


def _mixed_supra_truck(group, group_rows, mappings, orders, category, latest_deliver):
    """Owner 29/10/2026: one S truck/day may carry Supra orders of several models.

    GXT-model orders share the truck cost by their own kg and get the delivery
    wage; Supra orders of other models (BC/FTL/NCC/opening) are paid by their own
    model, so their kg counts with the other goods. An order redelivered after a
    failed attempt counts once, on its latest Raw load day; repeated rows of the
    same trip count once. Only unreadable grams or Raw row errors hold the group.
    """
    gday = day(group['day'])
    selected = [mappings[ref] for _, ref in group_rows if ref in mappings]
    if not selected:
        return None
    flags = set()
    if set(group.get('issues', [])) - {'duplicate_event_key'}:
        flags.add('raw_group_quality')
    supra, other, seen, warnings = {}, Decimal(group['other_kg']), set(), 0
    for raw_row, ref in group_rows:
        if ref not in mappings:
            continue
        order = business_key(raw_row['order_code'])
        if not order or latest_deliver.get(order) != gday:
            continue
        trip = business_key(raw_row['trip_code'])
        if (order, trip) in seen:
            continue
        seen.add((order, trip))
        grams, status = parse_kg(raw_row['weight'])
        if status != 'VALID':
            flags.add('invalid_weight')
            continue
        kg = grams / Decimal(1000)
        dated = [r for r in orders.get(order, []) if r['dispatch_date'] is not None]
        before = [r for r in dated if str(r['dispatch_date']) <= group['day']]
        # The Booking day of this leg: latest E on/before the load day, else (booked after an
        # early delivery) the earliest E.
        pick = max(before, key=lambda r: str(r['dispatch_date'])) if before else min(dated, key=lambda r: str(r['dispatch_date']), default=None)
        cats = {category(r, gday) for r in dated if pick is not None and str(r['dispatch_date']) == str(pick['dispatch_date'])}
        if cats == {'gxt'}:
            supra[order] = str(Decimal(supra.get(order, '0')) + kg)
        else:
            # Other model, mixed or not-yet-priced Supra goods share the truck as other kg;
            # their own pricing gap is reported separately (owner 29/10/2026).
            other += kg
            if not cats or None in cats or len(cats) > 1:
                warnings += 1
    if not supra:
        return None
    return dict(day=group['day'], vehicle=group['plate'], supra_orders=supra, other_kg=str(other), valid=not flags,
                sources=group['sources'], flags=sorted(flags), unpriced_supra_rows=warnings)


def _blank_plate_points(data, rules, start, end, orders):
    """Owner 29/10/2026: Raw DELIVER with no plate in an area with an owner point fee (e.g. one
    district) is paid per point = store x load day, never per DO; rates live in the rule layer."""
    from .booking_handling import district_name
    areas = {(geography(x['province'], province=True), district_name(x['district'])): Decimal(x['vnd_per_point'])
             for x in (rules or {}).get('blank_plate_points', [])}
    if not areas:
        return None
    points, held = {}, []
    for item in data.vehicle_pair['data_supra']['rows']:
        r = dict(zip(RAW_HEADERS, item['values']))
        d = day(r['load_date'])
        if cell(r['type']).upper() != 'DELIVER' or plate(r['truck_plate_number']) or d is None or not start <= str(d) <= end:
            continue
        ref = f"Raw#{data.vehicle_pair['raw_digest'][:12]} dòng {item['sheet_row']}"
        related = [x for x in orders.get(business_key(r['order_code']), []) if x['dispatch_date'] is not None and str(x['dispatch_date']) <= str(d)]
        if not related:
            continue
        latest = max(str(x['dispatch_date']) for x in related)
        event = [x for x in related if str(x['dispatch_date']) == latest]
        keys = {(geography(x['source_province'], province=True), district_name(x['source_district'])) for x in event}
        stores = {x['source_store_id'] for x in event}
        if not keys & set(areas):
            continue
        if len(keys) != 1 or len(stores) != 1 or '' in stores:
            held.append(ref)
            continue
        points.setdefault((str(d), next(iter(stores))), (areas[next(iter(keys))], []))[1].append(ref)
    with localcontext() as ctx:
        ctx.prec = 100
        amount = sum((rate for rate, _ in points.values()), Decimal(0))
    return {'points': len(points), 'eligible_vnd': str(amount) if points else None, 'held_sources': held,
            'sources': [ref for _, refs in points.values() for ref in refs]}


def _split_multi_vehicle_ftl(ftl_rows, pair):
    """Owner rule 28/10/2026: a GHN on several Booking AG vehicles the same day is split by Raw grams.

    Each vehicle gets L x its DELIVER grams / all grams for that GHN and day. Raw
    plates must be exactly the Booking AG plates and every gram value valid;
    otherwise the rows stay unchanged and the group remains held.
    """
    groups = defaultdict(list)
    for row in ftl_rows:
        if row['order'] and row['day']:
            groups[(row['order'], row['day'])].append(row)
    grams = defaultdict(lambda: defaultdict(Decimal))
    bad = set()
    for item in pair['data_supra']['rows']:
        r = dict(zip(RAW_HEADERS, item['values']))
        if cell(r['type']).upper() != 'DELIVER':
            continue
        key = (business_key(r['order_code']), str(day(r['load_date'])))
        if key not in groups:
            continue
        value, status = parse_kg(r['weight'])
        pl = plate(r['truck_plate_number'])
        if status != 'VALID' or not pl or value <= 0:
            bad.add(key)
        else:
            grams[key][pl] += value
    out = []
    for row in ftl_rows:
        key = (row['order'], row['day'])
        related = groups.get(key, [])
        vehicles = {r['vehicle'] for r in related}
        weights = {r['weight_kg'] for r in related if r['weight_status'] == 'VALID'}
        if (len(vehicles) < 2 or '' in vehicles or key in bad or set(grams[key]) != vehicles or len(weights) != 1
                or any(r['weight_status'] == 'INVALID' for r in related)):
            out.append(row)
            continue
        with localcontext() as ctx:
            ctx.prec = 100
            share = Decimal(next(iter(weights))) * grams[key][row['vehicle']] / sum(grams[key].values())
        out.append({**row, 'order': row['order']+'@'+row['vehicle'], 'weight_kg': str(share), 'weight_status': 'VALID'})
    return out


def manual_cost_report(state, data, evidence, source, facts, owner_id, text=''):
    """Bind protected policy and calculate independently priced components."""
    path = state.root / FILENAME
    if not path.exists():
        return {'available': False, 'reason': 'NOT_CONFIGURED'}
    try:
        raw = load_protected(path)
        policy = json.loads(raw)
        forecast_raw = load_protected(state.root / FORECAST_FILENAME)
        if hashlib.sha256(forecast_raw).hexdigest() != policy['forecast_sha256'] or json.loads(forecast_raw) != policy['forecast_binding']:
            raise ValueError
        validate_policy(policy, data, source, facts, owner_id, state.control('paired'))
        if (data.vehicle_pair['booking_digest'] != evidence['booking_digest'] or data.vehicle_pair['raw_digest'] != evidence['raw_digest'] or
                data.vehicle_pair['vehicle_catalog_sha256'] != evidence['catalog_sha256']):
            raise ValueError
        scope = vehicle_scope(evidence, text)
        if scope['aliases'] or not scope['start'] or not scope['end']:
            return {'available': False, 'reason': 'RANGE_REQUIRED'}
        binding = policy['forecast_binding']
        if not binding['window']['start'] <= scope['start'] <= scope['end'] <= binding['window']['end']:
            return {'available': False, 'reason': 'OUTSIDE_POLICY_WINDOW'}
        from ..telegram_rule_layer import load_rules
        rules,rules_sha=load_rules(state,data,source,facts,owner_id)
        if rules and not rules['window']['start']<=scope['start']<=scope['end']<=rules['window']['end']:
            return {'available':False,'reason':'OUTSIDE_RULES_WINDOW'}
        if rules:
            from .vehicle_cost import vehicle_cost_by_day
            cost=vehicle_cost_by_day(data,evidence,text,rules,policy)
            legacy=vehicle_cost_status(data,evidence,text)
        else:cost=vehicle_cost_status(data,evidence,text)
        report=_calculate(data, evidence, policy, scope, hashlib.sha256(raw).hexdigest(),
                          cost,manual_forecast(state, data, evidence, source, facts, owner_id, text,rules=rules),rules,rules_sha)
        if rules:
            report['legacy_dc_warnings']={'counters':legacy.get('counters',{}),'unresolved_ghn':legacy.get('unresolved_ghn'),
                'issue_sources':legacy.get('issue_sources',[])}
        from .manual_cost_overlay import apply_overlay
        return apply_overlay(state,data,evidence,source,facts,owner_id,report)
    except (ValueError, TypeError, KeyError, AttributeError, OSError, OverflowError):
        return {'available': False, 'reason': 'POLICY_OR_SOURCE_MISMATCH'}


def _calculate(data, evidence, policy, scope, sha, dc, upstream, rules=None, rules_sha=None):
    start, end = scope['start'], scope['end']
    catalog = data.vehicle_pair['vehicle_catalog']
    selectors = {k: set(v) for k, v in policy['selectors'].items()}
    rows = data.rows.to_dict('records')
    orders = defaultdict(list)
    for row in rows:
        if row['order_id']:
            orders[row['order_id']].append(row)
    def scoped(r):
        d = r['dispatch_date']
        return d is not None and d == d and start <= str(d) <= end
    def category(r, when=None):
        if rules and (r['order_id'],str(r['dispatch_date'])) in opening:
            return 'opening_ftl'
        rule = _rule(r, catalog, when)
        return next((name for name, ids in selectors.items() if rule and rule['rule_id'] in ids), None)
    opening={(r['order_id'],r['day']):r for r in rules['opening_ftl']} if rules else {}
    if rules:
        # Owner 28/10/2026: any GHN/day whose 'Ghi chú 2' marks an opening (KT/CHKT/khai trương)
        # is FTL by Booking T + actual km; no BC point fee, no GXT delivery wage.
        from .booking_vehicles import is_opening_note
        for r in rows:
            if r['order_id'] and r['dispatch_date'] is not None and is_opening_note(r.get('source_note','')):
                opening.setdefault((r['order_id'],str(r['dispatch_date'])),{'order_id':r['order_id'],'day':str(r['dispatch_date']),'source':'GHI_CHU_2'})
    point_rows, ftl_rows = [], []
    representatives = {plate(x) for x in catalog['representative_plates']}
    for row in rows:
        order = row['order_id']
        current = category(row)
        related = orders.get(order, [])
        event_related=[r for r in related if str(r['dispatch_date'])==str(row['dispatch_date'])] if rules else related
        classes = {category(r) for r in event_related}
        valid_route = bool(order and event_related and all(r['dispatch_date'] is not None and r['dispatch_date'] == r['dispatch_date'] for r in event_related) and len(classes) == 1 and None not in classes)
        ftl_classes={category(r) for r in related}
        valid_ftl = bool(order and related and all(scoped(r) for r in related) and len(ftl_classes)==1 and None not in ftl_classes)
        date_value = str(row['dispatch_date']) if scoped(row) else ''
        if scoped(row) and current in ('point_bc', 'point_lc', 'point_remote'):
            point_rows.append(dict(day=date_value, store=row['source_store_id'], category=current, valid=valid_route, source=row['_source']))
        if current == 'ftl' and (scoped(row) or any(scoped(r) for r in related)):
            parsed, status = parse_kg(row.get('source_ghn_weight', ''))
            vehicle = plate(row.get('source_booking_plate', ''))
            if vehicle in representatives:
                vehicle = ''
            ftl_rows.append(dict(day=date_value, vehicle=vehicle, order=order,
                                 weight_kg=str(parsed) if status == 'VALID' else '', weight_status=status,
                                 class_kg=capacity_kg(row.get('source_capacity_raw', '')) or '', valid=valid_ftl, source=row['_source']))
    if rules:
        ftl_rows = _split_multi_vehicle_ftl(ftl_rows, data.vehicle_pair)
    point_keys = {(r['day'], r['store']) for r in point_rows if r['day'] and r['store']}
    for row in rows:
        if scoped(row) and category(row) not in ('point_bc', 'point_lc', 'point_remote') and (str(row['dispatch_date']), row['source_store_id']) in point_keys:
            point_rows.append(dict(day=str(row['dispatch_date']), store=row['source_store_id'], category='other_or_unknown', valid=False, source=row['_source']))
    rates = policy['rates']
    point_rates={'point_bc': rates['point_bc_vnd'], 'point_lc': rates['point_lc_vnd']}
    if 'point_remote_vnd' in rates:point_rates['point_remote']=rates['point_remote_vnd']
    points = delivery_points(point_rows, point_rates)
    remote=None
    if 'point_remote_vnd' in rates:
        remote_count=points['counts'].get('point_remote',0)
        remote_amount=Decimal(rates['point_remote_vnd'])*remote_count
        remote={'eligible_vnd':str(remote_amount) if remote_count else None,'eligible_points':remote_count,
                'km_extra_vnd':None,'component_vnd':None,'reason':'KM_EVIDENCE_AND_RATE_UNAVAILABLE'}
        if points['eligible_vnd'] is not None:
            rest=Decimal(points['eligible_vnd'])-remote_amount
            points['eligible_vnd']=str(rest) if points['counts'].get('point_bc',0) or points['counts'].get('point_lc',0) else None
    vehicle_classes = defaultdict(list)
    for row in rows:
        raw_class = cell(row.get('source_capacity_raw', ''))
        vehicle = plate(row.get('source_booking_plate', ''))
        if scoped(row) and vehicle and raw_class and (not rules or category(row)=='ftl'):
            vehicle_classes[(str(row['dispatch_date']), vehicle)].append(capacity_kg(raw_class) or '')
    ftl = ftl_base_trips(ftl_rows, rates['ftl_capacity_kg'], rates['ftl_vnd'],
                         ceil_confirmed=policy['ftl_ceil_confirmed'], vehicle_classes=vehicle_classes, tariffs=policy.get('ftl_tariffs'), per_class=bool(rules))
    for row in rows:
        dispatch = row['dispatch_date']
        if dispatch is not None and dispatch == dispatch:
            continue
        cat = category(row, day(start))
        if cat in ('point_bc', 'point_lc', 'point_remote'):
            points['held'].append({'reason': 'unscoped_missing_booking_date', 'sources': [row['_source']]})
            points['complete'] = False
        elif cat == 'ftl':
            ftl['held'].append({'reason': 'unscoped_missing_booking_date', 'sources': [row['_source']]})
            ftl['complete'] = False
    unknown_refs = [r['_source'] for r in rows if scoped(r) and category(r) is None]
    unpriced_routes = defaultdict(lambda: {'orders': set(), 'rows': 0})
    for row in rows:
        if not scoped(row) or category(row) is not None:
            continue
        rule = _rule(row, catalog)
        if rule and rule['mode'] in ('NCC_DIRECT', 'LAI_CHAU_DIRECT'):
            key = (rule['province'], rule['district'])
            unpriced_routes[key]['rows'] += 1
            if row['order_id']:
                unpriced_routes[key]['orders'].add(row['order_id'])
    route_summary = [dict(province=k[0], district=k[1], rows=v['rows'], orders=len(v['orders']))
                     for k,v in sorted(unpriced_routes.items())]
    warehouse = warehouse_labor_pool(start, end, rates['warehouse_labor_day_vnd'])
    # For eligible handling GHNs, classify all rows, require the corresponding
    # package point/trip to have been accepted and subtract only proven overlap.
    included_sources = set(points['accepted_sources']) | set(ftl['accepted_sources'])
    covered_issues=[];covered_count=0
    for issue in dc.get('group_evidence',[]):
        related=orders.get(issue.get('order_id'),[])
        if dc.get('grain')=='GHN_BOOKING_E_DAY':related=[r for r in related if str(r['dispatch_date'])==issue.get('day')]
        refs=set(issue.get('sources',[]))
        categories={category(r) for r in related}
        if (related and refs and refs <= included_sources and len(categories)==1 and
                next(iter(categories)) in ('point_bc','point_lc','point_remote','ftl')):
            covered_issues.extend(refs)
            covered_count+=1
    dc_scope={'unresolved_ghn':dc.get('unresolved_ghn',0),'covered_in_packages':covered_count,
              'pending_incremental':max(0,dc.get('unresolved_ghn',0)-covered_count),
              'covered_sources':sorted(set(covered_issues))}
    included_dc = Decimal(0)
    unknown_dc = []
    unknown_dc_amount=Decimal(0);known_incremental_events=0
    if dc.get('grain')=='GHN_BOOKING_E_DAY':
        for event in dc.get('eligible_events',[]):
            related=[r for r in orders.get(event['order_id'],[]) if str(r['dispatch_date'])==event['day']]
            categories={category(r) for r in related};source_refs=set(event['sources'])
            if categories=={None} and policy['package_selectors_complete'] and all((r:=_rule(row,catalog)) is not None and r['mode'] in ('TRANSFER_GXT','GXT_DIRECT') for row in related):
                known_incremental_events+=1;continue
            if len(categories)!=1 or None in categories or 'opening_ftl' in categories:
                unknown_dc.extend(source_refs)
                with localcontext() as ctx:ctx.prec=140;unknown_dc_amount+=Decimal(event['amount_vnd'])
                continue
            cat=next(iter(categories))
            if cat in ('point_bc','point_lc','point_remote','ftl'):
                if not source_refs<=included_sources:
                    unknown_dc.extend(source_refs)
                    with localcontext() as ctx:ctx.prec=140;unknown_dc_amount+=Decimal(event['amount_vnd'])
                    continue
                with localcontext() as ctx:
                    ctx.prec=140;included_dc+=Decimal(event['amount_vnd'])
            else:known_incremental_events+=1
    for bucket in ([] if dc.get('grain')=='GHN_BOOKING_E_DAY' else dc.get('rate_buckets', [])):
        refs = set(bucket.get('sources', []))
        rate = Decimal(bucket['rate_vnd_per_kg'])
        selected_orders = {r['order_id'] for r in rows if r['_source'] in refs and r['order_id']}
        for order in selected_orders:
            related = orders[order]
            categories = {category(r) for r in related}
            source_refs = {r['_source'] for r in related}
            known_rules = [_rule(r, catalog) for r in related]
            if policy['package_selectors_complete'] and categories == {None} and all(rule is not None and rule['mode'] in ('TRANSFER_GXT', 'GXT_DIRECT') for rule in known_rules):
                # Owner's fee selectors are exhaustive for the confirmed
                # packages; known unselected GXT routes have no package overlap.
                continue
            if len(categories) != 1 or None in categories:
                unknown_dc.extend(source_refs)
                continue
            cat = next(iter(categories))
            if cat in ('point_bc', 'point_lc', 'point_remote', 'ftl'):
                if not source_refs <= included_sources:
                    unknown_dc.extend(source_refs)
                    continue
                vals = {parse_kg(r.get('source_ghn_weight', ''))[0] for r in related if parse_kg(r.get('source_ghn_weight', ''))[1] == 'VALID'}
                if len(vals) != 1:
                    unknown_dc.extend(source_refs)
                    continue
                with localcontext() as ctx:
                    ctx.prec = 140
                    included_dc += next(iter(vals)) * rate
    gross = Decimal(dc['eligible_vnd']) if dc.get('available') and dc.get('eligible_vnd') is not None else None
    with localcontext() as ctx:
        ctx.prec = 140
        if dc.get('grain')=='GHN_BOOKING_E_DAY' and unknown_dc:
            incremental=gross-included_dc-unknown_dc_amount if gross is not None and known_incremental_events else None
        else:incremental = gross - included_dc if gross is not None and not unknown_dc else None
    # Raw rows are joined only by the exact previously classified source refs.
    mappings = {m['source']: m for m in evidence['mappings']}
    raw_rows = defaultdict(list)
    for row in data.vehicle_pair['data_supra']['rows']:
        r = dict(zip(RAW_HEADERS, row['values']))
        d = day(r['load_date']); vehicle = plate(r['truck_plate_number'])
        if cell(r['type']).upper() != 'DELIVER' or not d or not start <= str(d) <= end or not vehicle:
            continue
        raw_rows[(str(d), vehicle)].append((r, f"Raw#{data.vehicle_pair['raw_digest'][:12]} dòng {row['sheet_row']}"))
    gxt_groups = []
    group_flags = {}
    gxt_issue_counts = defaultdict(int)
    upstream_warning_groups = 0;two_leg_groups=0;opening_mixed_groups=0
    from .two_leg_evidence import direct_gxt_resolver
    resolve_direct=direct_gxt_resolver(data,evidence,policy) if rules else None
    def blocking_flags(mapping):
        flags = set(mapping['flags'])
        if mapping['mode'] == 'TRANSFER_GXT':
            flags.discard('multiple_booking_vehicles')
        if rules and mapping['mode'] == 'GXT_DIRECT' and mapping['raw_plate']:
            # Owner 29/10/2026: for the GXT truck cost the Raw S plate is the physical truck,
            # even when it differs from Booking AG (DC handling still uses the two-leg resolver).
            flags -= {'direct_vehicle_mismatch', 'multiple_booking_vehicles'}
        return flags
    latest_deliver = {}
    for row in data.vehicle_pair['data_supra']['rows']:
        r = dict(zip(RAW_HEADERS, row['values']))
        d = day(r['load_date']); order = business_key(r['order_code'])
        if cell(r['type']).upper() == 'DELIVER' and d is not None and order:
            latest_deliver[order] = max(latest_deliver.get(order, d), d)
    for group in evidence['groups']:
        if not start <= group['day'] <= end:
            continue
        group_rows = raw_rows.get((group['day'], group['plate']), [])
        if rules:
            item = _mixed_supra_truck(group, group_rows, mappings, orders, category, latest_deliver)
            if item is None:
                continue
            for flag in item['flags']:
                gxt_issue_counts[flag] += 1
            group_flags[(group['day'], group['plate'])] = item['flags']
            gxt_groups.append({k: item[k] for k in ('day', 'vehicle', 'supra_orders', 'other_kg', 'valid', 'sources')})
            continue
        selected = [mappings[ref] for _, ref in group_rows if ref in mappings]
        mapped_categories = {category(row, day(group['day'])) for m in selected for row in orders.get(m['order'], [])}
        if not selected or (mapped_categories and mapped_categories <= {'point_bc', 'point_lc', 'point_remote', 'ftl'} and all(not m['flags'] for m in selected)):
            continue
        issue_flags = {flag for mapping in selected for flag in blocking_flags(mapping)}
        if rules and 'opening_ftl' in mapped_categories:
            issue_flags.add('opening_ftl_shared_vehicle_day')
            opening_mixed_groups+=1
        two_leg_candidate=bool(rules and any(m['mode']=='GXT_DIRECT' and 'direct_vehicle_mismatch' in m['flags'] and 'direct_vehicle_mismatch' not in blocking_flags(m) for m in selected))
        if any(m['mode'] == 'TRANSFER_GXT' and 'multiple_booking_vehicles' in m['flags'] for m in selected):
            upstream_warning_groups += 1
        if group['share'] is None:
            issue_flags.add('raw_group_quality')
        if any(m['mode'] not in ('TRANSFER_GXT', 'GXT_DIRECT') for m in selected):
            issue_flags.add('mixed_or_unknown_role')
        valid = bool(group['share'] is not None and group_rows and all(m['mode'] in ('TRANSFER_GXT', 'GXT_DIRECT') and not blocking_flags(m) for m in selected))
        supra_orders = {}
        seen_trips = set()
        for raw_row, ref in group_rows:
            mapping = mappings.get(ref)
            if mapping is None:
                continue
            order = business_key(raw_row['order_code'])
            related = orders.get(order, [])
            parsed, status = parse_kg(raw_row['weight'])
            trip = business_key(raw_row['trip_code'])
            # Owner 29/10/2026: one GHN on several trips of the same truck/day is one order;
            # its kg is the sum of distinct trips and the delivery wage is paid once on that total.
            if (not order or (order, trip) in seen_trips or status != 'VALID' or not related or
                    any(category(r, day(group['day'])) != 'gxt' for r in related)):
                valid = False
                issue_flags.add('duplicate_order_or_unconfirmed_route_or_weight')
                continue
            seen_trips.add((order, trip))
            supra_orders[order] = str(Decimal(supra_orders.get(order, '0')) + parsed / Decimal(1000))
        if sum((Decimal(x) for x in supra_orders.values()), Decimal(0)) != Decimal(group['supra_kg']):
            valid = False
            issue_flags.add('source_weight_mismatch')
        if valid and two_leg_candidate:two_leg_groups+=1
        for flag in issue_flags:
            gxt_issue_counts[flag] += 1
        group_flags[(group['day'], group['plate'])] = sorted(issue_flags)
        gxt_groups.append(dict(day=group['day'], vehicle=group['plate'], supra_orders=supra_orders,
                               other_kg=group['other_kg'], valid=valid, sources=group['sources']))
    gxt = gxt_daily_cost(gxt_groups, rates['gxt_vehicle_day_vnd'], policy['wage']['tiers'])
    by_sources = {tuple(g['sources']): group_flags.get((g['day'], g['vehicle']), []) for g in gxt_groups}
    for item in gxt['held']:
        item['flags'] = by_sources.get(tuple(item['sources']), [])
    gxt['issue_counts'] = dict(gxt_issue_counts)
    gxt['upstream_multi_ag_warning_groups'] = upstream_warning_groups
    if rules:gxt['two_leg_groups']=two_leg_groups;gxt['two_leg_upstream_vnd']=None
    if rules:gxt['opening_mixed_groups']=opening_mixed_groups
    blank_points = _blank_plate_points(data, rules, start, end, orders)
    quality = vehicle_scope_quality(data, evidence, start+' '+end)
    gxt['unassigned_quality'] = quality
    if quality and (quality['missing_plate'] or quality['undated']):
        gxt['complete'] = False
    return {'available': True, 'scope': scope, 'policy_sha256': sha,
            'booking_digest': data.vehicle_pair['booking_digest'], 'raw_digest': data.vehicle_pair['raw_digest'],
            'vehicle_catalog_sha256': data.vehicle_pair['vehicle_catalog_sha256'], 'handling_catalog_sha256': data.handling_catalog_sha256,
            'wage_source_sha256': policy['wage']['source_sha256'], 'wage_extraction_sha256': policy['wage']['extraction_sha256'],
            'points': points, 'remote': remote, 'ftl': ftl, 'warehouse': warehouse, 'dc': dc, 'dc_scope':dc_scope, 'upstream': upstream,
            'dc_included_vnd': str(included_dc) if gross is not None else None, 'dc_overlap_unknown_sources': sorted(set(unknown_dc)),
            'dc_incremental_vnd': str(incremental) if incremental is not None else None,
            'gxt': gxt, 'unpriced_booking_sources': unknown_refs, 'unpriced_ncc_routes': route_summary,
            'rules_sha256':rules_sha,'opening_unpriced_events':len([key for key in opening if start<=key[1]<=end]),'blank_points':blank_points,
            'total_cost_vnd': None, 'gross_profit_vnd': None}


def _complete_lead(report):
    from .manual_cost_overlay import projected_totals
    if projected_totals(report)[0] is not None:
        return 'Kết luận: đã đủ nguồn để tính tổng chi phí và lãi/lỗ DỰ KIẾN trước VAT; số thực chi chờ hóa đơn/bảng kê NCC cuối tháng.'
    return 'Kết luận: không còn dòng giữ riêng, nhưng tổng dự kiến chưa chốt vì còn khoản chưa có quy tắc hoặc chưa có giá (xem dưới).'


def _projected_line(report, money):
    from .manual_cost_overlay import projected_totals
    total, gp, blockers = projected_totals(report)
    overlay = report['overlay']
    if total is not None:
        from decimal import Decimal
        vat = Decimal('1.08')  # Owner 27/10/2026: VAT 8% on revenue and NCC charges, for invoice reconciliation only.
        revenue = overlay['revenue']['component_vnd']
        return ('TỔNG DỰ KIẾN trước VAT: chi phí '+money(total)+'; lãi/lỗ dự kiến '+money(gp)+'. Đối chiếu hóa đơn (gồm VAT 8%): doanh thu '+
                money(str(Decimal(revenue)*vat))+', chi phí '+money(str(Decimal(total)*vat))+'. Chưa đối soát hóa đơn/bảng kê NCC.')
    return ('Chi phí đã tính: '+money(overlay['known_cost_subtotal'])+'; chênh lệch: '+money(overlay['revenue_minus_known_costs'])+
            '. Chưa có tổng dự kiến vì: '+'; '.join(blockers[:3])+('…' if len(blockers)>3 else '')+'.')


def render_cost_report(report, *, details=False, audit=False, answered_text=None):
    if not report.get('available'):
        return 'Chưa tính được bảng chi phí: chính sách hoặc nguồn chưa được nạp và đối chiếu đúng kỳ.'
    from .vehicle_report import _vi_money, _forecast_line, _vi_day
    def money(value):
        return _vi_money(value) + 'đ' if value is not None else 'chưa đủ căn cứ'
    p, f, w, dc, g = (report[k] for k in ('points', 'ftl', 'warehouse', 'dc', 'gxt'))
    remote=report.get('remote')
    start, end = report['scope']['start'], report['scope']['end']
    lines = [f"CHI PHÍ DỰ KIẾN {_vi_day(start,year=True)}–{_vi_day(end,year=True)} — phần đủ nguồn, chưa là thực chi.",
             'Giao theo điểm: ' + money(p['eligible_vnd']) + ' (' + str(p['counts'].get('point_bc', 0)) + ' điểm BC, ' + str(p['counts'].get('point_lc', 0)) + ' điểm Lai Châu); giữ ' + str(len(p['held'])) + ' nhóm/dòng.',
             'FTL tải đã chốt: ' + money(f['eligible_vnd']) + '; ' + str(f['eligible_groups']) + ' xe/ngày, dự kiến ' + str(f['eligible_trips']) + ' chuyến; giữ ' + str(len(f['held'])) + ' nhóm/dòng.',
             'Xe GXT: ' + money(g['vehicle_supra_vnd']) + ' phần Supra theo kg từng xe/ngày; lương giao thêm ' + money(g['wage_supra_vnd']) + '; giữ ' + str(len(g['held'])) + ' nhóm.' + ((' '+str(g['unassigned_quality']['missing_plate'])+' dòng thiếu biển chưa gán xe.') if g['unassigned_quality'] and g['unassigned_quality']['missing_plate'] else '') + (' Có dòng thiếu ngày chưa gán kỳ.' if g['unassigned_quality'] and g['unassigned_quality']['undated'] else ''),
             'Bốc xếp DC: theo đơn giá gốc, phần đủ nguồn ' + money(dc.get('eligible_vnd')) + '; chứng minh đã gồm trong gói ' + money(report['dc_included_vnd']) + '; ngoài gói đủ nguồn ' + money(report['dc_incremental_vnd']) + '.',
             'Nhân sự bốc xếp tại kho GXT: ' + money(w['shared_pool_vnd']) + ' dùng chung, chưa chia Supra (' + str(w['days']) + ' ngày).',
             ('Xe trung chuyển dùng chung: '+money(report['upstream'].get('eligible_pool_vnd'))+' phần đủ nguồn; chưa chia Supra. /xe xem từng tải.' if report['upstream'].get('available') else _forecast_line(report['upstream'])),
             'Chưa đủ tổng chi phí/lãi lỗ; '+str(len(report['unpriced_booking_sources']))+' dòng Booking chưa có giá theo phạm vi. VAT, phụ phí km/chờ và thuê kho/điện nước chưa tính. /details xem khoản cần đối chiếu.']
    overlay=report.get('overlay')
    exclusions=(overlay.get('scope_exclusions') if overlay and overlay.get('available') else None)
    labels={'WAREHOUSE_RENT':'thuê kho','ELECTRICITY_WATER':'điện/nước','VEHICLE_WAIT':'chờ xe','OTHER_OUT_OF_PACKAGE':'phụ phí ngoài gói'}
    if overlay and overlay.get('available'):
        revenue=overlay['revenue']
        lines=[f"DỰ KIẾN TRƯỚC VAT {_vi_day(start,year=True)}–{_vi_day(end,year=True)} — phần đủ nguồn.",
            'Doanh thu dự kiến '+('đủ nguồn trong phạm vi' if revenue['complete'] else 'phần đủ nguồn, chưa đủ toàn phạm vi')+': '+money(revenue['eligible_vnd'])+' ('+str(revenue['eligible_units'])+' sản phẩm).',
            _projected_line(report,money),
            'Giao điểm: '+money(p['eligible_vnd'])+('; NCC vùng xa cơ bản: '+money(remote['eligible_vnd']) if remote else '')+'; FTL: '+money(f['eligible_vnd'])+' ('+str(f['eligible_trips'])+' chuyến dự kiến).',
            'Xe GXT phân bổ theo kg: '+money(g['vehicle_supra_vnd'])+'; lương giao: '+money(g['wage_supra_vnd'])+'.'+
                ((' Điểm giao không gán xe theo giá điểm khu vực: '+str(report['blank_points']['points'])+' điểm, '+money(report['blank_points']['eligible_vnd'])+'.') if report.get('blank_points') and report['blank_points']['points'] else ''),
            'Bốc xếp DC theo đơn giá gốc: '+money(dc.get('eligible_vnd'))+'; đã gồm trong gói: '+money(report['dc_included_vnd'])+'; ngoài gói: '+money(report['dc_incremental_vnd'])+'.',
            'Theo chỉ định cho Supra: xe trung chuyển '+money(overlay['allocations']['upstream_vnd'])+'; nhân sự bốc xếp GXT '+money(overlay['allocations']['warehouse_vnd'])+'.',
            (('Thiếu biển: '+str(overlay['missing_s_gxt_rows'])+' dòng GXT trong '+str(g['unassigned_quality']['missing_plate'] if g['unassigned_quality'] else 0)+' dòng toàn nguồn. ')
                if overlay['missing_s_gxt_rows'] or (g['unassigned_quality'] or {}).get('missing_plate') else '')+'Còn khoản giữ riêng, VAT'+(' và km NCC chờ nhật trình/đơn giá' if remote else '')+'; /details xem tiếp.']
        if exclusions:
            lines.append('Loại khỏi phạm vi theo chỉ định: '+', '.join(labels[x] for x in exclusions['categories'])+'. Không có nghĩa là không phát sinh.')
        else:
            lines.append('Chưa tính thuê kho, điện/nước, chờ xe và phụ phí ngoài gói.')
    elif overlay:
        lines.insert(1,'Chưa dùng được chính sách doanh thu/phân bổ: cần kiểm tra lại nguồn và cấu hình đã duyệt.')
    if remote and not (overlay and overlay.get('available')):
        lines.insert(2,'NCC vùng xa: điểm cơ bản '+money(remote['eligible_vnd'])+'; phí km chưa đủ căn cứ, tổng chưa hoàn tất.')
    if report.get('opening_unpriced_events'):
        lines.append('Chưa tính '+str(report['opening_unpriced_events'])+' đợt khai trương dùng xe Booking: thiếu giá và km thực tế.')
    if report.get('rules_sha256') and g.get('two_leg_groups'):
        lines.append('Có '+str(g['two_leg_groups'])+' nhóm hai chặng; chi phí chặng AG chưa có căn cứ tính riêng cho các tuyến này.')
    if details:
        detail_lines = ['Tiền điểm theo ngày xuất và cửa hàng, không nhân theo DO; chuyến FTL tính theo tải đã chốt, chưa là chuyến thực tế.',
                  ('Xe GXT tính theo kg trên chính xe/ngày; lương giao tính thêm. Phân bổ xe trung chuyển và nhân sự kho theo tỷ lệ owner chỉ định, không chứng minh hàng khác không có.' if overlay and overlay.get('available') else 'Xe GXT tính theo kg trên chính xe/ngày; lương giao tính thêm. Tiền xe trung chuyển và nhân sự kho là pool chưa phân bổ Supra.'),
                  'Bốc xếp DC ngoài gói chỉ là phần đủ nguồn; không cộng lại khoản đã nằm trong giá giao. Các tuyến, tải trọng chưa có giá tiếp tục giữ riêng.',
                  ('Cần chốt cước NCC cho: '+ '; '.join(r['district']+' ('+str(r['orders'])+' GHN)' for r in report['unpriced_ncc_routes'])+'.' if report['unpriced_ncc_routes'] else 'Chưa có tuyến NCC thiếu giá xác định theo catalog.'),
                  'FTL giữ '+str(sum('unpriced_class' in r['reason'] for r in f['held']))+' nhóm tải chưa có giá và '+str(sum('mixed_or_unknown_class' in r['reason'] for r in f['held']))+' nhóm tải T thiếu/mâu thuẫn; đối chiếu T/AG và chốt giá cho loại tải chưa có.',
                  'GXT: '+str(g['issue_counts'].get('direct_vehicle_mismatch',0))+' nhóm giao thẳng cần đối chiếu biển Raw S với Booking AG theo ngày; '+str(g['issue_counts'].get('raw_group_quality',0))+' nhóm cần kiểm tra dữ liệu chuyến/đơn/kg. '+str(g['unassigned_quality']['missing_plate'] if g['unassigned_quality'] else 0)+' dòng thiếu biển chưa gán xe.',
                  'Cần đối chiếu cửa hàng/tuyến hoặc kg còn thiếu; VAT và phụ phí chưa có giá thì chưa tính.']
        if overlay and overlay.get('available'):
            detail_lines=[
                ('NCC vùng xa: '+str(remote['eligible_points'])+' điểm cơ bản; cần nhật trình/km và đơn giá phát sinh để tính đủ.' if remote else ('Chốt cước NCC: '+ '; '.join(r['district']+' ('+str(r['orders'])+' GHN)' for r in report['unpriced_ncc_routes'])+'.' if report['unpriced_ncc_routes'] else 'Chưa có tuyến NCC thiếu giá xác định theo catalog.')),
                'FTL giữ '+str(sum('unpriced_class' in r['reason'] for r in f['held']))+' nhóm tải chưa có giá và '+str(sum('mixed_or_unknown_class' in r['reason'] for r in f['held']))+' nhóm tải T thiếu/mâu thuẫn; đối chiếu T/AG.',
                'GXT: '+str(g['issue_counts'].get('direct_vehicle_mismatch',0))+' nhóm giao thẳng cần đối chiếu Raw S với Booking AG theo ngày; ưu tiên '+str(overlay['missing_s_gxt_rows'])+' dòng GXT thiếu S, không suy biển từ AG.',
                'DC: '+str(report['dc_scope']['unresolved_ghn'])+(' đợt' if dc.get('grain')=='GHN_BOOKING_E_DAY' else ' GHN')+' còn vấn đề nguồn; '+str(report['dc_scope']['covered_in_packages'])+' đã có khoản DC trong gói được xác nhận, '+str(report['dc_scope']['pending_incremental'])+' cần đối chiếu để xét phí thêm. Chưa sửa hoặc xác nhận dữ liệu.',
                'Phân bổ xe trung chuyển và nhân sự kho là quyết định của owner. Chênh lệch chưa gồm các khoản còn thiếu, không là lợi nhuận cuối.']
        lines+=detail_lines
        if report.get('opening_unpriced_events'):
            lines.append('Có '+str(report['opening_unpriced_events'])+' đợt khai trương dùng xe Booking; giá/km thực tế chưa có nên chưa tính, không áp giá điểm BC. '+str(g.get('opening_mixed_groups',0))+' nhóm S/ngày có đơn này được giữ riêng khi chưa tách được chi phí; các chuyến Raw khác vẫn giữ nguyên bằng chứng.')
        if exclusions:
            lines.append('Các khoản đã loại chỉ nằm ngoài phạm vi dự kiến này, không khẳng định thực tế bằng 0. Phí km NCC vẫn chờ nhật trình và đơn giá.')
        if remote and not (overlay and overlay.get('available')):
            lines.append('NCC vùng xa: phần điểm cơ bản '+money(remote['eligible_vnd'])+'; chi phí km phát sinh chưa đủ căn cứ, tổng chưa hoàn tất.')
    if audit:
        lines += ['DẤU VẾT: policy ' + report['policy_sha256'] + '; booking ' + report['booking_digest'] + '; raw ' + report['raw_digest'] +
                  '; catalog xe ' + report['vehicle_catalog_sha256'] + '; catalog DC ' + report['handling_catalog_sha256'] +
                  '; nguồn lương ' + report['wage_source_sha256'] + '; trích xuất ' + report['wage_extraction_sha256'] + '.',
                  'Điểm giữ: ' + str(p['held'][:8]) + '; FTL giữ: ' + str(f['held'][:8]) + '; GXT giữ: ' + str(g['held'][:8]) +
                  '; lỗi GXT: ' + str(g['issue_counts']) + '; cảnh báo nhiều AG chặng trước: '+str(g['upstream_multi_ag_warning_groups'])+' nhóm; Raw chưa gán: ' + str(g['unassigned_quality']) + '; tuyến chưa có giá: ' + str(report['unpriced_booking_sources'][:8]) + '; DC trùng chưa chứng minh: ' + str(report['dc_overlap_unknown_sources'][:8]) + '.',
                  'DC giữ: ' + str(dc.get('counters', {})) + '; phạm vi '+str({k:v for k,v in report['dc_scope'].items() if k!='covered_sources'})+'; mẫu nguồn được chứng minh trong gói '+str(report['dc_scope']['covered_sources'][:8])+'; mẫu nguồn chưa giải quyết: ' + str(dc.get('issue_sources', [])[:8]) + '.',
                  ('FTL theo tải: '+str(f.get('tariff_breakdown',{}))+'; NCC vùng xa: '+str(remote)+'.' if remote else ''),
                  'Nội dung đã trả:\n' + (answered_text or '')]
        if report.get('rules_sha256'):lines.append('Quy tắc owner '+report['rules_sha256']+'; bốc xếp theo đợt GHN/ngày Booking; số đợt khai trương thiếu giá/km '+str(report['opening_unpriced_events'])+'.')
        if report.get('rules_sha256') and report['upstream'].get('available'):
            confirmed=[x for x in report['upstream'].get('group_evidence',[]) if 'owner_confirmed_booked_trips' in x.get('warnings',[])]
            if confirmed:lines.append('Mẫu lượt đặt đã xác nhận: '+str([{k:x[k] for k in ('date','sources','warnings')} for x in confirmed[:8]])+'.')
        if dc.get('eligible_events'):lines.append('Mẫu đợt bốc xếp: '+str([{k:x[k] for k in ('day','sources','kg','amount_vnd')} for x in dc['eligible_events'][:8]])+'.')
        if report.get('legacy_dc_warnings'):lines.append('Cảnh báo GHN cũ giữ để đối chiếu: '+str(report['legacy_dc_warnings']['counters'])+'; mẫu '+str(report['legacy_dc_warnings']['issue_sources'][:8])+'.')
        if overlay and overlay.get('available'):
            lines.append('Phân bổ/doanh thu: overlay '+overlay['overlay_sha256']+'; base '+overlay['base_policy_sha256']+'; forecast '+overlay['forecast_sha256']+'; nguồn giá '+str(overlay['price_source_sha256'])+'; provenance '+str(overlay['provenance'])+'; tỷ lệ '+overlay['upstream_ratio']+'/'+overlay['warehouse_ratio']+'; pool gốc '+str(w['shared_pool_vnd'])+'/'+str(report['upstream'].get('eligible_pool_vnd'))+'; thành phần '+str(overlay['components'])+'; số lượng cách ly/cảnh báo '+str(overlay['revenue']['quarantined_rows'])+'/'+str(overlay['revenue']['conflicting_key_rows'])+'; mẫu thiếu giá '+str(overlay['revenue']['unpriced_sources'][:8])+'; thiếu S GXT '+str(overlay['missing_s_gxt_sources'][:8])+'.')
            if exclusions:lines.append('Chỉ định loại phạm vi: '+str(exclusions)+'; không loại phí km NCC.')
    if not details and not audit:
        from .missing_data import missing_checklist
        count,_=missing_checklist(report)
        lines.insert(0,('Kết luận: mới tính được phần đủ nguồn; còn '+str(count)+' nhóm dữ liệu thiếu nên chưa có tổng chi phí/lãi lỗ. Việc cần làm: gửi /thieu để xem từng dòng cần bổ sung.') if count else
            _complete_lead(report))
    return '\n\n'.join(lines)
