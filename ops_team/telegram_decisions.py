"""Owner confirms source-bound exceptions in Telegram (owner rule 28/10/2026).

Only exceptions the code can bind exactly are offered: today, adding the
distinct L parts of one GHN + Booking E day. The owner previews, then confirms
with an explicit second command. Saving writes a rule layer v2 atomically
(DPAPI) after full validation and appends an audit record; nothing else
(plan, schedule, prices, sources) changes.
"""
import hashlib
import json
from datetime import datetime
from decimal import Decimal

from .supra.booking_weight import parse_kg
from .telegram_rule_layer import (FILENAME, SCHEMA_V2, DECISIONS_FILENAME, _part_key, validate_rules_v2)
from .telegram_secrets import load_protected, save_protected


def proposals(report, data):
    """Conflicting-L events (GHN/day) whose every non-blank L is valid: offer 'add the parts'."""
    rows = data.rows.to_dict('records')
    out = []
    for item in report['dc'].get('group_evidence', []):
        if item.get('issues') != ['conflicting_weight'] or not item.get('day'):
            continue
        related = [r for r in rows if r['order_id'] == item['order_id'] and str(r['dispatch_date']) == item['day']]
        filled = [r for r in related if parse_kg(r['source_ghn_weight'])[1] != 'MISSING']
        parts = sorted(list(_part_key(r)) for r in filled)
        if any(p[2] is None for p in parts) or len({p[2] for p in parts}) < 2 or len({tuple(p) for p in parts}) != len(parts):
            continue
        body = {'kind': 'handling_parts', 'order_id': item['order_id'], 'day': item['day'], 'parts': parts}
        code = 'P'+hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:6].upper()
        out.append({'code': code, 'body': body, 'sources': [r['_source'] for r in filled]})
    return out


def describe(proposal):
    from .supra.missing_data import _rows_text, _vi_day
    parts = [Decimal(p[2]) for p in proposal['body']['parts']]
    def kg(value):
        from .supra.vehicle_report import _vi_money
        return _vi_money(value)
    return (f"{proposal['code']}: cộng các phần kg {' + '.join(kg(x) for x in parts)} = {kg(sum(parts))} kg cho đợt bốc xếp ngày "
            f"{_vi_day(proposal['body']['day'])} ({_rows_text(proposal['sources'])}).")


def to_v2(rules, data):
    """Convert a validated v1 layer to v2 business keys (SO, DO, kg of each L part)."""
    parts = []
    for item in rules['handling_parts']:
        rows = data.rows.loc[data.rows._source.isin(item['sources'])].to_dict('records')
        parts.append({'order_id': item['order_id'], 'day': item['day'], 'parts': sorted(list(_part_key(r)) for r in rows)})
    keys = ('base_sha256', 'forecast_sha256', 'overlay_sha256', 'vehicle_catalog_sha256', 'handling_catalog_sha256', 'window',
            'direct_gxt_two_legs', 'booked_class_basis', 'provenance')
    v2 = {k: rules[k] for k in keys}
    v2.update(schema=SCHEMA_V2, handling_parts=parts,
        opening_ftl=[{k: x[k] for k in ('order_id', 'day', 'class_kg', 'pricing')} for x in rules['opening_ftl']])
    return v2


def apply(proposal, rules, state, data, source, facts, owner_id, request):
    body = proposal['body']
    if not rules['window']['start'] <= body['day'] <= rules['window']['end']:
        raise ValueError('Ngày của đợt nằm ngoài khoảng quy tắc owner đã duyệt.')
    previous = load_protected(state.root/FILENAME)
    stored = json.loads(previous)
    # Keep stored v2 entries verbatim (including ones stale for this snapshot); convert v1 once.
    v2 = stored if stored.get('schema') == SCHEMA_V2 else to_v2(rules, data)
    if any(x['order_id'] == body['order_id'] and x['day'] == body['day'] for x in v2['handling_parts']):
        raise ValueError('Đợt này đã có ngoại lệ.')
    v2['handling_parts'].append({'order_id': body['order_id'], 'day': body['day'], 'parts': body['parts']})
    record = {'at': datetime.now().astimezone().isoformat(timespec='seconds'), 'owner_id': owner_id, 'chat_id': request.chat_id,
              'message_id': request.message_id, 'code': proposal['code'], 'decision': body,
              'previous_rules_sha256': hashlib.sha256(previous).hexdigest()}
    evidence = hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    v2['provenance'] = {'source': 'OWNER_CONFIRMED_IN_TELEGRAM', 'confirmed_date': record['at'][:10], 'evidence_sha256': evidence}
    validate_rules_v2(v2, state, data, source, facts, owner_id)
    encoded = json.dumps(v2, ensure_ascii=False).encode()
    record['new_rules_sha256'] = hashlib.sha256(encoded).hexdigest()
    path = state.root/DECISIONS_FILENAME
    log = json.loads(load_protected(path)) if path.exists() else []
    save_protected(state.root/FILENAME, encoded)
    try:
        save_protected(path, json.dumps(log+[record], ensure_ascii=False).encode())
    except Exception:
        save_protected(state.root/FILENAME, previous)  # no rule change without its audit record
        raise
    return record
