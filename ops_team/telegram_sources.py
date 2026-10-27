"""Owner-selected encrypted files and explicit read-only local export connector."""
from io import BytesIO
import hashlib
from pathlib import Path, PureWindowsPath
import zipfile
from .ingestion import read_file, map_columns, validate
from .schema import SPECS
from .telegram_secrets import save_protected, load_protected


def read_tables(data, name, kind="", mapping=None):
    if len(data) > 20*1024*1024 or PureWindowsPath(name).suffix.lower() not in (".xlsx", ".csv"):
        raise ValueError("Chỉ nhận Excel .xlsx hoặc CSV UTF-8, tối đa 20 MB.")
    if name.lower().endswith(".xlsx"):
        with zipfile.ZipFile(BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 1000 or sum(e.file_size for e in entries) > 64*1024*1024:
                raise ValueError("Workbook vượt giới hạn giải nén an toàn 64 MB/1.000 mục.")
            if any("vbaproject" in e.filename.casefold() for e in entries):
                raise ValueError("Không nhận workbook chứa macro.")
    sheets = read_file(data, name)
    tables = {}
    for sheet, frame in sheets.items():
        target = kind if len(sheets) == 1 and kind in SPECS else sheet
        if target not in SPECS:
            raise ValueError("Tên sheet chưa ánh xạ. Dùng sheet costs/budgets/operations/workforce/fleet/kpi, hoặc /import costs cho CSV một bảng.")
        if target in tables:
            raise ValueError("Hai sheet cùng bảng; cần gộp và đối soát trước.")
        tables[target] = map_columns(frame, target, {c: (mapping or {}).get(c, c) for c in SPECS[target].columns})
    return tables


def import_file(state, request, name, data, kind="", mapping=None, numeric_format="machine"):
    clean_name = PureWindowsPath(name).name.replace("\n", " ").replace("\r", " ")[:120]
    if kind == "chief":
        from .chief.ingestion import read_financial
        checked = read_financial(data, clean_name)
        digest = hashlib.sha256(data).hexdigest()
        save_protected(state.root/"sources"/(digest+".dpapi"), data)
        return state.add_source(clean_name, kind, digest, len(data), request), checked
    tables = read_tables(data, clean_name, kind, mapping)
    checked = validate(tables, numeric_format)
    errors = [i for i in checked.issues if i["code"] != "MISSING_TABLE"]
    if errors:
        raise ValueError(f"File có {len(errors)} lỗi dữ liệu/đơn vị/trùng/ánh xạ. Chưa thay nguồn hiện tại. Xem hợp đồng Excel mẫu trong AI Team.")
    digest = hashlib.sha256(data).hexdigest()
    save_protected(state.root/"sources"/(digest+".dpapi"), data)
    identifier = state.add_source(clean_name, kind, digest, len(data), request)
    return identifier, checked


def load_source(state, identifier, facts):
    record = state.source(identifier)
    if record is None:
        raise ValueError("Mã nguồn chưa tồn tại. Gửi file /import trước.")
    data = load_protected(state.root/"sources"/(record["digest"]+".dpapi"))
    if hashlib.sha256(data).hexdigest() != record["digest"]:
        raise ValueError("Nguồn đổi hash; đã chặn đọc.")
    if record["kind"] == "chief":
        from .chief.ingestion import read_financial
        return read_financial(data, record["name"]), record
    import json
    tables = read_tables(data, record["name"], record["kind"], json.loads(facts.get("mapping", "{}")) or None)
    return validate(tables, facts.get("numeric_format", "machine")), record


def read_export_folder(folder, facts):
    """Never recursive. Resolve each candidate to prevent junction/symlink escapes."""
    root = Path(folder).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Thư mục export chưa tồn tại.")
    candidates = []
    for item in root.iterdir():
        if item.suffix.lower() not in (".xlsx", ".csv") or not item.is_file():
            continue
        resolved = item.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise ValueError("File trỏ ra ngoài thư mục được owner cho phép.")
        candidates.append(resolved)
        if len(candidates) > 100:
            raise ValueError("Thư mục vượt 100 file; dùng thư mục export riêng.")
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    books = [p for p in candidates if p.suffix.lower() == ".xlsx"]
    selected = books[:1] if books else []
    if not books:
        for kind in SPECS:
            matches = [p for p in candidates if p.suffix.lower() == ".csv" and (p.stem == kind or p.stem.startswith(kind+"_"))]
            selected += matches[:1]
    if not selected:
        raise ValueError("Chưa có workbook canonical hoặc CSV costs_*/operations_* trong thư mục được phép.")
    tables, hashes, newest = {}, [], 0
    import json
    for item in selected:
        if item.stat().st_size > 20*1024*1024:
            raise ValueError("File export vượt 20 MB.")
        data = item.read_bytes()
        kind = next((k for k in SPECS if item.stem == k or item.stem.startswith(k+"_")), "")
        loaded = read_tables(data, item.name, kind, json.loads(facts.get("mapping", "{}")) or None)
        if set(tables) & set(loaded):
            raise ValueError("Nguồn export trùng bảng.")
        tables.update(loaded)
        hashes.append(hashlib.sha256(data).hexdigest())
        newest = max(newest, item.stat().st_mtime)
    digest = hashlib.sha256("".join(hashes).encode()).hexdigest()
    return validate(tables, facts.get("numeric_format", "machine")), {"digest": digest, "name": "EXPORT_FOLDER", "created": newest}
