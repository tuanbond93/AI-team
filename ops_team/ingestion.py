from dataclasses import dataclass
from io import BytesIO, StringIO
import csv
import re
from pathlib import PureWindowsPath
import hashlib
import zipfile
import pandas as pd
from .schema import SPECS


@dataclass
class Validation:
    tables: dict
    issues: list
    rejected: dict
    raw_counts: dict


def read_file(data: bytes, filename: str, header_row=1, csv_separator="auto") -> dict[str, pd.DataFrame]:
    """Read only; no uploaded files or untrusted formulas are written or evaluated."""
    if len(data) > 20 * 1024 * 1024:
        raise ValueError("File vượt 20 MB.")
    suffix = PureWindowsPath(filename).suffix.lower()
    if not 1 <= header_row <= 50:
        raise ValueError("Dòng tiêu đề phải từ 1 đến 50.")
    physical_rows = {}
    if suffix == ".csv":
        try:
            decoded = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ValueError("CSV phải dùng UTF-8. Hãy xuất lại từ Google Sheets.") from None
        if csv_separator == "auto":
            try:
                delimiter = csv.Sniffer().sniff(decoded[:20000], delimiters=",;\t|").delimiter
            except csv.Error:
                delimiter = ","
        elif csv_separator in (",", ";", "\t", "|"):
            delimiter = csv_separator
        else:
            raise ValueError("Dấu phân cột CSV không hợp lệ.")
        reader = csv.reader(StringIO(decoded, newline=""), delimiter=delimiter, strict=True)
        rows, refs, headers = [], [], None
        try:
            while True:
                start = reader.line_num+1
                row = next(reader)
                end = reader.line_num
                if start < header_row:
                    continue
                if headers is None:
                    if start != header_row:
                        raise ValueError("Dòng tiêu đề nằm giữa trường nhiều dòng; hãy chọn dòng bắt đầu của tiêu đề.")
                    headers = row
                    continue
                if not row or (len(row) == 1 and not row[0].strip()):
                    continue
                if len(row) != len(headers):
                    raise ValueError(f"CSV dòng {start}–{end}: số cột khác tiêu đề; kiểm tra dấu phân cột/dấu ngoặc kép.")
                rows.append(row)
                refs.append(str(start) if start == end else f"{start}–{end}")
        except StopIteration:
            pass
        except csv.Error:
            raise ValueError("CSV có dấu ngoặc kép hoặc trường nhiều dòng không hợp lệ.") from None
        if headers is None:
            raise ValueError("File không có dòng tiêu đề đã chọn.")
        df = pd.DataFrame(rows, columns=headers, dtype=object)
        sheets = {"CSV": df}
        physical_rows["CSV"] = refs
    elif suffix == ".xlsx":
        with zipfile.ZipFile(BytesIO(data)) as archive:
            if sum(i.file_size for i in archive.infolist()) > 100 * 1024 * 1024:
                raise ValueError("Workbook giải nén vượt 100 MB.")
        sheets = pd.read_excel(BytesIO(data), sheet_name=None, engine="openpyxl", dtype=object, header=header_row-1)
    else:
        raise ValueError("Chỉ nhận .xlsx hoặc .csv.")
    if sum(len(df) for df in sheets.values()) > 100_000:
        raise ValueError("MVP giới hạn 100.000 dòng mỗi file.")
    clean_name = PureWindowsPath(filename).name.replace("\n", " ").replace("\r", " ")
    digest = hashlib.sha256(data).hexdigest()[:10]
    for sheet, df in sheets.items():
        df.columns = [str(c).strip() for c in df.columns]
        if len(df.columns) != len(set(df.columns)) or any(not c for c in df.columns):
            raise ValueError(f"Sheet {sheet}: tên cột rỗng hoặc trùng; sửa dòng tiêu đề rồi nhập lại.")
        # Override all source metadata supplied by the input file.
        positions = physical_rows.get(sheet, [str(i+header_row+1) for i in range(len(df))])
        df["_source"] = [f"{clean_name} [{digest}] / {sheet} / dòng {position}" for position in positions]
    return sheets


def map_columns(df, kind, mapping, constants=None):
    constants = constants or {}
    columns = SPECS[kind].columns
    if any(c not in SPECS[kind].units and c != "document_ref" for c in constants):
        raise ValueError("Chỉ cho phép hằng số đơn vị và tham chiếu chứng từ trống đã xác nhận.")
    if any(c in SPECS[kind].units and value != SPECS[kind].units[c] for c, value in constants.items()):
        raise ValueError("Hằng số đơn vị phải đúng hợp đồng; không tự quy đổi.")
    chosen = [mapping.get(c) for c in columns if mapping.get(c) and c not in constants]
    if len(chosen) != len(set(chosen)):
        raise ValueError("Một cột nguồn không thể ánh xạ cho nhiều trường.")
    missing = [c for c in columns if c not in constants and (not mapping.get(c) or mapping[c] not in df)]
    if missing:
        raise ValueError("Chưa ánh xạ: " + ", ".join(missing))
    out = pd.DataFrame(index=df.index)
    for c in columns:
        out[c] = constants[c] if c in constants else df[mapping[c]].values
    out["_source"] = df["_source"].values
    return out


def validate(tables, numeric_format="machine"):
    if numeric_format not in ("machine", "vi", "en"):
        raise ValueError("Chọn định dạng số machine, vi hoặc en.")
    valid, issues, rejected, counts = {}, [], {}, {}
    for kind, spec in SPECS.items():
        df = tables.get(kind, pd.DataFrame()).copy(deep=True).reset_index(drop=True)
        counts[kind] = len(df)
        rejected[kind] = []
        if df.empty:
            valid[kind] = pd.DataFrame(columns=[*spec.columns, "_source"])
            issues.append({"table": kind, "code": "MISSING_TABLE", "source": "", "detail": "Chưa có dữ liệu: " + spec.label})
            continue
        if "_source" not in df:
            df["_source"] = [f"RAM / {kind} / bản ghi {i+1}" for i in range(len(df))]
        missing = set(spec.columns) - set(df.columns)
        if missing:
            issues.append({"table": kind, "code": "MISSING_COLUMN", "source": "; ".join(df._source), "detail": ", ".join(sorted(missing))})
            rejected[kind] = df._source.tolist()
            valid[kind] = pd.DataFrame(columns=[*spec.columns, "_source"])
            continue
        bad = pd.Series(False, index=df.index)

        def flag(mask, code, detail, exclude=True, column=""):
            nonlocal bad
            mask = mask.fillna(True)
            for idx in df.index[mask]:
                suggestions = {"INVALID_NUMBER": "Điền số nguồn hoặc chọn đúng định dạng số; không điền 0 thay thiếu.",
                    "DUPLICATE_KEY": "Đối soát nhóm trùng với file gốc rồi xuất lại.", "WRONG_UNIT": "Sửa đơn vị nguồn hoặc chọn hằng số đơn vị đã xác nhận.",
                    "PERIOD_MISMATCH": "Xác minh ngày phát sinh và kỳ hạch toán; không tự chuyển kỳ.",
                    "OVER_CAPACITY": "Kiểm tra quá tải và đơn vị/sức chứa; dữ liệu vẫn được giữ để cảnh báo."}
                issues.append({"table": kind, "code": code, "source": str(df.at[idx, "_source"]), "column": column,
                    "detail": detail, "action": suggestions.get(code, "Kiểm tra bản ghi nguồn, sửa tại bản sao và nhập lại."),
                    "severity": "Cách ly" if exclude else "Cảnh báo"})
            if exclude:
                bad |= mask

        for c in set((*spec.key, *spec.text, *spec.units)) - set(spec.dates):
            df[c] = df[c].astype("string").str.strip()
            # Missing invoice reference is an accounting exception; preserve its actual cost.
            flag(df[c].isna() | df[c].eq(""), "MISSING_VALUE", f"Thiếu {c}", c != "document_ref", column=c)
        for c in spec.numeric:
            # Numeric Excel cells remain numeric. Text numbers use one explicit
            # locale, with strict grouping; never strip punctuation speculatively.
            def parse_number(value):
                if not isinstance(value, str):
                    return value
                value = value.strip()
                patterns = {"machine": r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?",
                    "vi": r"[+-]?(?:\d+|\d{1,3}(?:\.\d{3})+)(?:,\d+)?",
                    "en": r"[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?"}
                if not re.fullmatch(patterns[numeric_format], value):
                    return None
                return value.replace(".", "").replace(",", ".") if numeric_format == "vi" else value.replace(",", "") if numeric_format == "en" else value
            df[c] = pd.to_numeric(df[c].map(parse_number), errors="coerce")
            flag(df[c].isna() | ~df[c].between(0, 1e15), "INVALID_NUMBER", f"{c} thiếu, âm hoặc không hữu hạn", column=c)
            if c in ("volume", "on_time", "backlog", "processed_volume", "defects"):
                flag(df[c].notna() & df[c].mod(1).ne(0), "FRACTIONAL_COUNT", f"{c} phải nguyên", column=c)
        for c, unit in spec.units.items():
            flag(df[c].ne(unit), "WRONG_UNIT", f"{c} phải là {unit}; không tự quy đổi", column=c)
        flag(~df.period.str.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", na=False), "INVALID_PERIOD", "Kỳ phải là YYYY-MM")
        for c in spec.dates:
            # ISO dates only. Ambiguous locale dates are never guessed.
            raw = df[c].astype("string").str.slice(0, 10)
            parsed = pd.to_datetime(raw, format="%Y-%m-%d", errors="coerce")
            flag(parsed.isna(), "INVALID_DATE", f"{c} phải là ngày ISO YYYY-MM-DD", column=c)
            flag(parsed.notna() & parsed.dt.strftime("%Y-%m").ne(df.period), "PERIOD_MISMATCH", f"{c} lệch kỳ hạch toán; cần đối soát", column=c)
            df[c] = parsed.dt.strftime("%Y-%m-%d")
        flag(df.duplicated(list(spec.key), keep=False), "DUPLICATE_KEY", "Trùng khóa: cách ly toàn bộ nhóm, chờ đối soát")
        if kind == "costs":
            # A voucher may have several categories, but the same voucher/category may not repeat.
            has_doc = df.document_ref.notna() & df.document_ref.ne("")
            flag(has_doc & df.duplicated(["warehouse", "document_ref", "category"], keep=False), "DUPLICATE_DOCUMENT", "Trùng chứng từ + kho + loại chi phí")
            flag(~df.category.isin(["labor", "fleet", "rent", "other"]), "INVALID_CATEGORY", "Loại chi phí: labor/fleet/rent/other")
        if kind == "budgets":
            flag(~df.category.isin(["labor", "fleet", "rent", "other"]), "INVALID_CATEGORY", "Loại chi phí không hợp lệ")
        for numerator, denominator in {"operations": [("on_time", "volume")], "workforce": [("defects", "processed_volume")],
                "fleet": [("empty_km", "km"), ("loaded_kg", "capacity_kg"), ("loaded_m3", "capacity_m3")]}.get(kind, []):
            overloaded = kind == "fleet" and numerator in ("loaded_kg", "loaded_m3")
            flag(df[numerator] > df[denominator], "OVER_CAPACITY" if overloaded else "INCONSISTENT",
                 f"{numerator} lớn hơn {denominator}", not overloaded, column=numerator)
        if kind == "kpi":
            flag(~df.metric.isin(["sla", "ot_rate", "empty_rate", "load_weight", "load_volume"]), "UNKNOWN_KPI", "Mã KPI chưa hỗ trợ")
            flag(~df.direction.isin(["min", "max"]), "INVALID_DIRECTION", "direction phải min hoặc max")
            flag(df.target > 100, "INVALID_TARGET", "Mục tiêu % vượt 100")
        rejected[kind] = df.loc[bad, "_source"].tolist()
        valid[kind] = df.loc[~bad].copy()
    return Validation(valid, issues, rejected, counts)
