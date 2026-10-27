from decimal import Decimal
import pytest
from ops_team.supra.manual_cost_rules import wage_amount, warehouse_labor_pool, delivery_points, ftl_base_trips, gxt_daily_cost


TIERS = [
    {'upper_bound': '99', 'inclusive': True, 'formula': {'kind': 'FIXED', 'amount_vnd': '110'}},
    {'upper_bound': '100', 'inclusive': True, 'formula': {'kind': 'FIXED', 'amount_vnd': '220'}},
    {'upper_bound': '200', 'inclusive': False, 'formula': {'kind': 'STEP', 'base_vnd': '300', 'offset_kg': '100', 'step_kg': '10', 'increment_vnd': '7', 'min_steps': 0}},
    {'upper_bound': None, 'inclusive': False, 'formula': {'kind': 'STEP', 'base_vnd': '500', 'offset_kg': '200', 'step_kg': '25', 'increment_vnd': '9', 'min_steps': None}},
]


@pytest.mark.parametrize(('weight', 'expected'), [
    ('0', '0'), ('99', '110'), ('99.001', '220'), ('100', '220'),
    ('100.001', '300'), ('109.999', '300'), ('110', '307'),
    ('199.999', '363'), ('200', '500'), ('200.001', '500'), ('224.999', '500'), ('225', '509'),
])
def test_synthetic_tier_boundaries(weight, expected):
    assert wage_amount(weight, TIERS) == Decimal(expected)


def test_validation_checks_even_unselected_tiers_and_rejects_expressions():
    bad = [*TIERS[:-1], {'upper_bound': None, 'inclusive': False, 'formula': {'kind': 'FIXED', 'amount_vnd': '__import__("os")'}}]
    with pytest.raises(ValueError):
        wage_amount('10', bad)
    with pytest.raises(ValueError):
        wage_amount('NaN', TIERS)
    with pytest.raises(ValueError):
        wage_amount('2', TIERS[:-1])


def test_warehouse_pool_counts_inclusive_days_and_never_allocates():
    result = warehouse_labor_pool('2026-01-01', '2026-01-03', '123')
    assert result == {'days': 3, 'shared_pool_vnd': '369', 'supra_vnd': None}
    with pytest.raises(ValueError):
        warehouse_labor_pool('2026-01-31', '2026-02-01', '123')


def test_points_deduplicate_store_day_and_hold_conflicting_classification():
    def row(day, store, category, valid=True):
        return dict(day=day, store=store, category=category, valid=valid, source='SYN')
    rows = [row('2026-01-01', 'A', 'A'), row('2026-01-01', 'A', 'A'),
            row('2026-01-01', 'B', 'B'), row('2026-01-01', 'B', 'A'),
            row('2026-01-02', '', 'A'), row('2026-01-02', 'C', 'B')]
    out = delivery_points(rows, {'A': '10', 'B': '25'})
    assert out['counts'] == {'A': 1, 'B': 1} and out['eligible_vnd'] == '35'
    assert len(out['held']) == 2 and not out['complete'] and out['total_cost_vnd'] is None


def test_ftl_deduplicates_weight_per_order_and_never_guesses_extra_trips():
    def row(day, vehicle, order, weight, valid=True):
        return dict(day=day, vehicle=vehicle, order=order, weight_kg=weight, weight_status='VALID', class_kg='100', valid=valid, source='SYN')
    rows = [row('2026-01-01', 'X', 'A', '40'), row('2026-01-01', 'X', 'A', '40'),
            row('2026-01-01', 'X', 'B', '60'), row('2026-01-02', 'X', 'C', '101'),
            row('2026-01-03', 'X', 'D', '80'), row('', '', 'D', '80')]
    out = ftl_base_trips(rows, '100', '500')
    assert out['eligible_trips'] == 1 and out['eligible_vnd'] == '500'
    assert any('above_capacity' in x['reason'] for x in out['held'])
    assert any('partial_or_conflicting_scope' in x['reason'] for x in out['held'])
    assert not out['complete'] and out['total_cost_vnd'] is None
    forecast = ftl_base_trips(rows, '100', '500', ceil_confirmed=True)
    assert forecast['eligible_groups'] == 2 and forecast['eligible_trips'] == 3 and forecast['eligible_vnd'] == '1500'
    rows[3]['class_kg'] = '999'
    assert ftl_base_trips(rows, '100', '500', ceil_confirmed=True)['eligible_groups'] == 1
    rows[3]['class_kg'] = '100'
    context={('2026-01-01','X'):['100','200']}
    out=ftl_base_trips(rows,'100','500',ceil_confirmed=True,vehicle_classes=context)
    assert out['eligible_groups']==1 and any('mixed_or_unknown_class' in x['reason'] for x in out['held'])


@pytest.mark.parametrize(('second_status','second_weight','expected'), [
    ('MISSING', '', True), ('VALID', '40', True), ('INVALID', '', False),
    ('VALID', '41', False),
])
def test_ftl_distinguishes_blank_invalid_conflicting_and_repeated_weight(second_status,second_weight,expected):
    base=dict(day='2026-01-01',vehicle='X',order='A',weight_kg='40',weight_status='VALID',class_kg='100',valid=True,source='S1')
    second={**base,'weight_kg':second_weight,'weight_status':second_status,'source':'S2'}
    result=ftl_base_trips([base,second],'100','500',ceil_confirmed=True)
    assert result['eligible_groups']==int(expected)
    assert result['eligible_vnd']==('500' if expected else None)
    assert bool(result['held']) is not expected
    missing=[{**base,'weight_kg':'','weight_status':'MISSING'},second|{'weight_kg':'','weight_status':'MISSING'}]
    assert ftl_base_trips(missing,'100','500',ceil_confirmed=True)['eligible_groups']==0


def test_gxt_uses_individual_vehicle_day_shares_and_order_wages():
    def group(day, vehicle, orders, other, valid=True):
        return dict(day=day, vehicle=vehicle, supra_orders=orders, other_kg=other, valid=valid, sources=['SYN'])
    groups = [group('2026-01-01', 'X', {'A': '10'}, '30'),
              group('2026-01-01', 'Y', {'B': '100'}, '0'),
              group('2026-01-02', 'Z', {'C': '10'}, '10', False)]
    out = gxt_daily_cost(groups, '400', TIERS)
    assert out['eligible_vehicle_days'] == 2 and Decimal(out['vehicle_pool_vnd']) == 800
    assert Decimal(out['vehicle_supra_vnd']) == 500
    assert Decimal(out['wage_supra_vnd']) == 330
    assert len(out['held']) == 1 and not out['complete'] and out['total_cost_vnd'] is None
    with pytest.raises(ValueError):
        gxt_daily_cost([groups[0], groups[0]], '400', TIERS)


def test_ftl_multi_class_applies_only_confirmed_counting_method():
    def row(vehicle,order,weight,klass):
        return dict(day='2026-01-01',vehicle=vehicle,order=order,weight_kg=weight,weight_status='VALID',class_kg=klass,valid=True,source=order)
    tariffs=[dict(class_kg='100',rate_vnd='500',method='CEIL_WEIGHT'),dict(class_kg='200',rate_vnd='400',method='ONE_IF_WITHIN_CAPACITY'),dict(class_kg='300',rate_vnd='600',method='ONE_IF_WITHIN_CAPACITY')]
    result=ftl_base_trips([row('A','OA','180','100'),row('B','OB','190','200'),row('C','OC','301','300'),row('D','OD','250','300')],'100','500',ceil_confirmed=True,tariffs=tariffs)
    assert result['eligible_groups']==3 and result['eligible_trips']==4 and result['eligible_vnd']=='2000'
    assert len(result['held'])==1 and 'above_capacity_no_trip_algorithm' in result['held'][0]['reason']
    assert result['tariff_breakdown']['100']['trips']==2
