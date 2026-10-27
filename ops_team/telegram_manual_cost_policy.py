"""Owner-protected manual cost policy; never changes financial/model plans."""
import hashlib
import json
import re
from pathlib import Path

from .telegram_secrets import load_protected, save_protected
from .telegram_vehicle_forecast import MULTI_SCHEMA, validate_forecast
from .supra.manual_cost_rules import _number, wage_amount

SCHEMA = 'supra-manual-cost-policy-v1'
SCHEMA_V2 = 'supra-manual-cost-policy-v2'
FILENAME = 'manual-cost-policy.dpapi'


def validate_policy(policy, data, source, facts, owner_id, paired):
    """Validate exact schema and source/catalog binding before atomic import."""
    try:
        required = {'schema', 'forecast_binding', 'forecast_sha256', 'selectors', 'rates', 'wage', 'provenance', 'ftl_ceil_confirmed', 'package_selectors_complete'}
        if not isinstance(policy,dict) or policy.get('schema') not in (SCHEMA,SCHEMA_V2):
            raise ValueError
        multi=policy['schema']==SCHEMA_V2
        if multi:required|={'ftl_tariffs','remote_km_status'}
        if set(policy)!=required:raise ValueError
        if type(policy['ftl_ceil_confirmed']) is not bool or type(policy['package_selectors_complete']) is not bool:
            raise ValueError
        binding = policy['forecast_binding']
        if not isinstance(policy['forecast_sha256'], str) or not re.fullmatch('[a-f0-9]{64}', policy['forecast_sha256']):
            raise ValueError
        if not isinstance(binding, dict) or binding.get('schema') != MULTI_SCHEMA:
            raise ValueError
        validate_forecast(binding, data, source, facts, owner_id, paired)
        selectors = policy['selectors']
        allowed_modes = {'point_bc': {'TRANSFER_REP_LASTMILE', 'TRANSFER_GXT'}, 'point_lc': {'LAI_CHAU_DIRECT'},
                         'ftl': {'NCC_DIRECT'}, 'gxt': {'TRANSFER_GXT', 'GXT_DIRECT'}}
        if multi:allowed_modes['point_remote']={'LAI_CHAU_DIRECT','NCC_DIRECT'}
        if not isinstance(selectors, dict) or set(selectors) != set(allowed_modes):
            raise ValueError
        routes = {r['rule_id']: r for r in data.vehicle_pair['vehicle_catalog']['routing']}
        seen = set()
        for key, modes in allowed_modes.items():
            ids = selectors[key]
            if not isinstance(ids, list) or not ids or len(ids) != len(set(ids)):
                raise ValueError
            for ident in ids:
                if not isinstance(ident, str) or ident in seen or ident not in routes:
                    raise ValueError
                route = routes[ident]
                if route['mode'] not in modes or not (route['from'] <= binding['window']['start'] <= binding['window']['end'] <= route['to']):
                    raise ValueError
                seen.add(ident)
        rates = policy['rates']
        rate_keys={'point_bc_vnd', 'point_lc_vnd', 'ftl_vnd', 'ftl_capacity_kg', 'gxt_vehicle_day_vnd', 'warehouse_labor_day_vnd'}
        if multi:rate_keys.add('point_remote_vnd')
        if not isinstance(rates, dict) or set(rates) != rate_keys:
            raise ValueError
        for value in rates.values():
            _number(value, positive=True)
        if multi:
            if policy['remote_km_status']!='UNAVAILABLE':raise ValueError
            tariffs=policy['ftl_tariffs']
            if not isinstance(tariffs,list) or not 1<=len(tariffs)<=20:raise ValueError
            seen_classes=set();legacy_match=False
            for t in tariffs:
                if not isinstance(t,dict) or set(t)!={'class_kg','rate_vnd','method'} or t['method'] not in ('CEIL_WEIGHT','ONE_IF_WITHIN_CAPACITY'):raise ValueError
                klass=_number(t['class_kg'],positive=True);_number(t['rate_vnd'],positive=True)
                if klass in seen_classes:raise ValueError
                seen_classes.add(klass)
                if klass==_number(rates['ftl_capacity_kg']):
                    legacy_match=(_number(t['rate_vnd'])==_number(rates['ftl_vnd']) and (t['method']=='CEIL_WEIGHT')==policy['ftl_ceil_confirmed'])
                elif t['method']=='CEIL_WEIGHT':
                    raise ValueError
            if not legacy_match:raise ValueError
        wage = policy['wage']
        if not isinstance(wage, dict) or set(wage) != {'source_sha256', 'extraction_sha256', 'tiers', 'unit'} or wage['unit'] != 'VND_PER_GHN_BY_KG':
            raise ValueError
        for name in ('source_sha256', 'extraction_sha256'):
            if not isinstance(wage[name], str) or not re.fullmatch('[a-f0-9]{64}', wage[name]):
                raise ValueError
        wage_amount('0', wage['tiers'])
        if policy['provenance'] != 'USER_CONFIRMED_IN_CODEX':
            raise ValueError
        return policy
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        raise ValueError('Chính sách chi phí chưa khớp owner, nguồn, catalog, phạm vi hoặc quy tắc đã chốt.') from None


def ingest_policy(path, descriptor, state, owner_id, *, cancel=None):
    def stop():
        if cancel is not None and cancel.is_set():
            raise ValueError('Đã hủy; chính sách trước giữ nguyên.')
    stop()
    p = Path(path)
    if not p.is_absolute() or p.suffix.lower() != '.dpapi' or p.stat().st_size > 1024 * 1024:
        raise ValueError('Cần file DPAPI chính sách tuyệt đối, tối đa 1 MB.')
    raw = load_protected(p)
    sha = hashlib.sha256(raw).hexdigest()
    if not isinstance(descriptor, dict) or set(descriptor) != {'schema', 'sha256'} or descriptor['schema'] not in (SCHEMA,SCHEMA_V2) or descriptor['sha256'] != sha:
        raise ValueError('Descriptor/hash chính sách sai.')
    config = json.loads(raw)
    if config.get('schema')!=descriptor['schema']:raise ValueError('Schema không khớp.')
    from .telegram_vehicle_forecast import FILENAME as FORECAST_FILENAME
    forecast_raw = load_protected(state.root / FORECAST_FILENAME)
    if hashlib.sha256(forecast_raw).hexdigest() != config.get('forecast_sha256') or json.loads(forecast_raw) != config.get('forecast_binding'):
        raise ValueError('Kịch bản xe chưa khớp chính sách.')
    facts = {k: row['value'] for k, row in state.facts().items()}
    from .telegram_snapshot import load_snapshot
    data, source = load_snapshot(state, facts)
    validate_policy(config, data, source, facts, owner_id, state.control('paired'))
    stop()
    save_protected(state.root / FILENAME, raw)
    return {'state': 'SAVED_MANUAL_ONLY', 'policy_sha256': sha, 'plan_or_schedule_changed': False}


def main():
    import argparse
    import sys
    from .telegram_secrets import load_settings
    from .telegram_state import BusinessState
    state = None
    try:
        parser = argparse.ArgumentParser(description='Protected manual cost policy; descriptor on stdin')
        parser.add_argument('--minimized', required=True)
        args = parser.parse_args()
        descriptor = sys.stdin.buffer.read(1024*1024+1)
        if len(descriptor) > 1024*1024:
            raise ValueError
        settings = load_settings()
        state = BusinessState()
        result = ingest_policy(args.minimized, json.loads(descriptor), state, settings['owner_id'])
        print(json.dumps(result))
        return 0
    except Exception:
        print('{"state":"REJECTED","reason":"Kiểm tra owner, nguồn, kịch bản xe và chính sách; cấu hình cũ giữ nguyên."}')
        return 1
    finally:
        if state:
            state.close()


if __name__ == '__main__':
    import sys
    sys.exit(main())
