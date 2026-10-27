"""Comparable-period bridges and independent, user-defined what-if cases."""
import math
from .metrics import compute, ratio


def compare_periods(validation, current, previous, warehouses, categories=None):
    now = compute(validation, current, warehouses, categories)
    before = compute(validation, previous, warehouses, categories)
    current_keys, previous_keys = set(), set()
    costs = validation.tables["costs"]
    for period, keys in ((current, current_keys), (previous, previous_keys)):
        data = costs[costs.period.eq(period) & costs.warehouse.isin(warehouses)]
        if categories is not None:
            data = data[data.category.isin(categories)]
        keys.update(zip(data.warehouse, data.category))
    comparable = bool(previous and current_keys and current_keys == previous_keys)
    changes = []
    for key, metric in now.items():
        old, new = before[key].value, metric.value
        allowed = comparable or key not in ("cost", "budget", "budget_delta", "cost_per_unit")
        delta = new-old if previous and allowed and new is not None and old is not None else None
        changes.append({"id": key, "label": metric.label, "previous": old, "current": new,
                        "delta": delta, "change_pct": ratio(delta, old, 100), "unit": metric.unit})
    c0, c1, q0, q1 = before["cost"].value, now["cost"].value, before["volume"].value, now["volume"].value
    bridge = {"ready": False, "volume_effect": None, "cost_effect": None, "total_change": None,
        "formula": "Volume = C0/Q1 − C0/Q0; Cost = (C1−C0)/Q1; tổng = C1/Q1−C0/Q0",
        "order": "Thay sản lượng trước, thay chi phí sau; đơn vị VND/kiện. Không phải kết luận nhân quả.",
        "sources": sorted({s for m in (now["cost"], before["cost"], now["volume"], before["volume"]) for s in m.sources})}
    if comparable and all(v is not None for v in (c0, c1, q0, q1)) and q0 > 0 and q1 > 0:
        bridge.update(ready=True, volume_effect=c0/q1-c0/q0, cost_effect=(c1-c0)/q1, total_change=c1/q1-c0/q0)
    breakdown = []
    for warehouse, category in sorted(current_keys | previous_keys):
        values, sources = {}, []
        for period in (previous, current):
            part = costs[costs.period.eq(period) & costs.warehouse.eq(warehouse) & costs.category.eq(category)]
            values[period] = None if part.empty or validation.rejected["costs"] else float(part.amount_vnd.sum())
            sources.extend(part._source.tolist())
        old, new = values[previous], values[current]
        breakdown.append({"warehouse": warehouse, "category": category, "previous": old, "current": new,
            "delta": new-old if old is not None and new is not None else None, "sources": sources})
    return {"comparable_cost_scope": comparable, "changes": changes, "bridge": bridge, "breakdown": breakdown,
        "limitation": "Chưa có kỳ so sánh" if not previous else "" if comparable else "Tập kho × loại chi phí khác giữa hai kỳ; chặn so sánh tổng chi phí/kiện."}


def what_if(validation, period, warehouses, category, savings_pct, implementation_cost_vnd=0,
            volume_change_pct=0, sla_delta_pp=None, assumption="", categories=None):
    if category not in ("labor", "fleet"):
        raise ValueError("Chỉ mô phỏng riêng nhân sự hoặc xe.")
    values = (savings_pct, implementation_cost_vnd, volume_change_pct)
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
        raise ValueError("Giả định phải là số hữu hạn.")
    if not 0 <= savings_pct <= 100 or implementation_cost_vnd < 0 or not -99 <= volume_change_pct <= 1000:
        raise ValueError("Tỷ lệ giảm 0–100%, chi phí triển khai không âm; sản lượng −99% đến 1000%.")
    if sla_delta_pp is not None and (not math.isfinite(sla_delta_pp) or not -100 <= sla_delta_pp <= 100):
        raise ValueError("Ảnh hưởng SLA phải từ −100 đến 100 điểm phần trăm.")
    metrics = compute(validation, period, warehouses, categories)
    ledger = validation.tables["costs"]
    part = ledger[ledger.period.eq(period) & ledger.warehouse.isin(warehouses) & ledger.category.eq(category)]
    ready = not validation.rejected["costs"] and set(warehouses).issubset(set(part.warehouse))
    ready = ready and (categories is None or category in categories)
    base = float(part.amount_vnd.sum()) if ready else None
    gross = base*savings_pct/100 if base is not None else None
    net = gross-implementation_cost_vnd if gross is not None else None
    cost, volume, sla = (metrics[k].value for k in ("cost", "volume", "sla"))
    projected_cost = cost-net if cost is not None and net is not None else None
    projected_volume = volume*(1+volume_change_pct/100) if volume is not None else None
    projected_sla = sla+sla_delta_pp if sla is not None and sla_delta_pp is not None else None
    if projected_sla is not None and not 0 <= projected_sla <= 100:
        raise ValueError("SLA sau giả định nằm ngoài 0–100%; hãy sửa giả định.")
    return {"id": "scenario_"+category, "category": category,
        "label": "Cân ca / OT" if category == "labor" else "Khai thác tải xe",
        "base_vnd": base, "savings_pct": savings_pct, "implementation_cost_vnd": implementation_cost_vnd,
        "volume_change_pct": volume_change_pct, "sla_delta_pp": sla_delta_pp,
        "gross_savings_vnd": gross, "net_savings_vnd": net, "projected_cost_vnd": projected_cost,
        "projected_cost_per_unit": ratio(projected_cost, projected_volume), "projected_sla": projected_sla,
        "assumption": assumption.strip(), "ready": ready and bool(assumption.strip()),
        "formula": "Tiết kiệm ròng = chi phí loại được chọn × % giả định − chi phí triển khai; không cộng 2 phương án.",
        "sla_status": "Chưa có giả định ảnh hưởng SLA" if sla_delta_pp is None else "Ước tính người dùng; chưa kiểm chứng",
        "sources": sorted({*part._source.tolist(), *metrics["volume"].sources, *metrics["sla"].sources}),
        "owner": "Quản lý nhân sự vận hành" if category == "labor" else "Quản lý đội xe",
        "priority": "P2 — sau xác minh SLA", "conditions": "Duyệt phạm vi thử nghiệm, nguồn dữ liệu và ngưỡng dừng trước khi áp dụng",
        "status": "Chờ người quản lý xem xét"}
