"""Cost by route (province / district), owner J1: each part at the price of its own vehicle or carrier.

Reads the /chiphi table only (no new prices):
- GXT vehicle share and delivery wage: per order (kg share of its own vehicle/day, tier wage of the order);
- delivery points and area points: per store/day;
- FTL: per vehicle/day, when all its orders go to one route.
An FTL vehicle/day serving several routes, and orders or stores whose route is not unique,
are "not attributed". Shared pools (DC handling, GXT warehouse staff, transfer trucks,
remote NCC km) are listed as not split by route. Assigned + not attributed = each component total.
"""
from collections import defaultdict
from decimal import Decimal, localcontext

from .booking_handling import district_name, geography
from .booking_quantity import business_key

PARTS = ('gxt_vehicle', 'gxt_wage', 'points', 'ftl')


def route_costs(report, data, scope):
    order_geo, store_geo, events = defaultdict(set), defaultdict(set), defaultdict(set)
    for r in data.rows.to_dict('records'):
        geo = (geography(r['source_province'], province=True), district_name(r['source_district']))
        if r['order_id']:
            order_geo[business_key(r['order_id'])].add(geo)
            if r['dispatch_date'] is not None and scope['start'] <= str(r['dispatch_date']) <= scope['end']:
                events[geo].add((r['order_id'], str(r['dispatch_date'])))
        if r.get('source_store_id'):
            store_geo[r['source_store_id']].add(geo)
    routes = defaultdict(lambda: {p: Decimal(0) for p in PARTS})
    missing = {p: Decimal(0) for p in PARTS}
    def put(part, geos, amount):
        if len(geos) == 1:
            routes[next(iter(geos))][part] += amount
        else:
            missing[part] += amount
    with localcontext() as ctx:
        ctx.prec = 100
        for g in report['gxt'].get('by_group', []):
            for order, cost in g.get('order_costs', {}).items():
                geos = order_geo.get(business_key(order), set())
                put('gxt_vehicle', geos, Decimal(cost['vehicle_vnd']))
                put('gxt_wage', geos, Decimal(cost['wage_vnd']))
        for item in report['points'].get('by_store', [])+(report.get('blank_points') or {}).get('by_store', []):
            put('points', store_geo.get(item['store'], set()), Decimal(item['vnd']))
        for item in report['ftl'].get('by_group', []):
            geos = set().union(*(order_geo.get(business_key(o), set()) for o in item['orders'])) if item['orders'] else set()
            put('ftl', geos, Decimal(item['vnd']))
    pools = {'Bốc xếp DC ngoài gói': report.get('dc_incremental_vnd'), 'Nhân sự bốc xếp kho GXT': report['warehouse'].get('shared_pool_vnd'),
             'Xe trung chuyển dùng chung': report['upstream'].get('eligible_pool_vnd') if report['upstream'].get('available') else None,
             'NCC vùng xa': (report.get('remote') or {}).get('eligible_vnd')}
    return {'routes': {geo: {**parts, 'total': sum(parts.values(), Decimal(0)), 'events': len(events.get(geo, ()))}
                       for geo, parts in routes.items()},
            'missing': missing, 'pools': {k: v for k, v in pools.items() if v is not None}}
