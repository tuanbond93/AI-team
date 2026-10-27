"""Official Sheets read-only API with owner-supplied service-account credentials."""
import hashlib
import json
import re
import time
import httpx
import pandas as pd
from .schema import SPECS
from .ingestion import map_columns, validate

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def validate_account(info):
    if (not isinstance(info, dict) or info.get("type") != "service_account" or
            info.get("token_uri") != "https://oauth2.googleapis.com/token" or
            not re.fullmatch(r"[^\s@]+@[^\s@]+\.iam\.gserviceaccount\.com", str(info.get("client_email", ""))) or
            not str(info.get("private_key", "")).startswith("-----BEGIN PRIVATE KEY-----")):
        raise ValueError("Cần JSON service account Google chính thức, token_uri oauth2.googleapis.com. Không nhận cookie hoặc credential lấy từ trình duyệt.")
    return info


def spreadsheet_id(value):
    match = re.fullmatch(r"https://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]{15,100})(?:/[^\s]*)?", value.strip())
    result = match[1] if match else value.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{15,100}", result):
        raise ValueError("Google Sheets ID hoặc link chưa đúng định dạng.")
    return result


def range_map(value):
    result = json.loads(value) if isinstance(value, str) else value
    if not isinstance(result, dict) or not 1 <= len(result) <= len(SPECS):
        raise ValueError("sheets_ranges cần JSON tên_bảng → tab!A1:Z10000.")
    for kind, area in result.items():
        if kind not in (*SPECS, "supra_booking") or not isinstance(area, str) or len(area) > 180:
            raise ValueError("Range chỉ ánh xạ bảng canonical đã hỗ trợ.")
        if "supra_booking" in result and len(result) != 1:
            raise ValueError("Booking phải là nguồn riêng, không trộn với canonical P&L.")
        match = re.fullmatch(r"([^!\r\n]+)!([A-Z]{1,2})([1-9][0-9]{0,5}):([A-Z]{1,2})([1-9][0-9]{0,5})", area)
        if not match or not 0 <= int(match[5])-int(match[3]) <= 100000:
            raise ValueError("Range phải có tab và giới hạn dòng rõ ràng, tối đa 100.000 dòng dữ liệu.")
        def column(text):
            total = 0
            for char in text:
                total = total*26+ord(char)-64
            return total
        if not 0 <= column(match[4])-column(match[2]) < 100:
            raise ValueError("Range tối đa 100 cột, theo thứ tự tăng.")
    return result


def access_token(info):
    validate_account(info)
    try:
        import requests
        from google.oauth2.service_account import Credentials
        from google.auth.transport.requests import Request
        with requests.Session() as session:
            session.trust_env = False
            request = Request(session=session)
            def bounded_request(url, method="GET", body=None, headers=None, **kwargs):
                if url != "https://oauth2.googleapis.com/token":
                    raise ValueError("Đích xác thực Google chưa cho phép.")
                kwargs.pop("timeout", None)
                return request(url, method=method, body=body, headers=headers, timeout=15, **kwargs)
            credential = Credentials.from_service_account_info(info, scopes=SCOPES)
            credential.refresh(bounded_request)
            return credential.token
    except Exception:
        raise ValueError("Chưa xác thực được service account. Kiểm tra credential và Google Sheets API; không dùng phiên/cookie trình duyệt.") from None


def read_sheets(facts, info, *, client=None, token_provider=access_token,state=None):
    identifier = spreadsheet_id(facts.get("sheets_id", ""))
    areas = range_map(facts.get("sheets_ranges", "{}"))
    title, gid = facts.get("sheets_tab", ""), facts.get("sheets_gid", "")
    if not title or not re.fullmatch(r"[0-9]{1,12}", gid):
        raise ValueError("Cần owner xác nhận sheets_tab và sheets_gid trước khi đọc dữ liệu.")
    for area in areas.values():
        range_title = area.split("!")[0]
        if range_title.startswith("'") and range_title.endswith("'"):
            range_title = range_title[1:-1].replace("''", "'")
        if range_title != title:
            raise ValueError("Range không thuộc tab đã khóa; không đọc tab khác.")
    validate_account(info)
    token = token_provider(info)
    connection = client or httpx.Client(trust_env=False, follow_redirects=False)
    try:
        response = connection.get(f"https://sheets.googleapis.com/v4/spreadsheets/{identifier}",
            params={"fields": "sheets.properties(sheetId,title)", "ranges": "'"+title.replace("'", "''")+"'"},
            headers={"Authorization": "Bearer "+token}, timeout=20)
        if response.status_code != 200:
            raise ValueError("Chưa xác minh được tab Sheets. Kiểm tra quyền Viewer và Sheets API; không đọc dữ liệu.")
        metadata = response.json()
        matching = [s.get("properties", {}) for s in metadata.get("sheets", []) if s.get("properties", {}).get("title") == title]
        if len(matching) != 1 or str(matching[0].get("sheetId")) != gid:
            raise ValueError("Tên tab/gid đã thay đổi hoặc không khớp owner xác nhận; đã chặn đọc dữ liệu.")
        params = [("ranges", area) for area in areas.values()]
        params += [("majorDimension", "ROWS"), ("valueRenderOption", "UNFORMATTED_VALUE"), ("dateTimeRenderOption", "SERIAL_NUMBER")]
        with connection.stream("GET", f"https://sheets.googleapis.com/v4/spreadsheets/{identifier}/values:batchGet",
                               params=params, headers={"Authorization": "Bearer "+token}, timeout=20) as response:
            if response.status_code != 200:
                raise ValueError("Chưa đọc được Sheets. Kiểm tra quyền Viewer của service account, ID/tab/range, API/quota. Không tự công khai sheet hoặc đổi nguồn.")
            parts, size = [], 0
            for part in response.iter_bytes():
                size += len(part)
                if size > 20*1024*1024:
                    raise ValueError("Sheets vượt 20 MB; thu hẹp range.")
                parts.append(part)
        raw = b"".join(parts)
        payload = json.loads(raw)
        ranges = payload["valueRanges"]
        if payload.get("spreadsheetId") != identifier or len(ranges) != len(areas):
            raise ValueError("Sheets trả phạm vi khác yêu cầu.")
        mapping = json.loads(facts.get("mapping", "{}"))
        tables = {}
        for (kind, area), returned in zip(areas.items(), ranges):
            rows = returned.get("values", [])
            actual_range = returned.get("range", "")
            actual_title = actual_range.split("!")[0]
            if actual_title.startswith("'") and actual_title.endswith("'"):
                actual_title = actual_title[1:-1].replace("''", "'")
            if actual_title != title:
                raise ValueError("Phản hồi Sheets không thuộc tab đã khóa.")
            if not rows:
                raise ValueError("Range không có dòng tiêu đề/dữ liệu.")
            headers = [str(c).strip() for c in rows[0]]
            if not all(headers) or len(headers) != len(set(headers)):
                raise ValueError("Tiêu đề Sheets rỗng/trùng.")
            start = int(re.search(r"![A-Z]+([0-9]+):", area)[1])
            end = int(re.search(r":[A-Z]+([0-9]+)$", area)[1])
            if len(rows) > end-start+1:
                raise ValueError("Sheets trả số dòng vượt range đã cho phép.")
            clean, positions = [], []
            for offset, row in enumerate(rows[1:], start=1):
                if len(row) > len(headers):
                    raise ValueError("Sheets có giá trị ngoài cột tiêu đề.")
                if not any(v != "" for v in row):
                    continue
                clean.append([*row, *([""]*(len(headers)-len(row)))])
                positions.append(start+offset)
            frame = pd.DataFrame(clean, columns=headers,dtype=object if kind=='supra_booking' else None)
            frame["_source"] = [f"GoogleSheets {identifier} / {area} / dòng {r}" for r in positions]
            if kind == "supra_booking":
                from .supra.booking import parse_booking
                from .supra.booking_quantity import export_quantity_rule
                from .supra.booking_weight import export_weight_rule
                from .telegram_snapshot import active_handling_config
                source_pin={'spreadsheet_id':identifier,'tab':title,'gid':gid,'range':area}
                policy,catalog,digest=active_handling_config(state,source_pin) if state is not None else (None,None,None)
                if policy is not None:
                    from .supra.booking import verify_export_headers
                    verify_export_headers(headers)
                tables[kind] = parse_booking(frame, quantity_rules=export_quantity_rule(headers,
                    {'spreadsheet_id':identifier,'tab':title,'gid':gid}),weight_rules=export_weight_rule(headers,
                    {'spreadsheet_id':identifier,'tab':title,'gid':gid}),
                    handling_policy=policy,handling_catalog=catalog,handling_catalog_sha256=digest)
                continue
            for col in SPECS[kind].dates:
                source_col = mapping.get(col, col)
                if source_col in frame:
                    frame[source_col] = frame[source_col].map(lambda v: (pd.Timestamp("1899-12-30")+pd.Timedelta(days=float(v))).date().isoformat()
                        if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v < 100000 else v)
            tables[kind] = map_columns(frame, kind, {c: mapping.get(c, c) for c in SPECS[kind].columns})
        checked = tables["supra_booking"] if "supra_booking" in tables else validate(tables, facts.get("numeric_format", "machine"))
        return checked, {"digest": hashlib.sha256(raw).hexdigest(), "name": "GOOGLE_SHEETS_READONLY",
                         "created": time.time(), "retrieved_at": time.time(), "ranges": list(areas.values()), "tab": title, "gid": gid}
    except ValueError:
        raise
    except Exception:
        raise ValueError("Chưa đọc được dữ liệu Sheets hợp lệ. Không dùng cache hoặc nguồn khác thay thế.") from None
    finally:
        if client is None:
            connection.close()
