"""Daily rules consume measured event fields; missing thresholds stay unknown."""
from datetime import datetime, timezone
from .domain import identity, number, require_scope


def daily_incidents(events, now=None):
    now = now or datetime.now(timezone.utc)
    incidents, gaps = [], []
    for event in events:
        require_scope(event)
        if not event.get("event_id") or not event.get("source"):
            raise ValueError("Sự kiện cần event_id và nguồn; không bịa timeline.")
        def stamp(key):
            value = event.get(key)
            if not value:
                return None
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                raise ValueError("Timeline cần timezone rõ.")
            return parsed
        def add(kind, fact, action):
            incidents.append({"id": "INC-"+identity(["SUPRA", "PHU_THO", event["event_id"], kind])[:16],
                "detected_at": now.isoformat(), "event_id": event["event_id"], "customer": "SUPRA", "region": "PHU_THO",
                "hub": event.get("hub"), "route": event.get("route"), "kind": kind, "severity": "HIGH",
                "fact": fact, "sources": [event["source"]], "cost_impact": None, "sla_impact": "Cần xác minh bằng cam kết nguồn",
                "hypothesis": "Chưa xác minh nguyên nhân", "confidence": "SOURCE_RULE_MATCH", "data_verification": event.get("verification", "UNVERIFIED"),
                "proposed_action": action, "approver": "Quản lý vận hành/Finance", "status": "OPEN", "outcome": None})
        promised, delivered = stamp("promised_at"), stamp("delivered_at")
        if promised and ((delivered and delivered > promised) or (not delivered and now > promised)):
            add("ORDER_LATE", "Mốc giao vượt cam kết nguồn", "Kiểm tra booking, POD và liên hệ nội bộ sau duyệt")
        expected, actual = stamp("scheduled_dc_departure"), stamp("actual_dc_departure")
        if expected and actual and actual > expected:
            add("DC_LATE", "Xe rời DC sau lịch nguồn", "Đối chiếu giờ dock, hàng sẵn sàng và GPS")
        pairs = (("wait_minutes", "wait_limit_minutes", "WAIT_TOO_LONG"),
                 ("stock_age_minutes", "stock_limit_minutes", "STOCK_OVERDUE"),
                 ("trip_cost_vnd", "trip_cost_limit_vnd", "TRIP_COST_ANOMALY"),
                 ("store_cost_vnd", "store_cost_limit_vnd", "STORE_COST_HIGH"))
        for value_key, limit_key, kind in pairs:
            value, limit = number(event.get(value_key), optional=True), number(event.get(limit_key), optional=True)
            if value is not None and limit is not None and value > limit:
                add(kind, f"{value_key}={value} > {limit_key}={limit} theo nguồn", "Kiểm chứng định mức, chứng từ và phạm vi trước đề xuất")
            elif value is not None and limit is None:
                gaps.append({"event_id": event["event_id"], "missing": limit_key, "source": event["source"]})
        load, capacity = number(event.get("kg"), optional=True), number(event.get("legal_kg"), optional=True)
        if load is not None and capacity is not None and event.get("legal_verified") is True and load > capacity:
            add("OVERLOAD", "Kg vượt tải hợp pháp được người dùng xác nhận theo nguồn", "Không chạy quá tải; quản lý duyệt phương án hợp pháp")
        if load is not None and capacity and event.get("underload_threshold") is not None and load/capacity < number(event["underload_threshold"]):
            add("UNDERLOAD", "Tải thấp hơn ngưỡng do quản lý cung cấp", "Kiểm tra thể tích, Ecom, báo giá và SLA trước ghép tuyến")
        returned, latest = stamp("predicted_dc_return"), stamp("latest_dc_pickup")
        if returned and latest and returned > latest:
            add("TURNAROUND_RISK", "Dự kiến quay lại sau mốc lấy hàng nguồn", "Kiểm tra timeline và xe dự phòng, không tự điều xe")
    return {"incidents": incidents, "missing_data": gaps, "mode": "SHADOW", "external_effect": False}
