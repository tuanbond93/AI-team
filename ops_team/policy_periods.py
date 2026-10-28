"""Monthly roll-forward of approved prices and rules (owner rule 28/10/2026, option A).

Prices, catalogs and owner rules stay unchanged into the next month until the
owner says otherwise; the bot reminds the owner to confirm each new month.

Layout: the current month's set lives at the vault root (unchanged paths);
earlier months live in ``periods/<YYYY-MM>/`` so a previous month can still be
reconciled against NCC invoices. A roll:

1. validates the whole current set against the current snapshot;
2. extends both catalogs: every interval ending on the last day of the current
   month gets a copy for the next month (new id, same values); older intervals
   are kept as they are;
3. re-imports the same snapshot with the extended catalogs;
4. rebinds the current month's set to the new catalog hashes and archives it;
5. writes the next month's set (window, prices, route ids moved to the next
   month; one-off exceptions are not carried) and validates it;
6. records the roll as unconfirmed.

Any failure restores every file and the snapshot pointer; the month then stays
N/A until the owner imports a set.
"""
import calendar
import hashlib
import json
import re
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path

from .telegram_secrets import load_protected, save_protected
from .telegram_vehicle_forecast import FILENAME as FORECAST_FILENAME, validate_forecast
from .telegram_manual_cost_policy import FILENAME as BASE_FILENAME, validate_policy
from .telegram_cost_overlay import FILENAME as OVERLAY_FILENAME, validate_overlay
from .telegram_rule_layer import FILENAME as RULES_FILENAME, SCHEMA_V2, load_rules, validate_rules_v2

ROLLOVER_FILENAME = 'rollover.dpapi'
SET_FILES = (FORECAST_FILENAME, BASE_FILENAME, OVERLAY_FILENAME, RULES_FILENAME)


class PeriodState:
    """Read/write one month's policy files; controls and facts come from the real vault."""

    def __init__(self, state, root):
        self._state = state
        self.root = Path(root)

    def __getattr__(self, name):
        return getattr(self._state, name)


def month_bounds(month):
    first = date.fromisoformat(month+'-01')
    return first, date(first.year, first.month, calendar.monthrange(first.year, first.month)[1])


def next_month(month):
    first, last = month_bounds(month)
    return date.fromordinal(last.toordinal()+1).strftime('%Y-%m')


def root_period(state):
    path = state.root/FORECAST_FILENAME
    return json.loads(load_protected(path))['period'] if path.exists() else None


def state_for_month(state, month):
    """The vault view holding ``month``'s set (root for the current month)."""
    archived = state.root/'periods'/month
    if root_period(state) != month and (archived/FORECAST_FILENAME).exists():
        return PeriodState(state, archived)
    return state


def rollovers(state):
    path = state.root/ROLLOVER_FILENAME
    return json.loads(load_protected(path)) if path.exists() else {}


def unconfirmed_notice(state, month):
    item = rollovers(state).get(month)
    if item and not item.get('confirmed'):
        return (f"Lưu ý: giá và quy tắc tháng {item['from'][5:]}/{item['from'][:4]} đang được giữ nguyên cho tháng {month[5:]}/{month[:4]}, "
                "anh chưa xác nhận. Gửi /giugia để xác nhận, hoặc báo thay đổi giá/quy tắc.")
    return None


def confirm(state, month, owner_id, message_id):
    data = rollovers(state)
    if month not in data or data[month].get('confirmed'):
        return False
    data[month].update(confirmed=True, confirmed_by=owner_id, confirmed_at=datetime.now().astimezone().isoformat(timespec='seconds'),
                       message_id=message_id)
    save_protected(state.root/ROLLOVER_FILENAME, json.dumps(data, ensure_ascii=False).encode())
    return True


def _base_id(ident):
    return re.sub(r'_[0-9]{6}$', '', ident)


def _roll_intervals(items, id_key, month_from, month_to):
    """Copy intervals ending on ``month_from``'s last day into ``month_to``; return (items, id map)."""
    _, old_last = month_bounds(month_from)
    first, last = month_bounds(month_to)
    mapping, out = {}, list(items)
    for item in items:
        if item['to'] == str(old_last):
            new_id = _base_id(item[id_key])+'_'+month_to.replace('-', '')
            mapping[item[id_key]] = new_id
            out.append({**item, id_key: new_id, 'from': str(first), 'to': str(last)})
    return out, mapping


def _encode(obj):
    raw = json.dumps(obj, ensure_ascii=False).encode()
    return raw, hashlib.sha256(raw).hexdigest()


def roll_forward(state, owner_id, target):
    from .telegram_snapshot import load_snapshot
    from .telegram_vehicle_snapshot import _read_record, ingest_vehicle_pair
    from .telegram_decisions import to_v2
    from .drive_import import pair_descriptor
    from .supra.booking_handling import HANDLING_POLICY, validate_catalog, catalog_digest
    from .supra.booking_vehicles import validate_vehicle_catalog, digest
    current = root_period(state)
    if not current or next_month(current) != target:
        raise ValueError('Chỉ chuyển sang đúng tháng kế tiếp của bộ chính sách hiện tại.')
    if (state.root/'periods'/current).exists():
        raise ValueError('Tháng hiện tại đã được lưu trữ trước đó.')
    facts = {k: r['value'] for k, r in state.facts().items()}
    paired = state.control('paired')
    data, source = load_snapshot(state, facts)
    raws = {name: load_protected(state.root/name) for name in SET_FILES}
    forecast, base, overlay = (json.loads(raws[n]) for n in (FORECAST_FILENAME, BASE_FILENAME, OVERLAY_FILENAME))
    validate_forecast(forecast, data, source, facts, owner_id, paired)
    validate_policy(base, data, source, facts, owner_id, paired)
    validate_overlay(overlay, base, raws[BASE_FILENAME], raws[FORECAST_FILENAME], data, source, facts, owner_id, paired)
    stored_rules = json.loads(raws[RULES_FILENAME])
    rules, _ = load_rules(state, data, source, facts, owner_id)
    rules_v2 = stored_rules if stored_rules['schema'] == SCHEMA_V2 else to_v2(rules, data)

    record = _read_record(state)
    pair = record['vehicle_pair']
    handling = deepcopy(record['handling_catalog'])
    vehicle = deepcopy(pair['vehicle_catalog'])
    handling['rates'], _ = _roll_intervals(handling['rates'], 'rate_id', current, target)
    handling['routing'], _ = _roll_intervals(handling['routing'], 'rule_id', current, target)
    vehicle['routing'], ids = _roll_intervals(vehicle['routing'], 'rule_id', current, target)
    handling['period'] = vehicle['period'] = target
    booking_pin = {k: record['source'][k] for k in ('spreadsheet_id', 'tab', 'gid')}
    validate_catalog(handling, booking_pin, HANDLING_POLICY)
    validate_vehicle_catalog(vehicle, vehicle['source_pins']['booking'], pair['data_supra'])
    booking = {k: record['source'][k] for k in ('source_url', 'spreadsheet_id', 'tab', 'gid', 'range', 'retrieved_at')}
    booking.update(raw_sha256=record['export']['sha256'], raw_column_count=37, headers=record['business_evidence']['headers'],
                   rows=record['business_evidence']['rows'])
    payload = dict(schema=pair['schema'], booking=booking, data_supra=pair['data_supra'],
        confirmed_business_rules={k: v for k, v in record['quantity_rules'].items() if k != 'version'},
        confirmed_weight_rules={k: v for k, v in record['weight_rules'].items() if k != 'version'},
        confirmed_handling_policy=record['handling_policy'], handling_catalog=handling, handling_catalog_sha256=catalog_digest(handling),
        vehicle_policy=pair['vehicle_policy'], vehicle_catalog=vehicle, vehicle_catalog_sha256=digest(vehicle))

    pointer = state.root/'snapshot-latest.dpapi'
    backup = {'pointer': pointer.read_bytes(), **{n: (state.root/n).read_bytes() for n in SET_FILES}}
    archive = state.root/'periods'/current
    try:
        encoded, descriptor = pair_descriptor(payload)
        staging = state.root/'staging'/'rollover-pair.dpapi'
        save_protected(staging, encoded)
        try:
            ingest_vehicle_pair(staging.resolve(), descriptor, state, owner_id)
        finally:
            staging.unlink(missing_ok=True)
        data, source = load_snapshot(state, facts)
        shas = dict(vehicle_catalog_sha256=data.vehicle_pair['vehicle_catalog_sha256'], handling_catalog_sha256=data.handling_catalog_sha256)

        def write_set(view, forecast_obj, base_obj, overlay_obj, rules_obj):
            forecast_obj = {**forecast_obj, **shas}
            f_raw, f_sha = _encode(forecast_obj)
            b_raw, b_sha = _encode({**base_obj, 'forecast_binding': forecast_obj, 'forecast_sha256': f_sha})
            o_raw, o_sha = _encode({**overlay_obj, 'base_policy_sha256': b_sha, 'forecast_sha256': f_sha})
            r_raw, _ = _encode({**rules_obj, **shas, 'base_sha256': b_sha, 'forecast_sha256': f_sha, 'overlay_sha256': o_sha})
            for name, raw in ((FORECAST_FILENAME, f_raw), (BASE_FILENAME, b_raw), (OVERLAY_FILENAME, o_raw), (RULES_FILENAME, r_raw)):
                save_protected(view.root/name, raw)
            validate_forecast(forecast_obj, data, source, facts, owner_id, paired)
            validate_policy(json.loads(b_raw), data, source, facts, owner_id, paired)
            validate_overlay(json.loads(o_raw), json.loads(b_raw), b_raw, f_raw, data, source, facts, owner_id, paired)
            validate_rules_v2(json.loads(r_raw), view, data, source, facts, owner_id)

        write_set(PeriodState(state, archive), forecast, base, overlay, rules_v2)
        first, last = month_bounds(target)
        window = {'start': str(first), 'end': str(last)}
        nf = {**forecast, 'period': target, 'window': window, 'version': forecast['version']+'-'+target,
              'route_selector': {**forecast['route_selector'], 'routes': [{**r, 'rule_id': ids[r['rule_id']]} for r in forecast['route_selector']['routes']]}}
        nb = {**base, 'selectors': {k: [ids[i] for i in v] for k, v in base['selectors'].items()}}
        prices, _ = _roll_intervals([{**p, 'id': str(i)} for i, p in enumerate(overlay['revenue']['prices'])], 'id', current, target)
        prices = [{k: v for k, v in p.items() if k != 'id'} for p in prices if p['from'] >= str(first)]
        no = {**overlay, 'revenue': {**overlay['revenue'], 'prices': prices}}
        if 'scope_exclusions' in overlay:
            no['scope_exclusions'] = {**overlay['scope_exclusions'], 'window': dict(window)}
        nr = {**rules_v2, 'window': dict(window), 'handling_parts': [], 'opening_ftl': []}
        write_set(state, nf, nb, no, nr)
    except Exception:
        pointer.write_bytes(backup['pointer'])
        for name in SET_FILES:
            (state.root/name).write_bytes(backup[name])
            (archive/name).unlink(missing_ok=True)
        raise
    log = rollovers(state)
    log[target] = {'from': current, 'at': datetime.now().astimezone().isoformat(timespec='seconds'), 'confirmed': False}
    save_protected(state.root/ROLLOVER_FILENAME, json.dumps(log, ensure_ascii=False).encode())
    return {'state': 'ROLLED_UNCONFIRMED', 'from': current, 'to': target}
