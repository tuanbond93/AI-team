"""Import the Booking + Raw CSVs that the owner's Apps Script writes to a synced Drive folder.

Flow (owner rule 27/10/2026): Apps Script runs under the owner's own company
account (Viewer access is enough), exports the two pinned tabs to CSV with a
manifest in one Drive folder; Google Drive for desktop syncs that folder to
the Windows machine; this module validates the files and reuses the existing
paired import (`ingest_vehicle_pair`). Catalogs and policies always come from
the currently approved vault record, never from the folder. No network access,
no credentials, nothing written outside the vault.
"""
import csv
import hashlib
import json
import re
from datetime import datetime
from io import StringIO
from pathlib import Path

from .supra.booking import normalized, verify_export_headers
from .supra.booking_quantity import RULE
from .supra.booking_weight import WEIGHT_RULE
from .supra.booking_handling import HANDLING_POLICY
from .supra.booking_vehicles import PAIR_SCHEMA, BOOK_HEADERS, RAW_HEADERS, VEHICLE_POLICY
from .telegram_secrets import save_protected

MANIFEST = 'supra-export-manifest.json'
MANIFEST_SCHEMA = 'supra-drive-export-v1'
MAX_BYTES = 20*1024*1024


def _token(value):
    return ''.join(c for c in normalized(value) if c.isalnum())


def _rows(raw, count):
    reader = csv.reader(StringIO(raw.decode('utf-8-sig'), newline=''), strict=True)
    headers = next(reader)
    rows = []
    for i, row in enumerate(reader, 2):
        if len(row) > count:
            raise ValueError('CSV có dòng vượt số cột của tab.')
        if any(v != '' for v in row):
            rows.append((i, row+['']*(count-len(row))))
        if i > 100001:
            raise ValueError('CSV vượt 100.000 dòng.')
    return headers, rows


def _project(headers, rows, wanted):
    index = {}
    for name in wanted:
        hits = [i for i, h in enumerate(headers) if _token(h) == _token(name)]
        if len(hits) != 1:
            raise ValueError('Thiếu hoặc trùng cột nguồn cần dùng.')
        index[name] = hits[0]
    return [dict(sheet_row=n, values=[row[index[name]] for name in wanted]) for n, row in rows]


def _source(meta, pinned, raw_bytes, headers, rows, count, wanted, exported_at):
    for key in ('spreadsheet_id', 'tab', 'gid', 'range'):
        if str(meta[key]) != str(pinned[key]):
            raise ValueError('File xuất không đúng nguồn/tab/range đã ghim.')
    end = int(re.search(r'([0-9]+)$', pinned['range'])[1])
    if rows and rows[-1][0] > end:
        raise ValueError('File xuất có dòng ngoài range đã ghim.')
    if hashlib.sha256(raw_bytes).hexdigest() != meta['sha256'] or meta['rows'] != len(rows):
        raise ValueError('Manifest không khớp file CSV; có thể đồng bộ chưa xong.')
    return dict(source_url=f"https://docs.google.com/spreadsheets/d/{pinned['spreadsheet_id']}/edit#gid={pinned['gid']}",
        spreadsheet_id=pinned['spreadsheet_id'], tab=pinned['tab'], gid=pinned['gid'], range=pinned['range'],
        retrieved_at=exported_at, raw_sha256=meta['sha256'], raw_column_count=count, headers=list(wanted),
        rows=_project(headers, rows, wanted))


def build_pair_from_folder(folder, previous):
    """Return (payload, descriptor) for `ingest_vehicle_pair`; raise ValueError on any mismatch."""
    root = Path(folder).resolve(strict=True)
    path = (root/MANIFEST).resolve(strict=True)
    if not path.is_relative_to(root) or path.stat().st_size > 64*1024:
        raise ValueError('Manifest sai vị trí hoặc quá lớn.')
    manifest = json.loads(path.read_bytes())
    if (not isinstance(manifest, dict) or set(manifest) != {'schema', 'exported_at', 'files'} or manifest['schema'] != MANIFEST_SCHEMA
            or not isinstance(manifest['files'], dict) or set(manifest['files']) != {'booking', 'raw'}):
        raise ValueError('Manifest sai schema.')
    exported = datetime.fromisoformat(manifest['exported_at'])
    if exported.tzinfo is None:
        raise ValueError('Manifest thiếu múi giờ.')
    if not previous or not previous.get('vehicle_pair'):
        raise ValueError('Chưa có bản paired đã duyệt để lấy catalog; nạp tay lần đầu.')
    pinned_booking = previous['source']
    pinned_raw = previous['vehicle_pair']['data_supra']
    parts = {}
    for key, pinned, count, wanted in (('booking', pinned_booking, 37, BOOK_HEADERS), ('raw', pinned_raw, 25, RAW_HEADERS)):
        meta = manifest['files'][key]
        if (not isinstance(meta, dict) or set(meta) != {'name', 'spreadsheet_id', 'tab', 'gid', 'range', 'sha256', 'rows'}
                or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}\.csv', str(meta['name'])) or not re.fullmatch('[a-f0-9]{64}', str(meta['sha256']))
                or type(meta['rows']) is not int):
            raise ValueError('Manifest file sai schema.')
        file = (root/meta['name']).resolve(strict=True)
        if not file.is_relative_to(root) or file.stat().st_size > MAX_BYTES:
            raise ValueError('File xuất sai vị trí hoặc quá lớn.')
        raw_bytes = file.read_bytes()
        headers, rows = _rows(raw_bytes, count)
        if key == 'booking':
            verify_export_headers(headers)
        elif len(headers) != 25:
            raise ValueError('Header Raw không đúng 25 cột.')
        parts[key] = _source(meta, pinned, raw_bytes, headers, rows, count, wanted, manifest['exported_at'])
    payload = dict(schema=PAIR_SCHEMA, booking=parts['booking'], data_supra=parts['raw'],
        confirmed_business_rules={k: v for k, v in RULE.items() if k != 'version'},
        confirmed_weight_rules={k: v for k, v in WEIGHT_RULE.items() if k != 'version'}, confirmed_handling_policy=HANDLING_POLICY,
        handling_catalog=previous['handling_catalog'], handling_catalog_sha256=previous['handling_catalog_sha256'],
        vehicle_policy=VEHICLE_POLICY, vehicle_catalog=previous['vehicle_pair']['vehicle_catalog'],
        vehicle_catalog_sha256=previous['vehicle_pair']['vehicle_catalog_sha256'])
    encoded = json.dumps(payload, ensure_ascii=False).encode()
    descriptor = {k: v for k, v in payload.items() if k not in ('booking', 'data_supra', 'handling_catalog', 'vehicle_catalog')}
    for k in ('booking', 'data_supra'):
        descriptor[k] = {k2: v for k2, v in payload[k].items() if k2 != 'rows'} | dict(row_count=len(payload[k]['rows']))
    descriptor.update(handling_catalog_version=payload['handling_catalog']['version'],
        vehicle_catalog_version=payload['vehicle_catalog']['version'], minimized_plaintext_sha256=hashlib.sha256(encoded).hexdigest())
    return encoded, descriptor


def import_folder(folder, state, owner_id):
    from .telegram_vehicle_snapshot import _read_record, ingest_vehicle_pair
    encoded, descriptor = build_pair_from_folder(folder, _read_record(state))
    staging = state.root/'staging'/'drive-pair.dpapi'
    save_protected(staging, encoded)
    try:
        return ingest_vehicle_pair(staging.resolve(), descriptor, state, owner_id)
    finally:
        staging.unlink(missing_ok=True)


def main():
    import argparse
    import sys
    from .telegram_state import BusinessState
    from .telegram_secrets import load_settings
    from .telegram_snapshot import send_scheduled
    state = None
    try:
        parser = argparse.ArgumentParser(description='Import Apps Script CSV export from a synced folder; one send per approved job')
        parser.add_argument('--folder', required=True)
        parser.add_argument('--send-scheduled', action='store_true')
        args = parser.parse_args()
        config = load_settings()
        state = BusinessState()
        result = import_folder(args.folder, state, config['owner_id'])
        if args.send_scheduled:
            result['delivery'] = send_scheduled(state, config)
        print(json.dumps({k: v for k, v in result.items() if k in ('state', 'digest', 'booking_rows', 'raw_rows', 'delivery')}))
        return 0
    except Exception:
        print('{"state":"REJECTED","reason":"Kiểm tra manifest, file CSV, nguồn đã ghim và bản paired đã duyệt; nguồn trước giữ nguyên."}')
        return 1
    finally:
        if state:
            state.close()


if __name__ == '__main__':
    import sys
    sys.exit(main())
