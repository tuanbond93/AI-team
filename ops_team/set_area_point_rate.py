"""CLI: owner sets a point fee for Raw deliveries with no plate in one area (owner rule 29/10/2026).

python -m ops_team.set_area_point_rate --root <vault> --province "Sơn La" --district "Mộc Châu" --vnd 100000
The rate is stored only in the DPAPI rule layer (never in Git); the layer is
converted to v2 if needed, fully validated, saved atomically and audited.
No Telegram, no model, no plan/schedule change.
"""
import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path


def set_rate(state, owner_id, province, district, vnd):
    from .telegram_snapshot import load_snapshot
    from .telegram_rule_layer import FILENAME, SCHEMA_V2, DECISIONS_FILENAME, load_rules, validate_rules_v2
    from .telegram_decisions import to_v2
    from .telegram_secrets import load_protected, save_protected
    from .supra.booking_handling import geography, district_name
    facts = {k: r['value'] for k, r in state.facts().items()}
    data, source = load_snapshot(state, facts)
    rules, _ = load_rules(state, data, source, facts, owner_id)
    if not rules:
        raise ValueError('Chưa có lớp quy tắc owner đang hiệu lực.')
    previous = load_protected(state.root/FILENAME)
    stored = json.loads(previous)
    v2 = stored if stored['schema'] == SCHEMA_V2 else to_v2(rules, data)
    area = (geography(province, province=True), district_name(district))
    items = [x for x in v2.get('blank_plate_points', []) if (geography(x['province'], province=True), district_name(x['district'])) != area]
    v2['blank_plate_points'] = items+[{'province': province, 'district': district, 'vnd_per_point': str(vnd)}]
    validate_rules_v2(v2, state, data, source, facts, owner_id)
    encoded = json.dumps(v2, ensure_ascii=False).encode()
    record = {'at': datetime.now().astimezone().isoformat(timespec='seconds'), 'owner_id': owner_id, 'decision': {'kind': 'blank_plate_points',
              'province': province, 'district': district, 'vnd_per_point': str(vnd)}, 'previous_rules_sha256': hashlib.sha256(previous).hexdigest(),
              'new_rules_sha256': hashlib.sha256(encoded).hexdigest()}
    path = state.root/DECISIONS_FILENAME
    log = json.loads(load_protected(path)) if path.exists() else []
    save_protected(state.root/FILENAME, encoded)
    try:
        save_protected(path, json.dumps(log+[record], ensure_ascii=False).encode())
    except Exception:
        save_protected(state.root/FILENAME, previous)
        raise
    return record


def main():
    from .telegram_state import BusinessState
    from .telegram_secrets import load_settings
    parser = argparse.ArgumentParser()
    parser.add_argument('--root')
    parser.add_argument('--province', required=True)
    parser.add_argument('--district', required=True)
    parser.add_argument('--vnd', required=True)
    args = parser.parse_args()
    state = None
    try:
        root = Path(args.root) if args.root else None
        state = BusinessState(root) if root else BusinessState()
        record = set_rate(state, load_settings(root)['owner_id'], args.province, args.district, args.vnd)
        print('Đã lưu giá điểm cho '+args.district+'. Mã audit '+record['new_rules_sha256'][:12]+'.')
        return 0
    except Exception as exc:
        print('Chưa lưu được: '+str(exc)[:300])
        return 1
    finally:
        if state:
            state.close()


if __name__ == '__main__':
    sys.exit(main())
