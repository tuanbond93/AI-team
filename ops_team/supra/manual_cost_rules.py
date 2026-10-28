"""Pure calculation primitives for owner-approved manual cost policies.

This module never imports or evaluates source code. Policy values must be
separately validated and bound to the owner's approved evidence before use.
"""
from decimal import Decimal, InvalidOperation, localcontext
from collections import defaultdict
from datetime import date
import re


def _number(value, *, positive=False):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,20}(?:\.[0-9]{1,12})?', value):
        raise ValueError('Expected bounded decimal text')
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise ValueError('Invalid decimal text') from None
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise ValueError('Invalid nonnegative value')
    return result


def wage_amount(weight, tiers):
    """Evaluate explicitly inclusive/exclusive tiers; never execute expressions.

    A stepped tier uses ``base + max(min_steps, int((weight-offset)//step))
    * increment``. ``min_steps`` may be null for the unbounded expression.
    The caller chooses the weight grain and checks provenance and eligibility.
    """
    weight = _number(weight)
    if not isinstance(tiers, list) or not 1 <= len(tiers) <= 100:
        raise ValueError('Invalid wage tiers')
    previous = None
    selected = None
    for i, tier in enumerate(tiers):
        if not isinstance(tier, dict) or set(tier) != {'upper_bound', 'inclusive', 'formula'} or type(tier['inclusive']) is not bool:
            raise ValueError('Invalid tier')
        upper = tier['upper_bound']
        if upper is None:
            if i != len(tiers)-1:
                raise ValueError('Unbounded tier must be last')
        else:
            upper = _number(upper, positive=True)
            if previous is not None and upper <= previous:
                raise ValueError('Tier bounds must increase')
            previous = upper
        formula = tier['formula']
        if not isinstance(formula, dict) or formula.get('kind') not in ('FIXED', 'STEP'):
            raise ValueError('Invalid formula')
        if formula['kind'] == 'FIXED':
            if set(formula) != {'kind', 'amount_vnd'}:
                raise ValueError('Invalid fixed formula')
            _number(formula['amount_vnd'])
        else:
            if set(formula) != {'kind', 'base_vnd', 'offset_kg', 'step_kg', 'increment_vnd', 'min_steps'}:
                raise ValueError('Invalid stepped formula')
            for field in ('base_vnd', 'offset_kg', 'increment_vnd'):
                _number(formula[field])
            _number(formula['step_kg'], positive=True)
            if formula['min_steps'] is not None and (type(formula['min_steps']) is not int or not 0 <= formula['min_steps'] <= 1000000):
                raise ValueError('Invalid step floor')
        if selected is None and (upper is None or weight < upper or (tier['inclusive'] and weight == upper)):
            selected = formula
    if tiers[-1]['upper_bound'] is not None:
        raise ValueError('Missing final tier')
    if weight == 0:
        return Decimal(0)
    if selected is None:
        raise ValueError('No applicable tier')
    if selected['kind'] == 'FIXED':
        return _number(selected['amount_vnd'])
    with localcontext() as ctx:
        ctx.prec = 100
        steps = int((weight - _number(selected['offset_kg'])) // _number(selected['step_kg'], positive=True))
        if selected['min_steps'] is not None:
            steps = max(selected['min_steps'], steps)
        value = _number(selected['base_vnd']) + Decimal(steps) * _number(selected['increment_vnd'])
        if value < 0:
            raise ValueError('Negative calculated wage')
        return value


def warehouse_labor_pool(start, end, daily_rate):
    """Calculate the inclusive shared labor pool, including nonworking days."""
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last or first.strftime('%Y-%m') != last.strftime('%Y-%m'):
        raise ValueError('Invalid period')
    rate = _number(daily_rate, positive=True)
    with localcontext() as ctx:
        ctx.prec = 100
        days = (last-first).days + 1
        return {'days': days, 'shared_pool_vnd': str(rate * days), 'supra_vnd': None}


def delivery_points(records, rates):
    """Compute distinct date/store points; hold mixed modes and missing keys.

    Caller must classify each row using the pinned approved catalog and set
    ``valid`` only after checking complete GHN scope and route. Each record
    has day, store, category, valid and source. Never use a DO as a point.
    """
    if not isinstance(rates, dict) or not rates:
        raise ValueError('Missing rates')
    numeric = {key: _number(value, positive=True) for key, value in rates.items()}
    groups = defaultdict(list)
    held = []
    for row in records:
        if set(row) != {'day', 'store', 'category', 'valid', 'source'} or type(row['valid']) is not bool:
            raise ValueError('Invalid point record')
        if not row['day'] or not row['store']:
            held.append({'reason': 'missing_date_or_store', 'sources': [row['source']]})
            continue
        try:
            date.fromisoformat(row['day'])
        except ValueError:
            held.append({'reason': 'invalid_date', 'sources': [row['source']]})
            continue
        groups[(row['day'], row['store'])].append(row)
    counts = defaultdict(int)
    accepted_sources = []
    with localcontext() as ctx:
        ctx.prec = 100
        amount = Decimal(0)
        for rows in groups.values():
            categories = {r['category'] for r in rows}
            if any(not r['valid'] for r in rows) or len(categories) != 1 or next(iter(categories)) not in numeric:
                held.append({'reason': 'ambiguous_route_or_rate', 'sources': sorted({r['source'] for r in rows})})
                continue
            category = next(iter(categories))
            counts[category] += 1
            amount += numeric[category]
            accepted_sources.extend(r['source'] for r in rows)
    return {'eligible_vnd': str(amount) if counts else None, 'counts': dict(counts), 'held': held, 'accepted_sources': accepted_sources,
            'complete': not held, 'total_cost_vnd': None}


def ftl_base_trips(records, capacity_kg, trip_rate, *, ceil_confirmed=False, vehicle_classes=None, tariffs=None, per_class=False):
    """Price only groups with confirmed matching T; optionally use owner ceil.

    ``per_class`` (owner rule 27/10/2026): one vehicle/day carrying several
    Booking T classes is one trip group per distinct T, each priced at its own
    T; GHNs with the same T on that vehicle/day share one group.

    Caller supplies all rows for each order and marks incomplete/mixed scope or
    route as invalid. Weight is a GHN total counted once; blank DO weights
    are allowed only when another DO supplies one unambiguous valid total.
    """
    cap = _number(capacity_kg, positive=True)
    rate = _number(trip_rate, positive=True)
    if type(ceil_confirmed) is not bool:
        raise ValueError('Explicit owner confirmation required')
    choices = {cap: (rate,ceil_confirmed)}
    if tariffs is not None:
        choices={}
        for tariff in tariffs:
            if not isinstance(tariff,dict) or set(tariff)!={'class_kg','rate_vnd','method'} or tariff['method'] not in ('CEIL_WEIGHT','ONE_IF_WITHIN_CAPACITY'):
                raise ValueError('Invalid FTL tariff')
            klass=_number(tariff['class_kg'],positive=True)
            if klass in choices:raise ValueError('Duplicate FTL class')
            choices[klass]=(_number(tariff['rate_vnd'],positive=True),tariff['method']=='CEIL_WEIGHT')
        if not choices:raise ValueError('Missing FTL tariffs')
    groups, orders = defaultdict(list), defaultdict(list)
    held = []
    for row in records:
        if set(row) != {'day', 'vehicle', 'order', 'weight_kg', 'weight_status', 'class_kg', 'valid', 'source'} or type(row['valid']) is not bool or row['weight_status'] not in ('VALID','MISSING','INVALID'):
            raise ValueError('Invalid trip record')
        if row['order']:
            orders[row['order']].append(row)
        if not row['day'] or not row['vehicle'] or not row['order']:
            held.append({'reason': 'missing_key', 'sources': [row['source']]})
            continue
        try:
            date.fromisoformat(row['day'])
        except ValueError:
            held.append({'reason': 'invalid_date', 'sources': [row['source']]})
            continue
        key = (row['day'], row['vehicle'])
        if per_class:
            try:
                key += (_number(row['class_kg'], positive=True),)
            except ValueError:
                key += (None,)
        groups[key].append(row)
    accepted = 0
    trips = 0
    amount_total=Decimal(0)
    breakdown=defaultdict(lambda:{'groups':0,'trips':0,'amount_vnd':Decimal(0)})
    accepted_sources = []
    for key, rows in groups.items():
        reasons = set()
        weights = []
        classes = set()
        for row in rows:
            try:
                classes.add(_number(row['class_kg'], positive=True))
            except ValueError:
                reasons.add('missing_or_invalid_class')
        for observed in ([] if per_class else (vehicle_classes or {}).get(key, [])):
            try:
                classes.add(_number(observed, positive=True))
            except ValueError:
                reasons.add('missing_or_invalid_class')
        group_cap=None;group_rate=None;group_ceil=False
        if len(classes) != 1:
            reasons.add('mixed_or_unknown_class')
        else:
            group_cap=next(iter(classes))
            if group_cap not in choices:reasons.add('unpriced_class')
            else:group_rate,group_ceil=choices[group_cap]
        for order in {r['order'] for r in rows}:
            related = orders[order]
            if any((r['day'], r['vehicle']) != key[:2] or not r['valid'] for r in related):
                reasons.add('partial_or_conflicting_scope')
            if per_class and any(r['class_kg'] != rows[0]['class_kg'] for r in related):
                reasons.add('mixed_or_unknown_class')
            parsed = []
            for r in related:
                if r['weight_status'] == 'MISSING':
                    continue
                if r['weight_status'] == 'INVALID':
                    reasons.add('invalid_weight')
                    continue
                try:
                    parsed.append(_number(r['weight_kg']))
                except ValueError:
                    reasons.add('invalid_weight')
            if len(set(parsed)) != 1:
                reasons.add('missing_or_conflicting_weight')
            elif parsed:
                weights.append(parsed[0])
        if any(not r['valid'] for r in rows):
            reasons.add('ambiguous_route')
        with localcontext() as ctx:
            ctx.prec = 100
            total_weight = sum(weights, Decimal(0))
        if group_cap is not None and total_weight > group_cap and not group_ceil:
            reasons.add('above_capacity_no_trip_algorithm')
        if total_weight <= 0:
            reasons.add('zero_weight')
        if reasons:
            held.append({'reason': ','.join(sorted(reasons)), 'sources': sorted({r['source'] for r in rows})})
        else:
            accepted += 1
            with localcontext() as ctx:
                ctx.prec = 100
                group_trips = int((total_weight / group_cap).to_integral_value(rounding='ROUND_CEILING')) if group_ceil else 1
                trips += group_trips
                amount_total += group_rate*group_trips
                entry=breakdown[str(group_cap)];entry['groups']+=1;entry['trips']+=group_trips;entry['amount_vnd']+=group_rate*group_trips
            accepted_sources.extend(r['source'] for r in rows)
    with localcontext() as ctx:
        ctx.prec = 100
        amount = str(amount_total) if accepted else None
    return {'eligible_vnd': amount, 'eligible_groups': accepted, 'eligible_trips': trips, 'held': held, 'accepted_sources': accepted_sources,
            'tariff_breakdown':{k:{**v,'amount_vnd':str(v['amount_vnd'])} for k,v in breakdown.items()},
            'complete': not held, 'total_cost_vnd': None}


def gxt_daily_cost(groups, daily_rate, wage_tiers):
    """Allocate only eligible physical vehicle/day groups using their own kg.

    Caller validates physical vehicle, date, source keys and route against the
    paired evidence. Supra order weights are already deduplicated at that
    vehicle/day grain; an invalid or conflicting event makes the group invalid.
    Wages are one tier-calculated amount per order using that order's weight.
    """
    rate = _number(daily_rate, positive=True)
    # Validate the complete tariff even if no groups are eligible.
    wage_amount('0', wage_tiers)
    seen = set()
    held = []
    count = 0
    vehicle_supra = Decimal(0)
    wage_supra = Decimal(0)
    with localcontext() as ctx:
        ctx.prec = 100
        for group in groups:
            if set(group) != {'day', 'vehicle', 'supra_orders', 'other_kg', 'valid', 'sources'} or type(group['valid']) is not bool:
                raise ValueError('Invalid physical group')
            key = (group['day'], group['vehicle'])
            if key in seen:
                raise ValueError('Duplicate vehicle/day group')
            seen.add(key)
            try:
                if not group['day'] or not group['vehicle'] or not group['valid']:
                    raise ValueError
                date.fromisoformat(group['day'])
                other = _number(group['other_kg'])
                orders = group['supra_orders']
                if not isinstance(orders, dict) or not orders:
                    raise ValueError
                weights = [_number(v) for v in orders.values()]
                if any(not k for k in orders):
                    raise ValueError
                supra = sum(weights, Decimal(0))
                if supra + other <= 0:
                    raise ValueError
            except (ValueError, TypeError):
                held.append({'reason': 'unverified_route_key_or_weight', 'sources': group['sources']})
                continue
            vehicle_supra += rate * supra / (supra + other)
            wage_supra += sum((wage_amount(str(w), wage_tiers) for w in weights), Decimal(0))
            count += 1
    return {'eligible_vehicle_days': count, 'vehicle_pool_vnd': str(rate*count) if count else None,
            'vehicle_supra_vnd': str(vehicle_supra) if count else None,
            'wage_supra_vnd': str(wage_supra) if count else None,
            'held': held, 'complete': not held, 'total_cost_vnd': None}
