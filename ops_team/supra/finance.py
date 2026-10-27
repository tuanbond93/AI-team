"""Decimal P&L: one cost pool → one allocation, no fact-table fanout."""
from collections import defaultdict
from decimal import Decimal, localcontext
from datetime import date
from .domain import number, money, divide, allocate, CATEGORIES, require_scope

ZERO = Decimal(0)


def forecast(dataset, days=None):
    require_scope(dataset)
    a, routes = dataset["assumptions"], dataset["routes"]
    days = int(number(days if days is not None else a["days"], integer=True))
    if days not in (26, 30):
        raise ValueError("So sánh kế hoạch chỉ hỗ trợ 26 hoặc 30 ngày cùng kỳ tháng.")
    routes_by_id = {r["id"]: r for r in routes}
    if len(routes_by_id) != len(routes):
        raise ValueError("Trùng tuyến gây phân bổ sai.")
    kg = sum((r["kg"] for r in routes), ZERO)
    if not kg or kg != a["kg_day"]:
        raise ValueError("Kg tuyến không khớp tổng kế hoạch.")
    all_weights = {r["id"]: r["kg"] for r in routes}
    vt = {r["id"]: r["kg"] for r in routes if r["flow"] == "HUB VIỆT TRÌ"}
    hybrid = {r["id"]: r["kg"] for r in routes if r["flow"] in ("HUB THANH BA", "5T GIAO THẲNG")}
    hubs = {r["id"]: r["kg"] for r in routes if r["flow"] in ("HUB THANH BA", "HUB VIỆT TRÌ")}
    pools = [
        ("DC", "dc", kg*a["dc_kg"]*days, all_weights, "VARIABLE", ["01_GIA_DINH!B11"]),
        ("8T", "linehaul", a["truck8_trip"]*a["truck8_trips"]*days, vt, "PER_TRIP", ["01_GIA_DINH!B15", "01_GIA_DINH!B16"]),
        ("5T_HYBRID", "linehaul", a["truck5_month"], hybrid, "FIXED_MONTH", ["01_GIA_DINH!B17"]),
        ("HUB", "hub", kg*a["hub_kg"]*days, hubs, "POOL_PROXY", ["01_GIA_DINH!B12"]),
        ("LAST_MILE_NET", "last_mile", kg*a["last_mile_kg"]*days, all_weights, "PROXY_ALREADY_NET_ECOM", ["01_GIA_DINH!B13"]),
        ("LABOR", "delivery_labor", kg*a["labor_kg"]*days, all_weights, "VARIABLE_PROXY", ["01_GIA_DINH!B14"]),
        ("EXTRA", "extra", a["extra_day"]*days, all_weights, "ASSUMPTION", ["01_GIA_DINH!B27"])]
    # Optional NCC replacement is explicitly a hypothetical linear tariff,
    # never added on top of the same last-mile allocation.
    if a["ncc_drops"] is not None:
        if not a["ncc_package_drops"]:
            raise ValueError("Số drop gói NCC phải dương.")
        if "TSTT" not in routes_by_id:
            raise ValueError("Thiếu tuyến NCC.")
        ncc_cost = a["ncc_package"] / a["ncc_package_drops"] * a["ncc_drops"] * days
        weights = {key: val for key, val in all_weights.items() if key != "TSTT"}
        retained = (kg-routes_by_id["TSTT"]["kg"])*a["last_mile_kg"]*days
        pools = [p for p in pools if p[0] != "LAST_MILE_NET"] + [
            ("LAST_MILE_NET", "last_mile", retained, weights, "PROXY_ALREADY_NET_ECOM", ["01_GIA_DINH!B13"]),
            ("NCC_REPLACEMENT", "last_mile", ncc_cost, {"TSTT": 1}, "LINEAR_DROP_SCENARIO_UNVERIFIED", ["01_GIA_DINH!B21", "01_GIA_DINH!B22", "01_GIA_DINH!B23"])]
    allocations = []
    seen = set()
    for pool_id, category, amount, weights, basis, refs in pools:
        if pool_id in seen:
            raise ValueError("Khoản chi phí đã được phân bổ; từ chối trùng pool.")
        seen.add(pool_id)
        shares = allocate(amount, weights)
        for route_id, share in shares.items():
            allocations.append({"pool_id": pool_id, "route": route_id, "category": category, "amount": share,
                "cost_basis": basis, "mode": "PLAN", "verification": "UNVERIFIED",
                "weight": Decimal(str(weights[route_id])), "weight_total": sum(map(Decimal, map(str, weights.values()))),
                "source": [dataset["refs"][ref] for ref in refs], "rule": "kg share; cents + largest remainder; allocated once"})
    detail = []
    for route in routes:
        costs = {cat: sum((row["amount"] for row in allocations if row["route"] == route["id"] and row["category"] == cat), ZERO) for cat in CATEGORIES}
        amount_kg = route["kg"]*days
        pcs = amount_kg*a["pcs_per_kg"]
        revenue = money(pcs*(a["price_pcs"]+a["surcharge_pcs"]))
        total = sum(costs.values(), ZERO)
        variable = sum((x["amount"] for x in allocations if x["route"] == route["id"] and x["pool_id"] != "5T_HYBRID"), ZERO)
        detail.append({"route": route["id"], "name": route["name"], "hub": route["hub"], "flow": route["flow"],
            "kg": amount_kg, "pcs": pcs, "stores": route["stores"], "drops": None, "vehicle_days": None,
            "revenue": revenue, "cost": total, "contribution_margin": revenue-variable,
            "contribution_definition": "revenue - costs other than fixed monthly 5T; hub pool classification unverified",
            "profit": revenue-total, "cost_per_kg": divide(total, amount_kg), "cost_per_pcs": divide(total, pcs),
            "cost_per_drop": None, "costs": costs, "source": route["source"], "verification": "UNVERIFIED", "mode": "PLAN"})
    summary = summarize(detail)
    planned_vehicle_days = sum((f["vehicles"] for f in dataset["fleets"] if f["vehicles"] is not None), ZERO)*days
    summary.update(vehicle_days=planned_vehicle_days, cost_per_vehicle_day=divide(summary["cost"], planned_vehicle_days),
        vehicle_day_basis="Tổng chi phí tài khoản / 13 xe kế hoạch-ngày; không phải P&L xe/biển số đã phân bổ thực tế")
    summary.update(days=days, daily_average_cost=summary["cost"]/days, daily_average_revenue=summary["revenue"]/days,
        asis_cost=money(kg*a["asis_kg"]*days), asis_profit=summary["revenue"]-money(kg*a["asis_kg"]*days),
        expected_savings=money(kg*a["asis_kg"]*days)-summary["cost"], verified_savings=None,
        mode="PLAN", verification="UNVERIFIED", is_accounting_net_profit=False,
        limitation="P&L phạm vi chi phí khai báo; chưa gồm thuế, tài trợ và khoản ngoài nguồn. Proxy kg không phải chi phí tuyến đã nghiệm thu.")
    grouped = {hub: summarize([r for r in detail if r["hub"] == hub]) for hub in sorted({r["hub"] for r in detail})}
    if sum(r["cost"] for r in detail) != sum(x["amount"] for x in allocations):
        raise ArithmeticError("Phân bổ không khớp sổ gốc.")
    if sum(x["cost"] for x in grouped.values()) != summary["cost"]:
        raise ArithmeticError("Hub/tuyến không khớp toàn tỉnh.")
    return {"summary": summary, "routes": detail, "hubs": grouped, "allocations": allocations,
            "version": dataset["version"], "customer": "SUPRA", "region": "PHU_THO"}


def summarize(rows):
    def total(key):
        return sum((row[key] for row in rows), ZERO) if rows else None
    revenue, cost, kg, pcs = (total(key) for key in ("revenue", "cost", "kg", "pcs"))
    drops = total("drops") if rows and all(r.get("drops") is not None for r in rows) else None
    vehicle_days = total("vehicle_days") if rows and all(r.get("vehicle_days") is not None for r in rows) else None
    route_count = len({r["route"] for r in rows})
    return {"revenue": revenue, "cost": cost, "profit": revenue-cost if rows else None,
        "contribution_margin": total("contribution_margin") if rows and all(r.get("contribution_margin") is not None for r in rows) else None,
        "kg": kg, "pcs": pcs, "drops": drops, "vehicle_days": vehicle_days,
        "cost_per_kg": divide(cost, kg), "cost_per_pcs": divide(cost, pcs),
        "cost_per_drop": divide(cost, drops), "cost_per_vehicle_day": divide(cost, vehicle_days),
        "cost_revenue_ratio": divide(cost, revenue), "route_count": route_count,
        "cost_per_route": divide(cost, Decimal(route_count)),
        "cost_per_store": divide(cost, sum((r.get("stores", ZERO) for r in rows), ZERO))}


def actual_pnl(records, known_routes, *, start=None, end=None):
    """Unknown/incomplete rows never silently disappear into a healthy total."""
    accepted, rejected, seen = [], [], set()
    documents = [r.get("document_ref") for r in records if r.get("document_ref")]
    repeated_documents = {ref for ref in documents if documents.count(ref) > 1}
    for raw in records:
        errors = []
        try:
            require_scope(raw)
            day = date.fromisoformat(raw["date"])
            if start and day < date.fromisoformat(start) or end and day > date.fromisoformat(end):
                continue
            key = (raw["date"], raw["route"])
            if key in seen:
                raise ValueError("Trùng ngày/tuyến; không cộng hai sổ hoặc chọn tùy ý một bản.")
            seen.add(key)
            if raw["route"] not in known_routes:
                raise ValueError("Tuyến chưa có trong danh mục.")
            kg, pcs, drops, on_time = (number(raw.get(k), integer=k in ("pcs", "drops", "on_time")) for k in ("kg", "pcs", "drops", "on_time"))
            if on_time > drops or (kg > 0 and pcs <= 0):
                raise ValueError("SLA/pcs không hợp lệ.")
            costs = {cat: money(number(raw.get("costs", {}).get(cat))) for cat in CATEGORIES}
            if not raw.get("document_ref") or not raw.get("source"):
                raise ValueError("Thiếu tham chiếu chứng từ hoặc nguồn.")
            if raw["document_ref"] in repeated_documents:
                raise ValueError("Tham chiếu chứng từ/bundle trùng nhiều dòng; cần mã dòng phân bổ riêng và Finance đối soát tổng pool, không cộng lặp hóa đơn.")
            if raw.get("verification") != "VERIFIED":
                raise ValueError("ACTUAL chưa được Finance đối soát; không tự nâng trạng thái từ workbook.")
            price = number(raw.get("price_pcs"))
            revenue = money(pcs*price)
            cost = sum(costs.values(), ZERO)
            accepted.append({"date": raw["date"], "route": raw["route"], "kg": kg, "pcs": pcs, "drops": drops,
                "on_time": on_time, "revenue": revenue, "cost": cost, "profit": revenue-cost, "contribution_margin": None,
                "stores": ZERO, "vehicle_days": number(raw.get("vehicle_days"), optional=True), "source": raw["source"],
                "verification": "VERIFIED", "mode": "ACTUAL", "costs": costs})
        except (ValueError, KeyError, TypeError) as exc:
            errors.append(str(exc))
        if errors:
            rejected.append({"source": raw.get("source", "không có nguồn"), "errors": errors})
    # Any duplicate invalidates both copies; no favorable first-row selection.
    duplicates = {k for k in seen if sum((r.get("date"), r.get("route")) == k for r in records) > 1}
    accepted = [r for r in accepted if (r["date"], r["route"]) not in duplicates]
    covered = summarize(accepted)
    covered["sla"] = divide(sum((r["on_time"] for r in accepted), ZERO), covered["drops"])
    return {"summary": summarize([]) if rejected else covered, "covered_subset": covered,
        "records": accepted, "rejected": rejected, "verification": "PARTIAL" if rejected else "VERIFIED" if accepted else "NO_ACTUAL",
        "observed_days": len({r["date"] for r in accepted}), "complete_14_day_pilot": False}
