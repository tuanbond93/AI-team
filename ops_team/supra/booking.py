"""Booking evidence only. Never infer trip/drop/PCS/cost/SLA from merged totals."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import re
import unicodedata
from zoneinfo import ZoneInfo
import pandas as pd
from ..metrics import Metric

BOOKING_HEADERS = ["Ngày LTC", "Mã đơn GHN", "Trạng thái", "STT", "Ngày xuất", "MÃ SO", "MÃ DO", "Mã siêu thị", "Tên siêu thị",
    "Qty (Units)", "Số kiện/Tổng PCS/Mã chuyến", "Weight (kg)/ tổng cửa hàng", "Volume up (m3)", "Tỉnh", "Huyện", "Mã giờ",
    "Giờ nhận hàng", "Số điểm giao", "Tổng số kiện xe", "Tải trọng", "Ghi chú", "Chẵn", "Lẻ", "Gợi ý lot sau", "Ghi chú 2",
    "Đơn vị vận tải", "Count", "Weight (từng SO)", "Volume", "PCS/DROP/Tỉnh", "KG/Lot", "CBM/Lot", "BIỂN SỐ XE",
    "Tên Tài Xế", "SĐT", "Số ký mỗi xe", "ISSUE"]


def verify_export_headers(headers):
    def token(value):
        return "".join(c for c in normalized(value) if c.isalnum())
    if len(headers) != 37 or [token(h) for h in headers] != [token(h) for h in BOOKING_HEADERS]:
        raise ValueError("Header CSV không khớp đúng 37 cột booking DC PHÚ THỌ; chặn trước khi nhập dòng dữ liệu.")


def normalized(value):
    return " ".join(unicodedata.normalize("NFC", str(value)).split()).casefold()


@dataclass
class BookingData:
    rows: pd.DataFrame
    quantity_rules: dict | None = None
    weight_rules: dict | None = None
    handling_policy: dict | None = None
    handling_catalog: dict | None = None
    handling_catalog_sha256: str | None = None
    vehicle_pair: dict | None = None


def is_booking(headers):
    return {normalized(v) for v in ("Ngày LTC", "Mã đơn GHN", "Trạng thái", "Ngày xuất")}.issubset({normalized(v) for v in headers})


def parse_booking(frame, *, quantity_rules=None, weight_rules=None, handling_policy=None,handling_catalog=None,handling_catalog_sha256=None,vehicle_fields=False):
    if not is_booking(frame.columns):
        raise ValueError("Booking thiếu header Ngày LTC/Mã đơn GHN/Trạng thái/Ngày xuất.")
    columns = {normalized(c): c for c in frame}
    if len(columns) != len(frame.columns):
        raise ValueError('Booking có header trùng sau chuẩn hóa; chưa chọn cột.')
    def source_date(value):
        if pd.isna(value) or value == "":
            return None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return date(1899, 12, 30)+timedelta(days=int(value)) if 0 <= value < 100000 else None
        text = str(value).strip()
        if text.casefold() in ("nan", "none", "null", "n/a", "na", "-"):
            return None
        for pattern in ("%d/%m/%Y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(text, pattern).date()
            except ValueError:
                pass
        return None
    # Product units are a separate field. Never use merged PCS/carton/lot totals.
    safe = pd.DataFrame(index=frame.index)
    safe["dispatch_date"] = frame[columns[normalized("Ngày xuất")]].map(source_date)
    safe["ltc_date"] = frame[columns[normalized("Ngày LTC")]].map(source_date)
    def order_id(value):
        if pd.isna(value):
            return ""
        text = str(value).strip()
        if text.casefold() in ("nan", "none", "null", "n/a", "na", "-"):
            return ""
        return text if re.fullmatch(r"[A-Za-z0-9_-]{3,80}", text) else ""
    safe["order_id"] = frame[columns[normalized("Mã đơn GHN")]].map(order_id)
    allowed_statuses = {"delivered", "delivering", "transporting", "ready_to_pick", "picking", "picked",
                        "storing", "return", "returned", "cancel", "cancelled", "delivery_fail", "waiting"}
    safe["status"] = frame[columns[normalized("Trạng thái")]].map(lambda v: normalized(v) if normalized(v) in allowed_statuses else "unknown/missing")
    safe["_source"] = frame["_source"].values
    from .booking_quantity import QUANTITY_COLUMNS, cell, business_scalar, business_key
    for source_col,target in zip(('MÃ SO','MÃ DO','Qty (Units)','Tỉnh'),QUANTITY_COLUMNS):
        if normalized(source_col) in columns:
            values=frame[columns[normalized(source_col)]]
            safe[target] = (pd.Series([business_scalar(v) for v in values],index=frame.index,dtype=object)
                            if target=='source_product_units' else values.map(cell))
    from .booking_weight import WEIGHT_HEADER
    if normalized(WEIGHT_HEADER) in columns:
        values=frame[columns[normalized(WEIGHT_HEADER)]]
        safe['source_ghn_weight']=pd.Series([business_scalar(v) for v in values],index=frame.index,dtype=object)
    for source_col,target in (('Mã siêu thị','source_store_id'),('Huyện','source_district')):
        if normalized(source_col) in columns:
            safe[target]=frame[columns[normalized(source_col)]].map(business_key if target=='source_store_id' else cell)
    for source_col,target in (('Tải trọng','source_capacity_raw'),('BIỂN SỐ XE','source_booking_plate')):
        if vehicle_fields and normalized(source_col) in columns:safe[target]=frame[columns[normalized(source_col)]].map(cell)
    return BookingData(safe, quantity_rules, weight_rules,handling_policy,handling_catalog,handling_catalog_sha256)


def booking_context(data, period, warehouses, cutoff, source, handling=None):
    """``handling`` is an owner rule-layer DC result for this exact scope; None keeps the legacy GHN engine."""
    tab = source.get("tab", "DC PHÚ THỌ")
    if len(warehouses) != 1 or normalized(warehouses[0]) != normalized(tab):
        raise ValueError("Booking được khóa theo tab/DC xuất hàng; warehouses phải đúng tên tab, không lọc theo tỉnh nhận.")
    dates = data.rows.dispatch_date
    scoped = data.rows[dates.map(lambda d: d is not None and d.strftime("%Y-%m") == period and d <= cutoff.date())].copy()
    known = scoped[scoped.order_id.ne("")]
    unique = known.order_id.nunique()
    refs = sorted(scoped._source.tolist())
    scope = f"Tab/DC xuất hàng {tab}; kỳ {period}; đến {cutoff.date()}; không lọc tỉnh đích"
    missing_dates = int(dates.isna().sum())
    metrics = {}
    def add(key, label, value, formula, sources=refs):
        metrics[key] = Metric(key, label, int(value), "mã GHN" if key in ("booking.unique_orders", "booking.conflicting_status_ids", "booking.consistent_delivered_ids") else "dòng",
            formula, period, scope, "OWNER SOURCE / chưa đối soát; không xác nhận giao đúng hạn", sources)
    add("booking.rows", "Dòng booking trong kỳ/cutoff đã biết ngày xuất", len(scoped), "count rows with Ngày xuất in period and <= cutoff date")
    add("booking.unique_orders", "Mã GHN khác nhau đọc được trong nguồn", unique, "nunique(nonblank format-readable GHN code); not verified valid orders/trips/drops/PCS")
    add("booking.duplicate_rows", "Dòng có mã GHN lặp ngoài lần đầu, chưa xác nhận lỗi", len(known)-unique, "count(format-readable GHN rows) - nunique(GHN); repeated IDs may be detail/history rows, not duplicate errors")
    add("booking.missing_ids", "Dòng thiếu/sai mã GHN trong phạm vi", scoped.order_id.eq("").sum(), "count blank/invalid GHN IDs in scoped rows")
    add("booking.missing_ltc_dates", "Dòng trong phạm vi thiếu/sai Ngày LTC", scoped.ltc_date.isna().sum(), "count missing/invalid Ngày LTC in scoped rows; never substitute dispatch date")
    grouped = known.groupby("order_id").status.agg(lambda values: set(values))
    add("booking.conflicting_status_ids", "Mã GHN có trạng thái mâu thuẫn trong phạm vi", grouped.map(lambda statuses: len(statuses) > 1).sum(), "count distinct GHN IDs with >1 normalized source statuses; never choose last row")
    add("booking.consistent_delivered_ids", "Mã GHN chỉ có trạng thái nguồn delivered trong phạm vi", grouped.map(lambda statuses: statuses == {"delivered"}).sum(), "count distinct GHN IDs whose scoped source-status set is exactly delivered; not actual completion or SLA")
    add("booking.unscoped_dates", "Dòng thiếu/sai ngày xuất, chưa phân được vào kỳ", missing_dates, "count missing/invalid dispatch dates in selected source", sorted(data.rows.loc[dates.isna(), "_source"].tolist()))
    for status, count in scoped.status.value_counts().items():
        add("booking.status."+status.replace("/", "_"), "Dòng trạng thái nguồn: "+status, count, "count source-status rows; not distinct GHN/SLA")
    from .booking_quantity import quantity_summary
    quantities = quantity_summary(data, scoped)
    for key,label,unit in (
        ('product_units','Số sản phẩm trong thùng — toàn phạm vi','sản phẩm'),
        ('eligible_product_units','Sản phẩm trong phần đủ khóa — không phải tổng toàn phạm vi','sản phẩm'),
        ('missing_quantity_rows','Dòng thiếu Qty(Units)','dòng'),
        ('invalid_quantity_rows','Dòng Qty(Units) không hợp lệ','dòng'),
        ('missing_key_rows','Dòng thiếu khóa GHN/SO/DO cho sản lượng','dòng'),
        ('repeated_key_rows','Dòng trùng khóa GHN+SO+DO cần làm rõ','dòng'),
        ('conflicting_key_rows','Dòng SO+DO trỏ tới nhiều GHN cần làm rõ','dòng'),
        ('quarantined_rows','Dòng cách ly khỏi tổng sản phẩm','dòng')):
        if not quantities['available'] and key!='product_units':
            continue
        value=quantities[key] if quantities['available'] else None
        formula=('sum(J) for distinct scoped GHN+SO+DO; unresolved => N/A; product units, not parcels' if key=='product_units' else
                 'sum(J) of eligible scoped GHN groups only; not whole-scope total' if key=='eligible_product_units' else 'count scoped rows: '+key+'; no dedup/last-row selection')
        metrics['booking.'+key]=Metric('booking.'+key,label,value,unit,formula,
            period,scope,'OWNER SOURCE / chưa đối soát; không xác nhận giao đúng hạn',refs)
    from .booking_weight import weight_summary
    weights=weight_summary(data,scoped,period,cutoff)
    for key,label,unit in (
        ('weight_kg','Kg nguồn — toàn phạm vi','kg'),
        ('eligible_weight_kg','Kg phần GHN đủ nguồn — không phải tổng toàn phạm vi','kg'),
        ('eligible_ghn','GHN đủ tổng kg','mã GHN'),
        ('missing_ghn','GHN không có tổng L','mã GHN'),
        ('invalid_ghn','GHN có L không hợp lệ','mã GHN'),
        ('conflicting_ghn','GHN có nhiều tổng L khác nhau','mã GHN'),
        ('partial_scope_ghn','GHN có DO ngoài kỳ/cutoff hoặc thiếu ngày','mã GHN'),
        ('missing_id_rows','Dòng chưa có GHN để ghi nhận kg','dòng')):
        if not weights['available'] and key!='weight_kg':
            continue
        value=weights[key] if weights['available'] else None
        metric_key='booking.'+key if key in ('weight_kg','eligible_weight_kg') else 'booking.weight_'+key
        sources=weights['metric_sources'].get('eligible_ghn' if key=='eligible_weight_kg' else key,refs)
        metrics[metric_key]=Metric(metric_key,label,value,unit,
            'L: once/GHN; same totals once, conflicts/invalid/missing/partial scope => N/A' if key=='weight_kg' else
            'sum eligible GHN totals only; not whole-scope kg' if key=='eligible_weight_kg' else 'count GHN/rows: '+key,
            period,scope,'OWNER SOURCE / chưa đối soát; không xác nhận giao đúng hạn',sorted(set(sources)))
    from .booking_handling import handling_summary
    if handling is None:
        handling=handling_summary(data,scoped,period,cutoff)
    by_event=handling.get('grain')=='GHN_BOOKING_E_DAY'
    if handling['available']:
        all_refs=sorted(set(data.rows.loc[data.rows.order_id.isin(set(scoped.order_id)-{''}),'_source']))
        group='đợt GHN/ngày' if by_event else 'mã GHN'
        entries=[('component_vnd','Bốc xếp DC — toàn component','VND theo điều khoản'),
            ('eligible_vnd','Bốc xếp DC — subtotal đủ nguồn, không phải tổng','VND theo điều khoản'),
            ('eligible_kg','Kg đủ điều kiện bốc xếp','kg'),
            ('eligible_transfer_ghn',('Đợt GHN/ngày' if by_event else 'GHN')+' tính được bốc xếp',group),
            ('direct_excluded_ghn',('Đợt GHN/ngày' if by_event else 'GHN')+' không áp dụng bốc xếp',group),
            ('unresolved_ghn',('Đợt GHN/ngày' if by_event else 'GHN')+' chưa chốt component bốc xếp',group)]
        formula=('DC fee: L once per GHN + Booking E day event, rate by E, owner rule layer; no borrowing L across days; not settlement/total cost' if by_event else
            'DC fee: eligible L/GHN once, one E rate, full scope; not physical mode/settlement/total cost')
        for key,label,unit in entries:
            metric_key='booking.dc_handling_'+key
            sources=(handling['issue_sources'] if key=='unresolved_ghn' else handling['sources_by_metric'].get(key,all_refs))
            metrics[metric_key]=Metric(metric_key,label,handling[key],unit,formula,
                period,scope,'OWNER SOURCE / chưa đối soát; không xác nhận giao đúng hạn',sorted(set(sources)))
        for i,b in enumerate(handling['rate_buckets'],1):
            for suffix,unit in (('kg','kg'),('vnd','VND theo điều khoản')):
                metric_key=f'booking.dc_handling_rate_{i}_{suffix}'
                metrics[metric_key]=Metric(metric_key,f"Bốc xếp {b['rate_vnd_per_kg']}đ/kg — phần đủ nguồn",b['eligible_'+suffix],unit,
                    'eligible GHN only; exact Decimal kg * effective rate' if suffix=='vnd' else 'L once/eligible GHN at this E rate',
                    period,scope,'OWNER SOURCE / chưa đối soát; không xác nhận giao đúng hạn',sorted(set(b['sources'])))
    sample = {key: {**value.to_dict(), "sources": value.sources[:1], "source_count": len(value.sources),
                    "source_sample_only": len(value.sources) > 1} for key, value in metrics.items()}
    latest = max(scoped.dispatch_date) if not scoped.empty else None
    retrieved = source.get("retrieved_at", source.get("created"))
    provenance = {"retrieved_at": datetime.fromtimestamp(retrieved, ZoneInfo("Asia/Ho_Chi_Minh")).isoformat(timespec="minutes") if isinstance(retrieved, (int, float)) else "chưa được cung cấp",
                  "latest_dispatch_date": str(latest), "mode": "POINT_IN_TIME_SOURCE_NOT_LIVE"}
    provenance['quantity_rule_version']=quantities['rule']['version'] if quantities['available'] else None
    provenance['weight_rule_version']=weights['rule']['version'] if weights['available'] else None
    provenance['source_version']=source['digest']
    if handling['available']:
        provenance.update(handling_policy_version=handling['policy_version'],handling_catalog_version=handling['catalog_version'],handling_catalog_sha256=handling['catalog_sha256'])
        if by_event:provenance.update(handling_grain=handling['grain'],owner_rules_sha256=handling['rules_sha256'])
    limitations=("J là sản phẩm trong thùng, L là tổng kg/GHN; hai grain độc lập. L thiếu/lỗi/mâu thuẫn hoặc GHN chỉ thuộc một phần kỳ/cutoff thì tổng kg N/A, subtotal giữ nhãn riêng. Không suy kiện/thùng/CBM/chuyến/drop, tải xe/chi phí hoặc SLA. delivered chỉ là trạng thái nguồn, chưa đối soát hoặc xác nhận giao thành công. Không cộng/điền xuống tổng gộp. PII không gửi." if weights['available'] else
        "Qty(Units), khi đủ mapping và khóa đã xác nhận, là sản phẩm bên trong thùng, không phải kiện/thùng. Thiếu/lỗi/trùng khóa được cách ly, toàn phạm vi N/A khi chưa đủ; không suy sản phẩm từ PCS/lot. Chưa tính kg/CBM/xe/chuyến/drop/chi phí/OT/SLA/tải sử dụng: thiếu khóa trip/drop, quy tắc tổng theo lot/xe, cam kết và chứng từ. delivered là trạng thái nguồn, không phải SLA. Không cộng/forward-fill tổng ô gộp. Tên tài xế, SĐT, biển số, tên siêu thị và địa chỉ không nằm trong payload/model/report.")
    return {"label": "BOOKING DC — OWNER SOURCE / UNVERIFIED; SHA256 "+source["digest"], "scope": scope,
            "metrics": sample, "booking": True, "latest": latest, "rows": len(scoped), "missing_dates": missing_dates,
            "source_provenance": provenance,
            "quantity_quality": {k:v for k,v in quantities.items() if k not in ('by_ghn','issue_sources')},
            "weight_quality": {k:v for k,v in weights.items() if k not in ('group_evidence','issue_sources','metric_sources')},
            "weight_issue_sources": sorted(set(weights['issue_sources'])),
            "handling_quality":{k:v for k,v in handling.items() if k not in ('issue_sources','group_evidence','sources_by_metric','rate_buckets','eligible_events','scope')},
            "handling_rate_buckets":[{k:v for k,v in b.items() if k!='sources'} for b in handling['rate_buckets']],
            "handling_issue_sources":sorted(set(handling['issue_sources'])),
            "limitations": limitations}
