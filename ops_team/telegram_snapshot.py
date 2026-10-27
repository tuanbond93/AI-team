"""RAM stdin → PII-minimized, DPAPI snapshot from an authorized Google connector.

No browser cookies, OAuth tokens, HTTP listener, raw chat or model payload storage.
"""
from datetime import datetime, timezone
import hashlib
import csv
from io import StringIO
import json
from pathlib import Path
import re
import sys
import time
import pandas as pd
from .supra.booking import BookingData, parse_booking, normalized
from .telegram_sheets import spreadsheet_id, range_map
from .telegram_secrets import save_protected, load_protected


def assert_pin(source, facts):
    areas = range_map(facts.get("sheets_ranges", "{}"))
    if (set(areas) != {"supra_booking"} or source["spreadsheet_id"] != spreadsheet_id(facts.get("sheets_id", "")) or
            source["tab"] != facts.get("sheets_tab") or str(source["gid"]) != facts.get("sheets_gid") or
            source["range"] != areas["supra_booking"]):
        raise ValueError("Snapshot không khớp file/tab/gid/range owner đã khóa; không thay nguồn.")


def active_handling_config(state,source):
    """Reuse only a previously imported business approval on the SAME source."""
    from .supra.booking_handling import validate_catalog
    pointer=state.root/'snapshot-latest.dpapi'
    if not pointer.exists():return None,None,None
    digest=json.loads(load_protected(pointer))['digest']
    if not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest):
        raise ValueError('Snapshot pointer sai; chưa đọc policy bốc xếp.')
    encoded=load_protected(state.root/'snapshots'/(digest+'.dpapi'))
    if hashlib.sha256(encoded).hexdigest()!=digest:raise ValueError('Snapshot đổi hash; chưa đọc policy bốc xếp.')
    previous=json.loads(encoded)
    if any(str(previous['source'][k])!=str(source[k]) for k in ('spreadsheet_id','tab','gid','range')):
        return None,None,None
    catalog=previous.get('handling_catalog');policy=previous.get('handling_policy');sha=previous.get('handling_catalog_sha256')
    if catalog is None and policy is None and sha is None:return None,None,None
    if not isinstance(sha,str) or not re.fullmatch('[a-f0-9]{64}',sha):raise ValueError('Thiếu hash catalog bốc xếp đã duyệt.')
    validate_catalog(catalog,source,policy,sha)
    return policy,catalog,sha


def ingest_snapshot(payload, state, owner_id, *, export_meta=None):
    allowed = {"version", "source", "spreadsheet_id", "tab", "gid", "range", "retrieved_at", "headers", "rows"}
    if (not isinstance(payload, dict) or set(payload) != allowed or payload["version"] != 1 or
            payload["source"] not in ("GOOGLE_SHEETS_CONNECTOR", "GOOGLE_BROWSER_VIEWER_EXPORT")):
        raise ValueError("Snapshot schema/source chưa được hỗ trợ.")
    source = {k: payload[k] for k in ("spreadsheet_id", "tab", "gid", "range", "retrieved_at")}
    source["channel"] = payload["source"]
    source["source_url"] = f"https://docs.google.com/spreadsheets/d/{source['spreadsheet_id']}/edit#gid={source['gid']}"
    facts = {k: r["value"] for k, r in state.facts().items()}
    assert_pin(source, facts)
    stamp = datetime.fromisoformat(source["retrieved_at"])
    if stamp.tzinfo is None or stamp.timestamp() > time.time()+60:
        raise ValueError("retrieved_at cần timezone và không nằm trong tương lai.")
    headers, rows = payload["headers"], payload["rows"]
    if (not isinstance(headers, list) or not 1 <= len(headers) <= 100 or not all(isinstance(c, str) for c in headers)
            or len(set(headers)) != len(headers) or not isinstance(rows, list) or len(rows) > 100000):
        raise ValueError("Snapshot vượt giới hạn cột/dòng hoặc header sai.")
    if any(not isinstance(r, list) or len(r) > len(headers) or any(not isinstance(v, (str, int, float, type(None))) for v in r) for r in rows):
        raise ValueError("Snapshot rows chỉ nhận ô giá trị vô hướng.")
    start = int(re.search(r"![A-Z]+([0-9]+):", source["range"])[1])
    end = int(re.search(r":[A-Z]+([0-9]+)$", source["range"])[1])
    if len(rows) > end-start:
        raise ValueError("Snapshot vượt số dòng owner cho phép.")
    positions, values = [], []
    for index, row in enumerate(rows, start=start+1):
        if not any(v not in ("", None) for v in row):
            continue
        values.append([*row, *([None]*(len(headers)-len(row)))])
        positions.append(index)
    frame = pd.DataFrame(values, columns=headers,dtype=object)
    frame["_source"] = [f"GoogleConnector {source['spreadsheet_id']} / {source['range']} / dòng {r}" for r in positions]
    from .supra.booking_quantity import export_quantity_rule
    from .supra.booking_weight import export_weight_rule
    handling_policy,handling_catalog,handling_sha=active_handling_config(state,source)
    from .telegram_vehicle_snapshot import active_vehicle_pair
    vehicle_pair=active_vehicle_pair(state,source)
    if handling_policy is not None:
        from .supra.booking import verify_export_headers
        verify_export_headers(headers)
    safe = parse_booking(frame, quantity_rules=export_quantity_rule(headers,source),weight_rules=export_weight_rule(headers,source),
        handling_policy=handling_policy,handling_catalog=handling_catalog,handling_catalog_sha256=handling_sha,vehicle_fields=vehicle_pair is not None)
    record = {"schema_version": 4, "quantity_rules": safe.quantity_rules, 'weight_rules':safe.weight_rules,
              'handling_policy':handling_policy,'handling_catalog':handling_catalog,'handling_catalog_sha256':handling_sha,
              'vehicle_pair':vehicle_pair,
              "source": source, "actor": owner_id, "provenance": "LOCAL_AUTHORIZED_CONNECTOR_SNAPSHOT — not independent verification",
              "rows": safe.rows.to_dict(orient="records")}
    if safe.quantity_rules:
        from .supra.booking_quantity import BUSINESS_HEADERS, business_scalar
        from .supra.booking_handling import HANDLING_BUSINESS_HEADERS
        from .supra.booking_weight import WEIGHT_BUSINESS_HEADERS
        from .supra.booking_vehicles import BOOK_HEADERS
        evidence_headers=BOOK_HEADERS if vehicle_pair else HANDLING_BUSINESS_HEADERS if len(headers)==37 else WEIGHT_BUSINESS_HEADERS if safe.weight_rules else BUSINESS_HEADERS
        indexes=[next(j for j,c in enumerate(headers) if normalized(c)==normalized(h)) for h in evidence_headers]
        record['business_evidence']={'headers':evidence_headers,'rows':[
            {'sheet_row':pos,'values':[business_scalar(values[i][j]) for j in indexes]}
            for i,pos in enumerate(positions)]}
    if vehicle_pair:
        vehicle_pair['booking_retrieved_at']=source['retrieved_at']
        vehicle_pair['booking_digest']=hashlib.sha256(json.dumps({'source':source,'rows':safe.rows.to_dict('records')},ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()
    if export_meta:
        record["provenance"] = "OWNER_BROWSER_VIEWER_EXPORT — snapshot, not live Sheets API or independent verification"
        record["export"] = export_meta
    # Driver/phone/store-name/address fields are absent; approved business keys,
    # capacity and AG vehicle evidence remain only inside the protected snapshot.
    encoded = json.dumps(record, ensure_ascii=False, default=str, allow_nan=False).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    pointer = state.root/"snapshot-latest.dpapi"
    if pointer.exists():
        previous = json.loads(load_protected(pointer))
        if stamp.timestamp() < previous["retrieved_epoch"]:
            raise ValueError("Không thay snapshot mới bằng snapshot cũ.")
    save_protected(state.root/"snapshots"/(digest+".dpapi"), encoded)
    save_protected(pointer, json.dumps({"digest": digest, "retrieved_epoch": stamp.timestamp()}).encode())
    ids = safe.rows.loc[safe.rows.order_id.ne(""), "order_id"]
    states = safe.rows.loc[safe.rows.order_id.ne(""), :].groupby("order_id").status.agg(lambda values: set(values))
    return {"digest": digest, "safe_rows": len(safe.rows), "state": "INGESTED_NOT_SCHEDULED",
            "source_counters": {"unique_ghn_ids": int(ids.nunique()), "duplicate_id_rows": int(len(ids)-ids.nunique()),
                "missing_ids": int(safe.rows.order_id.eq("").sum()), "unscoped_dispatch_dates": int(safe.rows.dispatch_date.isna().sum()),
                "conflicting_status_ids": int(states.map(lambda statuses: len(statuses) > 1).sum()),
                "consistent_delivered_ids": int(states.map(lambda statuses: statuses == {"delivered"}).sum())}}


def ingest_csv_file(path, descriptor, state, owner_id):
    from urllib.parse import urlparse, parse_qs
    from .supra.booking import verify_export_headers
    allowed = {"version", "source", "source_url", "spreadsheet_id", "tab", "gid", "range", "retrieved_at", "expected_data_rows"}
    if not isinstance(descriptor, dict) or set(descriptor) != allowed or descriptor.get("source") != "GOOGLE_BROWSER_VIEWER_EXPORT":
        raise ValueError("Browser export cần descriptor đúng schema/source.")
    assert_pin(descriptor, {k: r["value"] for k, r in state.facts().items()})
    if not re.search(r"!A1:AK[1-9][0-9]*$", descriptor["range"]):
        raise ValueError("CSV toàn tab phải khóa từ A1 đến AK và giới hạn dòng rõ ràng.")
    url = urlparse(descriptor["source_url"])
    gids = parse_qs(url.query).get("gid", [])+parse_qs(url.fragment).get("gid", [])
    if url.scheme != "https" or url.hostname != "docs.google.com" or spreadsheet_id(descriptor["source_url"]) != descriptor["spreadsheet_id"] or not gids or any(g != str(descriptor["gid"]) for g in gids):
        raise ValueError("URL/tab gid của browser export không khớp nguồn owner khóa.")
    path = Path(path)
    if not path.is_absolute() or path.suffix.lower() != ".csv" or path.stat().st_size > 20*1024*1024:
        raise ValueError("Chỉ nhận đường dẫn CSV tuyệt đối tối đa 20 MB đã được owner chọn.")
    raw = path.read_bytes()
    reader = csv.reader(StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
    headers = next(reader)
    verify_export_headers(headers)  # Reject wrong-tab 57-column exports before parsing data.
    rows = []
    for row in reader:
        if len(row) > 37:
            raise ValueError("CSV có dòng vượt 37 cột.")
        rows.append(row)
        if len(rows) > 100000:
            raise ValueError("CSV vượt 100.000 dòng.")
    observed = sum(any(v != "" for v in row) for row in rows)
    if type(descriptor["expected_data_rows"]) is not int or descriptor["expected_data_rows"] != observed:
        raise ValueError("Số dòng CSV khác descriptor đã kiểm tra; chặn nhập.")
    sample = pd.DataFrame([r+[""]*(37-len(r)) for r in rows if any(r)][:10], columns=headers)
    sample["_source"] = "BROWSER_EXPORT_SAMPLE_ONLY"
    checked = parse_booking(sample)
    if checked.rows.order_id.eq("").all() or (checked.rows.dispatch_date.isna().all() and checked.rows.ltc_date.isna().all()):
        raise ValueError("Mẫu đầu CSV không có mã GHN/ngày booking nhận diện được; cần owner kiểm tra lại tab/header.")
    base = {k: descriptor[k] for k in ("version", "source", "spreadsheet_id", "tab", "gid", "range", "retrieved_at")}
    return ingest_snapshot({**base, "headers": headers, "rows": rows}, state, owner_id,
        export_meta={"sha256": hashlib.sha256(raw).hexdigest(), "header_sha256": hashlib.sha256(json.dumps(headers).encode()).hexdigest(), "observed_data_rows": observed})


def load_snapshot(state, facts):
    pointer = json.loads(load_protected(state.root/"snapshot-latest.dpapi"))
    digest = pointer["digest"]
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("Snapshot pointer sai.")
    data = load_protected(state.root/"snapshots"/(digest+".dpapi"))
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("Snapshot đổi hash; đã chặn đọc.")
    record = json.loads(data)
    assert_pin(record["source"], facts)
    from .supra.booking_quantity import QUANTITY_COLUMNS, RULE
    columns=["dispatch_date", "ltc_date", "order_id", "status", "_source"]
    if record.get('quantity_rules') is not None:
        from .supra.booking_quantity import owner_quantity_rule
        if record['quantity_rules'] != RULE or owner_quantity_rule(record['source']) != RULE:
            raise ValueError('Snapshot có quy tắc số sản phẩm sai nguồn/phiên bản.')
        columns += QUANTITY_COLUMNS
        if any(not all(k in r for k in columns) for r in record['rows']):
            raise ValueError('Snapshot sản lượng thiếu trường; chưa đọc tổng.')
    if record.get('weight_rules') is not None:
        from .supra.booking_weight import WEIGHT_RULE,owner_weight_rule
        if record['weight_rules']!=WEIGHT_RULE or owner_weight_rule(record['source'])!=WEIGHT_RULE:
            raise ValueError('Snapshot có quy tắc L sai nguồn/phiên bản.')
        if any('source_ghn_weight' not in r for r in record['rows']):
            raise ValueError('Snapshot khối lượng thiếu trường L; chưa đọc tổng.')
        columns.append('source_ghn_weight')
    handling_catalog=record.get('handling_catalog');handling_policy=record.get('handling_policy');handling_sha=record.get('handling_catalog_sha256')
    if any(v is not None for v in (handling_catalog,handling_policy,handling_sha)):
        from .supra.booking_handling import validate_catalog
        if not isinstance(handling_sha,str) or not re.fullmatch('[a-f0-9]{64}',handling_sha):raise ValueError('Thiếu hash catalog bốc xếp.')
        validate_catalog(handling_catalog,record['source'],handling_policy,handling_sha)
        for name in ('source_store_id','source_district'):
            if any(name not in row for row in record['rows']):raise ValueError('Snapshot bốc xếp thiếu H/O; chưa đọc component.')
            columns.append(name)
    frame = pd.DataFrame(record["rows"], columns=columns)
    if record.get('vehicle_pair'):
        from .telegram_vehicle_snapshot import validate_pair
        validate_pair(record['vehicle_pair'],record['source'])
        if any(not all(k in r for k in ('source_booking_plate','source_capacity_raw')) for r in record['rows']):raise ValueError('Paired snapshot thiếu AG/T.')
        for k in ('source_booking_plate','source_capacity_raw'):frame[k]=[r[k] for r in record['rows']]
    for name in ("dispatch_date", "ltc_date"):
        frame[name] = pd.to_datetime(frame[name], errors="coerce").dt.date.map(lambda v: None if pd.isna(v) else v)
    source = record["source"]
    stamp = datetime.fromisoformat(source["retrieved_at"]).timestamp()
    return BookingData(frame,record.get('quantity_rules'),record.get('weight_rules'),handling_policy,handling_catalog,handling_sha,record.get('vehicle_pair')), {"digest": digest, "created": stamp, "retrieved_at": stamp,
        "name": "GOOGLE_BROWSER_VIEWER_SNAPSHOT" if source.get("channel") == "GOOGLE_BROWSER_VIEWER_EXPORT" else "GOOGLE_CONNECTOR_SNAPSHOT",
        "tab": source["tab"], "gid": str(source["gid"]), "ranges": [source["range"]],
        **({"vehicle_raw_created":datetime.fromisoformat(record['vehicle_pair']['data_supra']['retrieved_at']).timestamp()}
           if record.get('vehicle_pair') else {})}


def ingest_minimized_snapshot(path, descriptor, state, owner_id, *, cancel=None):
    """Explicit local migration from an authorized BUSINESS-only DPAPI staging.

    Never reads settings/keys, changes facts/plan/schedule, selects a source or
    sends a report. The caller supplies the state and independently checked hash.
    """
    from .supra.booking_quantity import BUSINESS_HEADERS, RULE, owner_quantity_rule
    from .supra.booking_weight import WEIGHT_BUSINESS_HEADERS,WEIGHT_RULE,owner_weight_rule
    from .supra.booking_handling import HANDLING_SCHEMA,HANDLING_BUSINESS_HEADERS,HANDLING_POLICY,validate_catalog
    def stopped():
        if cancel is not None and cancel.is_set():
            raise ValueError('Đã hủy nhập số sản phẩm; không thay snapshot pointer.')
    stopped()
    if isinstance(descriptor,dict) and descriptor.get('schema')=='supra-vehicle-paired-source-intake-v4':
        from .telegram_vehicle_snapshot import ingest_vehicle_pair
        return ingest_vehicle_pair(path,descriptor,state,owner_id,cancel=cancel)
    path=Path(path)
    if not path.is_absolute() or path.suffix.lower()!='.dpapi' or path.stat().st_size>20*1024*1024:
        raise ValueError('Cần đường dẫn DPAPI staging tuyệt đối, tối đa 20 MB.')
    raw=load_protected(path)
    if (not isinstance(descriptor,dict) or descriptor.get('schema') not in ('supra-product-source-intake-v1','supra-quantity-weight-source-intake-v2',HANDLING_SCHEMA)
            or hashlib.sha256(raw).hexdigest()!=descriptor.get('minimized_plaintext_sha256')):
        raise ValueError('Staging không khớp descriptor/hash; chưa thay nguồn.')
    payload=json.loads(raw)
    expected={'schema','source_url','spreadsheet_id','tab','gid','source','retrieved_at','raw_sha256',
              'raw_column_count','headers','rows','confirmed_business_rules'}
    policy={k:v for k,v in RULE.items() if k!='version'}
    with_handling=descriptor['schema']==HANDLING_SCHEMA
    with_weight=descriptor['schema'] in ('supra-quantity-weight-source-intake-v2',HANDLING_SCHEMA)
    if with_weight:
        expected.add('confirmed_weight_rules')
    evidence_headers=WEIGHT_BUSINESS_HEADERS if with_weight else BUSINESS_HEADERS
    if with_handling:
        expected.update(('confirmed_handling_policy','handling_catalog','handling_catalog_sha256'))
        evidence_headers=HANDLING_BUSINESS_HEADERS
        descriptor_keys={'schema','source_url','tab','gid','retrieved_at','raw_sha256','raw_column_count','headers',
            'confirmed_business_rules','confirmed_weight_rules','confirmed_handling_policy','handling_catalog_sha256',
            'handling_catalog_version','row_count','minimized_plaintext_sha256'}
        if set(descriptor)!=descriptor_keys:raise ValueError('Descriptor bốc xếp sai schema hoặc chứa catalog ngoài DPAPI.')
    weight_policy={k:v for k,v in WEIGHT_RULE.items() if k!='version'}
    if (not isinstance(payload,dict) or set(payload)!=expected or payload['schema']!=descriptor['schema']
            or payload['source']!='AUTHORIZED_BROWSER_VIEWER_CSV_EXPORT'
            or payload['headers']!=evidence_headers or descriptor.get('headers')!=evidence_headers
            or payload['raw_column_count']!=37 or descriptor.get('raw_column_count')!=37
            or payload['confirmed_business_rules']!=policy or descriptor.get('confirmed_business_rules')!=policy
            or any(payload[k]!=descriptor.get(k) for k in ('source_url','tab','gid','retrieved_at','raw_sha256'))
            or not re.fullmatch('[a-f0-9]{64}',payload['raw_sha256'])):
        raise ValueError('Staging sai schema/mapping/quy tắc owner; chưa thay nguồn.')
    if with_weight and (payload['confirmed_weight_rules']!=weight_policy or descriptor.get('confirmed_weight_rules')!=weight_policy):
        raise ValueError('Staging sai quy tắc L theo mã GHN; chưa thay nguồn.')
    facts={k:r['value'] for k,r in state.facts().items()}
    source={k:payload[k] for k in ('spreadsheet_id','tab','gid','retrieved_at')}
    source['range']=range_map(facts.get('sheets_ranges','{}')).get('supra_booking','')
    assert_pin(source,facts)
    handling_catalog=None;handling_policy=None;handling_sha=None
    if with_handling:
        if payload['confirmed_handling_policy']!=HANDLING_POLICY or descriptor['confirmed_handling_policy']!=HANDLING_POLICY:
            raise ValueError('Staging chưa đúng policy bốc xếp đã xác nhận.')
        handling_catalog=payload['handling_catalog'];handling_policy=payload['confirmed_handling_policy'];handling_sha=payload['handling_catalog_sha256']
        if (not isinstance(handling_catalog,dict) or not isinstance(handling_sha,str) or not re.fullmatch('[a-f0-9]{64}',handling_sha) or
                handling_sha!=descriptor['handling_catalog_sha256'] or handling_catalog.get('version')!=descriptor['handling_catalog_version']):
            raise ValueError('Version/hash catalog bốc xếp không khớp descriptor.')
        validate_catalog(handling_catalog,source,handling_policy,handling_sha)
    if owner_quantity_rule(source)!=RULE:
        raise ValueError('Quy tắc J chỉ được xác nhận cho nguồn owner đã chỉ định.')
    if with_weight and owner_weight_rule(source)!=WEIGHT_RULE:
        raise ValueError('Quy tắc L chỉ được xác nhận cho nguồn owner đã chỉ định.')
    from urllib.parse import urlparse,parse_qs
    url=urlparse(payload['source_url'])
    gids=parse_qs(url.query).get('gid',[])+parse_qs(url.fragment).get('gid',[])
    if (url.scheme!='https' or url.hostname!='docs.google.com' or spreadsheet_id(payload['source_url'])!=source['spreadsheet_id']
            or not gids or any(g!='0' for g in gids) or not re.fullmatch(r"'DC PHÚ THỌ'!A1:AK[1-9][0-9]*",source['range'])):
        raise ValueError('URL/range của staging không khớp nguồn đã khóa.')
    stamp=datetime.fromisoformat(source['retrieved_at'])
    if stamp.tzinfo is None or stamp.timestamp()>time.time()+60:
        raise ValueError('retrieved_at cần timezone, không ở tương lai.')
    rows=payload['rows']; end=int(source['range'].split(':AK')[1]); positions=[]; values=[]
    if not isinstance(rows,list) or not 1<=len(rows)<=100000 or descriptor.get('row_count')!=len(rows):
        raise ValueError('Số dòng staging sai descriptor/giới hạn.')
    for row in rows:
        stopped()
        if (not isinstance(row,dict) or set(row)!={'sheet_row','values'} or type(row['sheet_row']) is not int
                or not 2<=row['sheet_row']<=end or not isinstance(row['values'],list) or len(row['values'])!=len(evidence_headers)
                or any(type(v) not in (str,int,float,type(None)) for v in row['values'])):
            raise ValueError('Dòng staging sai vị trí/giá trị business-only.')
        positions.append(row['sheet_row']); values.append(row['values'])
    if positions!=sorted(set(positions)):
        raise ValueError('sheet_row staging phải duy nhất và tăng dần.')
    frame=pd.DataFrame(values,columns=evidence_headers,dtype=object)
    frame['_source']=[f"GoogleViewer {source['spreadsheet_id']} / {source['range']} / dòng {r} / raw#{payload['raw_sha256'][:12]}" for r in positions]
    safe=parse_booking(frame,quantity_rules=dict(RULE),weight_rules=dict(WEIGHT_RULE) if with_weight else None,
        handling_policy=handling_policy,handling_catalog=handling_catalog,handling_catalog_sha256=handling_sha)
    source.update(channel='GOOGLE_BROWSER_VIEWER_EXPORT',source_url=payload['source_url'])
    record={'schema_version':4 if with_handling else 3 if with_weight else 2,'quantity_rules':dict(RULE),'weight_rules':safe.weight_rules,
        'handling_policy':handling_policy,'handling_catalog':handling_catalog,'handling_catalog_sha256':handling_sha,'source':source,'actor':owner_id,
        'provenance':'OWNER_BROWSER_VIEWER_EXPORT — product units rule owner-confirmed; not independently reconciled',
        'rows':safe.rows.to_dict('records'), 'business_evidence':{'headers':evidence_headers,'rows':rows},
        'export':{'sha256':payload['raw_sha256'],'observed_data_rows':len(rows),
                  'minimized_plaintext_sha256':descriptor['minimized_plaintext_sha256']}}
    encoded=json.dumps(record,ensure_ascii=False,default=str,allow_nan=False).encode()
    digest=hashlib.sha256(encoded).hexdigest(); pointer=state.root/'snapshot-latest.dpapi'
    if pointer.exists() and stamp.timestamp()<json.loads(load_protected(pointer))['retrieved_epoch']:
        raise ValueError('Không thay snapshot mới bằng staging cũ.')
    stopped()
    save_protected(state.root/'snapshots'/(digest+'.dpapi'),encoded)
    stopped()
    save_protected(pointer,json.dumps({'digest':digest,'retrieved_epoch':stamp.timestamp()}).encode())
    return {'state':'INGESTED_NOT_SCHEDULED','digest':digest,'safe_rows':len(rows),
            'quantity_rule_version':RULE['version'],'weight_rule_version':WEIGHT_RULE['version'] if with_weight else None,
            'handling_catalog_sha256':handling_sha,
            'raw_sha256':payload['raw_sha256']}


def send_scheduled(state, config, now=None):
    from .telegram_state import GROUP_ID
    from .telegram_assistant import AssistantResponder
    from .telegram_bridge import TelegramAPI, TelegramBridge
    owner = config.get("owner_id")
    if config.get("chat_id") != GROUP_ID or state.control("paired") != {"chat_id": GROUP_ID, "owner_id": owner}:
        return {"state": "NOT_PAIRED"}
    if not state.control("worker_enabled", False):
        return {"state": "WORKER_STOPPED"}
    if not state.control("schedule_enabled", False):
        return {"state": "SCHEDULE_PAUSED"}
    facts = {k: row["value"] for k, row in state.facts().items()}
    if facts.get("source_id") != "browser_snapshot":
        return {"state": "BROWSER_SOURCE_NOT_SELECTED"}
    responder = AssistantResponder(state, config)
    api = TelegramAPI(config["bot_token"])
    bridge = TelegramBridge(api, responder, owner_id=owner, state=state, lifetime=None)
    responder.cancel = bridge._stop
    try:
        # Sending-only adapter: no start(), getUpdates, lease or second poller.
        return responder.tick(bridge._send, now=now, external_snapshot=True)
    except Exception:
        return {"state": "DELIVERY_UNCONFIRMED", "reason": "Inspect durable receipts and Telegram before requesting another send; no automatic resend."}
    finally:
        responder.clear()
        api.close()
        api._token = ""


def main():
    import argparse
    from .telegram_state import BusinessState
    from .telegram_secrets import load_settings
    state = None
    try:
        parser = argparse.ArgumentParser(description="DPAPI source snapshot import; descriptor/payload only through stdin")
        inputs=parser.add_mutually_exclusive_group()
        inputs.add_argument("--file", help="Owner-selected absolute CSV path; descriptor stays on stdin")
        inputs.add_argument('--minimized', help='Owner-selected BUSINESS-only DPAPI staging; descriptor on stdin; no source/schedule change')
        parser.add_argument("--send-scheduled", action="store_true", help="After import, send only the paired and approved browser-source daily job")
        args = parser.parse_args()
        raw = sys.stdin.buffer.read(20*1024*1024+1)
        if len(raw) > 20*1024*1024:
            raise ValueError("Snapshot quá lớn.")
        config = load_settings()
        if not config.get("owner_id"):
            raise ValueError("Chưa cấu hình owner.")
        state = BusinessState()
        result = (ingest_minimized_snapshot(args.minimized,json.loads(raw),state,config['owner_id']) if args.minimized else
                  ingest_csv_file(args.file, json.loads(raw), state, config["owner_id"]) if args.file else
                  ingest_snapshot(json.loads(raw), state, config["owner_id"]))
        if args.send_scheduled:
            result["delivery"] = send_scheduled(state, config)
            result["state"] = "INGESTED_AND_SENT" if result["delivery"].get("state") == "SENT" else "INGESTED_WITHOUT_CONFIRMED_SEND"
        print(json.dumps(result))  # Only hash/count/state; never input/identity/token.
        return 0
    except Exception:
        print('{"state":"REJECTED","reason":"Check schema, owner source pin, credential context and snapshot time; no source replaced."}')
        return 1
    finally:
        if state:
            state.close()


if __name__ == "__main__":
    sys.exit(main())
