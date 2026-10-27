"""Deterministic calculations. No raw table joins or model arithmetic."""
from dataclasses import dataclass, asdict
import math
import pandas as pd
from .schema import SPECS


@dataclass
class Metric:
    id: str
    label: str
    value: float | None
    unit: str
    formula: str
    period: str | None
    scope: str
    status: str
    sources: list[str]

    def to_dict(self):
        return asdict(self)


def ratio(a, b, multiplier=1):
    if a is None or b is None or b == 0:
        return None
    result = a / b * multiplier
    return result if math.isfinite(result) else None


def compute(validation, period, warehouses, categories=None):
    """Aggregate tables independently at exactly period × selected warehouses.

    A table must cover every selected warehouse. Rejected rows make that table's
    dependent values unavailable, avoiding false improvements from dropped rows.
    """
    tables = {}
    complete = {}
    for kind, df in validation.tables.items():
        tables[kind] = df[df.period.eq(period) & df.warehouse.isin(warehouses)].copy()
        if kind in ("costs", "budgets") and categories is not None:
            tables[kind] = tables[kind][tables[kind].category.isin(categories)]
        # Conservatively block totals if any rows of that table were quarantined:
        # an invalid period or warehouse cannot safely be assigned to a scope.
        complete[kind] = (not validation.rejected[kind] and
            set(warehouses).issubset(set(tables[kind].warehouse)))

    def total(kind, col):
        df = tables[kind]
        return float(df[col].sum(min_count=1)) if complete[kind] and not df.empty else None

    metrics = {}
    scope = ", ".join(sorted(warehouses)) + (" / chi phí: " + ", ".join(categories) if categories is not None else "")

    def add(key, label, value, unit, formula, deps):
        sources = sorted({s for d in deps for s in tables[d]._source})
        good = all(complete[d] for d in deps)
        if not good:
            value = None
        status = "Đủ dữ liệu khai báo" if good and value is not None else "Thiếu dữ liệu / mẫu số 0 / có dòng cách ly"
        metrics[key] = Metric(key, label, value, unit, formula, period, scope, status, sources)

    cost = total("costs", "amount_vnd")
    budget = total("budgets", "budget_vnd")
    volume = total("operations", "volume")
    on_time = total("operations", "on_time")
    regular = total("workforce", "regular_hours")
    overtime = total("workforce", "ot_hours")
    hours = regular + overtime if regular is not None and overtime is not None else None
    processed = total("workforce", "processed_volume")
    add("cost", "Tổng chi phí", cost, "VND", "Σ costs.amount_vnd (không cộng workforce/fleet)", ["costs"])
    add("budget", "Ngân sách", budget, "VND", "Σ budgets.budget_vnd", ["budgets"])
    # Only compare matching category × warehouse sets; no implicit missing budget = 0.
    cost_keys = set(zip(tables["costs"].warehouse, tables["costs"].category))
    budget_keys = set(zip(tables["budgets"].warehouse, tables["budgets"].category))
    delta = cost - budget if cost is not None and budget is not None and cost_keys == budget_keys else None
    add("budget_delta", "Vượt ngân sách", delta, "VND", "Σ actual − Σ budget; cùng tập kho × loại chi phí", ["costs", "budgets"])
    add("volume", "Sản lượng", volume, "kiện", "Σ operations.volume", ["operations"])
    add("cost_per_unit", "Chi phí / kiện", ratio(cost, volume), "VND/kiện", "Σ costs.amount_vnd / Σ operations.volume", ["costs", "operations"])
    add("sla", "Tỷ lệ đúng SLA", ratio(on_time, volume, 100), "%", "Σ on_time / Σ volume × 100 (bình quân có trọng số)", ["operations"])
    # Backlog is a snapshot, never summed across dates. Each warehouse-route must have same snapshot date.
    op = tables["operations"]
    last = op.sort_values("date").groupby(["warehouse", "route"], as_index=False).tail(1)
    backlog = float(last.backlog.sum()) if not last.empty and last.date.nunique() == 1 else None
    add("backlog", "Tồn cuối ngày báo cáo", backlog, "kiện", "Σ snapshot cuối kỳ của từng kho × tuyến, cùng ngày", ["operations"])
    add("hours", "Tổng giờ công", hours, "giờ", "Σ regular_hours + Σ ot_hours", ["workforce"])
    add("productivity", "Năng suất gộp (chỉ mô tả)", ratio(processed, hours), "kiện/giờ", "Σ processed_volume / Σ (regular_hours + ot_hours); so sánh theo stage/goods_type/shift", ["workforce"])
    add("ot_rate", "Tỷ trọng OT", ratio(overtime, hours, 100), "%", "Σ ot_hours / Σ total_hours × 100", ["workforce"])
    add("defect_rate", "Tỷ lệ lỗi xử lý", ratio(total("workforce", "defects"), processed, 100), "%", "Σ defects / Σ processed_volume × 100", ["workforce"])
    add("load_weight", "Sử dụng tải trọng", ratio(total("fleet", "loaded_kg"), total("fleet", "capacity_kg"), 100), "%", "Σ loaded_kg / Σ capacity_kg × 100", ["fleet"])
    add("load_volume", "Sử dụng thể tích", ratio(total("fleet", "loaded_m3"), total("fleet", "capacity_m3"), 100), "%", "Σ loaded_m3 / Σ capacity_m3 × 100", ["fleet"])
    add("empty_rate", "Tỷ lệ km rỗng", ratio(total("fleet", "empty_km"), total("fleet", "km"), 100), "%", "Σ empty_km / Σ km × 100", ["fleet"])
    add("wait_hours", "Tổng giờ chờ xe", total("fleet", "wait_hours"), "giờ", "Σ fleet.wait_hours", ["fleet"])
    trips = len(tables["fleet"]) if complete["fleet"] else None
    add("cost_per_trip", "Chi phí / chuyến", ratio(total("fleet", "trip_cost_vnd"), trips), "VND/chuyến", "Σ trip_cost_vnd / COUNT(trip_id duy nhất)", ["fleet"])
    return metrics


def reconcile(validation, period, warehouses):
    """Reconcile ledger vs detail at category × warehouse, never join raw facts."""
    results = []
    costs = validation.tables["costs"]
    for wh in warehouses:
        for kind, category, amount_col in [("workforce", "labor", "labor_cost_vnd"), ("fleet", "fleet", "trip_cost_vnd")]:
            ledger = costs[costs.period.eq(period) & costs.warehouse.eq(wh) & costs.category.eq(category)]
            detail = validation.tables[kind]
            detail = detail[detail.period.eq(period) & detail.warehouse.eq(wh)]
            good = not validation.rejected["costs"] and not validation.rejected[kind] and not ledger.empty and not detail.empty
            diff = float(ledger.amount_vnd.sum() - detail[amount_col].sum()) if good else None
            results.append(dict(warehouse=wh, category=category, difference=diff,
                status="Khớp" if diff == 0 else "Thiếu dữ liệu" if diff is None else "Lệch cần đối soát",
                sources=[*ledger._source.tolist(), *detail._source.tolist()]))
    return results


def workforce_comparisons(validation, current, previous, warehouses):
    df = validation.tables["workforce"]
    df = df[df.warehouse.isin(warehouses) & df.period.isin([previous, current])].copy()
    # Never compare different work stages / goods mixes as if equivalent.
    if df.empty:
        return []
    df["total_hours"] = df.regular_hours+df.ot_hours
    keys = ["warehouse", "stage", "goods_type", "shift"]
    aggregated = df.groupby([*keys, "period"], sort=True).agg(volume=("processed_volume", "sum"),
        hours=("total_hours", "sum"), defects=("defects", "sum"), sources=("_source", list)).reset_index()
    grouped = {}
    for row in aggregated.itertuples(index=False):
        key = tuple(getattr(row, field) for field in keys)
        if key not in grouped:
            grouped[key] = {**dict(zip(keys, key)), "periods": {previous: None, current: None}}
        if not validation.rejected["workforce"]:
            grouped[key]["periods"][row.period] = {"productivity": ratio(float(row.volume), float(row.hours)),
                "defect_rate": ratio(float(row.defects), float(row.volume), 100), "sources": row.sources}
    return list(grouped.values())


def evaluate_targets(validation, period, warehouses):
    results = []
    targets = validation.tables["kpi"]
    for wh in warehouses:
        metrics = compute(validation, period, [wh])
        for _, row in targets[targets.period.eq(period) & targets.warehouse.eq(wh)].iterrows():
            metric = metrics[row.metric]
            value = metric.value
            breach = None if value is None else (value < row.target if row.direction == "min" else value > row.target)
            results.append(dict(warehouse=wh, metric=row.metric, value=value, target=float(row.target), direction=row.direction,
                breach=breach, assumption=row.assumption, sources=[row._source, *metric.sources]))
    return results
