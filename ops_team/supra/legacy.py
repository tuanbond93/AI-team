"""Read-only connector to the user's existing Supra historical publication.

Reuse its validator, not its publishers/network clients. No source repo writes,
imports, credentials or guessed redistribution of cross-province vehicle costs.
"""
from pathlib import Path
from decimal import Decimal
import ast
import hashlib
import json
import unicodedata
from datetime import date
from .domain import identity

REVIEWED_ENGINE_SHA256 = "57e9576f9b7503f1ff6fd9ee97c9a13d2691b20e6da4866d7b61713b2d29e591"


def is_phu_tho(value):
    text = "".join(c for c in unicodedata.normalize("NFD", str(value or "").lower()) if not unicodedata.combining(c))
    return " ".join(text.split()) in ("phu tho", "t. phu tho", "tinh phu tho")


def source_validator(engine_path):
    content = Path(engine_path).read_bytes()
    if hashlib.sha256(content).hexdigest() != REVIEWED_ENGINE_SHA256:
        raise ValueError("Engine nguồn đã đổi version; cần review validator trước khi nhận snapshot mới.")
    tree = ast.parse(content.decode("utf-8-sig"))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "validate_result")
    # Compile only the reviewed pure validation function. Never run module
    # imports, rate-master loading, entrypoints or publication/network scripts.
    namespace = {"__builtins__": {"sum": sum, "ValueError": ValueError}}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(engine_path), "exec"), namespace)
    return namespace["validate_result"]


def read_legacy_month(principal, source_dir, month="2026-10"):
    principal.require("VIEW_PNL")
    root = Path(source_dir)
    path, engine = root/"src/data/multiMonthSupraData.json", root/"supra_engine.py"
    content = path.read_bytes()
    if len(content) > 20000000:
        raise ValueError("Snapshot lịch sử vượt 20 MB.")
    validate = source_validator(engine)
    store = json.loads(content.decode("utf-8-sig"), parse_float=Decimal)
    payload = store.get("months", {}).get(month)
    if not payload or payload.get("snapshot", {}).get("schemaVersion") != 2:
        raise ValueError("Kỳ nguồn chưa có schema/version đối soát hỗ trợ; không dùng số cũ làm ACTUAL.")
    if payload["snapshot"].get("month") != month:
        raise ValueError("Tháng key và tháng snapshot không khớp.")
    try:
        validate(payload)
    except (ValueError, KeyError, TypeError, ArithmeticError):
        raise ValueError("Snapshot không qua validator của engine nguồn; cần pipeline nguồn tính/đối soát lại, không tự sửa store.") from None
    areas = [row for row in payload["areas"] if is_phu_tho(row.get("province"))]
    if not areas:
        raise ValueError("Snapshot không có dòng Phú Thọ.")
    file_hash = hashlib.sha256(content).hexdigest()
    source = f"multiMonthSupraData.json#{file_hash[:12]}::/months/{month}"
    lineage = []
    for i, row in enumerate(payload.get("lineage", [])):
        if not is_phu_tho(row.get("province")):
            continue
        if not date.fromisoformat(row["date"]).isoformat().startswith(month+"-"):
            raise ValueError("Lineage không cùng kỳ snapshot.")
        lineage.append({**{key: row.get(key) for key in ("orderCode", "date", "plate", "district", "pcs", "kg", "revenue", "basis", "sourceRow")},
            "province": "Phú Thọ", "source": source+f"/lineage/{i}", "verification": "UNVERIFIED"})
    def total(field):
        values = [Decimal(str(r[field])) for r in areas]
        if any(not v.is_finite() or abs(v) > Decimal("1e18") or (field != "profit" and v < 0) for v in values):
            raise ValueError("Số tiền/khối lượng nguồn không hợp lệ.")
        return sum(values, Decimal(0))
    summary = {"pcs": total("pcs"), "kg": total("weight"), "revenue": total("revenue"),
        "cost": total("allocatedCost"), "profit": total("profit")}
    if summary["revenue"]-summary["cost"] != summary["profit"]:
        raise ValueError("P&L Phú Thọ của snapshot không bảo toàn.")
    if any(not row["orderCode"] for row in lineage) or len({r["orderCode"] for r in lineage}) != len(lineage):
        raise ValueError("Lineage trùng/thiếu mã đơn; không cộng doanh thu nhiều lần.")
    if sum(Decimal(str(r["revenue"])) for r in lineage) != summary["revenue"] or sum(Decimal(str(r["pcs"])) for r in lineage) != summary["pcs"]:
        raise ValueError("Lineage Phú Thọ không khớp dòng tỉnh; không nhận tổng không truy vết được.")
    metadata = payload["snapshot"]
    return {"version": identity([file_hash, REVIEWED_ENGINE_SHA256, month, "PHU_THO-only-v1"]),
        "source": source, "source_sha256": file_hash, "source_engine_sha256": REVIEWED_ENGINE_SHA256,
        "month": month, "customer": "SUPRA", "region": "PHU_THO", "customer_assignment": "UNCONFIRMED_B2B_SOURCE_WITHOUT_CUSTOMER_ID",
        "mode": "HISTORICAL_SOURCE_MODEL", "verification": "UNVERIFIED", "validator_reused": "supra_engine.validate_result",
        "source_updated_at": metadata.get("sourceUpdatedAt"), "source_complete": metadata.get("sourceComplete") is True,
        "source_weight_unit": metadata.get("weightUnit"), "output_kg_unit": "kg (already normalized upstream; no second gram conversion)",
        "rate_version": metadata.get("rateVersion"), "summary": summary, "lineage": lineage,
        "observed_days": len({r["date"] for r in lineage}), "verified_savings_vnd": None,
        "limitations": ["Nguồn B2B chưa có customer ID độc lập; cần xác minh tập đơn thực sự thuộc Supra.",
            "Chi phí từ engine lịch sử là mô hình, chưa có hóa đơn/NVGH/nhật trình Finance đối soát.",
            "Chỉ lấy Phú Thọ; không xuất tổng, xe dùng chung hoặc bản ghi tỉnh khác. Không phân bổ lại cước xe đa tỉnh bằng tỷ lệ tự đoán.",
            "Không cộng vào PLAN, sổ ACTUAL route/day hoặc pilot 26/10–08/11. Kỳ cũ không qua validator phải được pipeline nguồn tính lại."]}
