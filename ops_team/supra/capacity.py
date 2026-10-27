"""Feasibility bounds, not a global route optimizer or legal certification."""
from decimal import Decimal, ROUND_CEILING
from .domain import number, divide


def capacity_case(kg, vehicles, legal_kg, trips, *, legal_verified=False, peak_factor=1,
                  ecom_kg=0, loading=None, outbound=None, service=None, returning=None,
                  first_pickup=540, dc_close=1020, store_close=None, cargo_m3=None, vehicle_m3=None):
    kg, n, cap, trips = (number(v, optional=True) for v in (kg, vehicles, legal_kg, trips))
    if any(v is None or v <= 0 for v in (n, cap, trips)):
        return {"status": "NEEDS_DATA", "feasible": None, "minimum_feasible_vehicles": None,
            "missing": ["Số xe, tải hợp pháp hoặc lượt khả thi chưa có"], "legal_verified": legal_verified}
    if n != n.to_integral_value() or trips != trips.to_integral_value() or kg is None:
        raise ValueError("Số xe/lượt phải nguyên; cần khối lượng.")
    demand = kg*number(peak_factor)+number(ecom_kg)
    theoretical = n*cap*trips
    minimum = int((demand/(cap*trips)).to_integral_value(rounding=ROUND_CEILING))
    missing, violations = [], []
    if not legal_verified:
        missing.append("Tải nguồn là danh định; cần tải hợp pháp đã xác minh")
    if demand > theoretical:
        violations.append("Vượt tải tổng theo số xe/lượt; không khuyến nghị chạy quá tải")
    times = (loading, outbound, service, returning)
    last_loading_end = None
    if any(v is None for v in times):
        missing.append("Thiếu thời gian bốc, đi, phục vụ hoặc quay lại; chưa chứng minh vòng quay")
    else:
        load, out, serve, back = [number(v) for v in times]
        cycle = load+out+serve+back
        start = number(first_pickup)
        last_loading_end = start+(trips-1)*cycle+load
        if start < 540 or last_loading_end > number(dc_close):
            violations.append("Chuyến lấy hàng không hoàn tất bốc tại DC trong 09:00–17:00")
        last_delivery = start+(trips-1)*cycle+load+out+serve
        if store_close is None:
            missing.append("Chưa có giờ nhận hàng cửa hàng/SLA")
        elif last_delivery > number(store_close):
            violations.append("Giao hàng ngoài cửa sổ nhận; kịch bản không đạt SLA thời gian")
    if cargo_m3 is None or vehicle_m3 is None:
        missing.append("Thiếu thể tích hàng/thùng xe")
    elif number(cargo_m3) > number(vehicle_m3)*n*trips:
        violations.append("Vượt sức chứa thể tích")
    return {"status": "INFEASIBLE" if violations else "NEEDS_DATA" if missing else "FEASIBLE_UNDER_INPUTS",
        "feasible": False if violations else None if missing else True, "nominal_capacity_kg": theoretical,
        "demand_kg": demand, "utilization": divide(demand, theoretical), "reserve_kg": theoretical-demand,
        "minimum_theoretical_vehicles": minimum, "minimum_feasible_vehicles": minimum if not missing and not violations else None,
        "last_loading_end_minute": last_loading_end, "missing": missing, "violations": violations,
        "legal_verified": legal_verified, "limitation": "Giới hạn khả thi theo đầu vào; chưa tối ưu toàn cục, chưa xét đầy đủ đường cấm/ca làm/điểm giao."}


def fleet_review(dataset, peak_factor=1, ecom_kg=0):
    return [{**fleet, **capacity_case(fleet["kg"], fleet["vehicles"], fleet["legal_kg"], fleet["trips"],
        legal_verified=fleet["legal_capacity_verified"], peak_factor=peak_factor,
        ecom_kg=ecom_kg if fleet["id"] == "F7" else 0)} for fleet in dataset["fleets"]]
