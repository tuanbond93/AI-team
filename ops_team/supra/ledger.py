"""Optional detailed financial ledger: conserved invoices and scoped drill-down.

Revenue is recorded once per billable item. Cost allocation is an explicit edge,
never a fact-to-fact join. Independent route/store/vehicle books are controls.
"""
from collections import defaultdict
from decimal import Decimal
from datetime import date
from .domain import number, money, divide, CATEGORIES, require_scope

DIMENSIONS = ("customer", "region", "hub", "route", "date", "vehicle_id", "store_id", "order_id")


class DetailedLedger:
    def __init__(self, revenue, pools, allocations):
        self.revenue, self.pools, self.allocations = revenue, pools, allocations

    def query(self, principal, group_by="route", filters=None, mode="ACTUAL"):
        principal.require("VIEW_PNL")
        if group_by not in DIMENSIONS or mode not in ("PLAN", "ACTUAL"):
            raise ValueError("Chiều P&L hoặc mode không hợp lệ.")
        filters = filters or {}
        if not set(filters) <= set(DIMENSIONS):
            raise ValueError("Bộ lọc ngoài schema.")
        if (filters.get("customer", principal.customer) != principal.customer
                or filters.get("region", principal.region) != principal.region):
            raise PermissionError("Không được đọc khách hàng/khu vực khác.")
        pools, revenues, issues = {}, {}, []
        for raw in self.pools:
            if raw.get("mode") != mode:
                continue
            key = raw.get("cost_id")
            if not key or key in pools:
                raise ValueError("Cost pool ID trùng/thiếu; không phân bổ cùng một hóa đơn hai lần.")
            if raw.get("category") not in CATEGORIES or not raw.get("source"):
                raise ValueError("Pool cần nhóm chi phí và nguồn.")
            pools[key] = {**raw, "amount": money(number(raw["amount"]))}
        for raw in self.revenue:
            if raw.get("mode") != mode:
                continue
            key = raw.get("revenue_id")
            if not key or key in revenues:
                raise ValueError("Revenue ID trùng/thiếu; không ghi doanh thu đơn hàng hai lần.")
            date.fromisoformat(raw["date"])
            if not raw.get("source") or any(not raw.get(k) for k in ("customer", "region", "route", "order_id")):
                raise ValueError("Doanh thu cần scope, ngày, tuyến, đơn và nguồn.")
            revenues[key] = {**raw, "amount": money(number(raw["amount"])), "kg": number(raw.get("kg"), optional=True),
                "pcs": number(raw.get("pcs"), optional=True, integer=True), "drops": number(raw.get("drops"), optional=True, integer=True)}
        edges, seen, allocated = [], set(), defaultdict(lambda: Decimal(0))
        for raw in self.allocations:
            if raw.get("mode") != mode:
                continue
            key = raw.get("allocation_id")
            if not key or key in seen:
                raise ValueError("Allocation ID trùng/thiếu.")
            seen.add(key)
            pool, revenue = pools.get(raw.get("cost_id")), revenues.get(raw.get("revenue_id"))
            if not pool or not revenue or not raw.get("source"):
                raise ValueError("Phân bổ dẫn pool/doanh thu/nguồn không tồn tại.")
            # Date/window is explicit. Monthly pool can cover many operating days
            # only within its documented accounting window.
            if not pool.get("period_start") or not pool.get("period_end"):
                raise ValueError("Pool cần kỳ vận hành, không tự ghép khác kỳ.")
            operation = date.fromisoformat(revenue["date"])
            if not date.fromisoformat(pool["period_start"]) <= operation <= date.fromisoformat(pool["period_end"]):
                raise ValueError("Chi phí và doanh thu không cùng kỳ vận hành.")
            amount = money(number(raw["amount"]))
            allocated[pool["cost_id"]] += amount
            edges.append({**raw, "amount": amount, "target": revenue, "pool": pool})
        for key, pool in pools.items():
            if allocated[key] != pool["amount"]:
                raise ValueError("Phân bổ không bằng đúng pool gốc; chặn P&L thiếu/trùng: "+key)
        if any(key not in {e["target"]["revenue_id"] for e in edges} for key in revenues):
            raise ValueError("Có doanh thu chưa đối soát chi phí; cần cả dòng phân bổ 0 có chứng từ khi không phát sinh.")
        def included(row):
            return row["customer"] == principal.customer and row["region"] == principal.region and all(row.get(k) == v for k, v in filters.items())
        chosen = [r for r in revenues.values() if included(r)]
        chosen_ids = {r["revenue_id"] for r in chosen}
        chosen_edges = [edge for edge in edges if edge["target"]["revenue_id"] in chosen_ids]
        # Do not expose other customers' records, IDs or proportional shares.
        if mode == "ACTUAL" and (any(r.get("verification") != "VERIFIED" for r in chosen)
                or any(e["pool"].get("verification") != "VERIFIED" or e.get("verification") != "VERIFIED" for e in chosen_edges)):
            return {"summary": None, "groups": [], "verification": "UNVERIFIED", "missing": "Finance chưa đối soát doanh thu, pool và từng dòng phân bổ"}
        groups = []
        for key in sorted({r.get(group_by) or "UNASSIGNED" for r in chosen}):
            rows = [r for r in chosen if (r.get(group_by) or "UNASSIGNED") == key]
            ids = {r["revenue_id"] for r in rows}
            expenses = [e for e in chosen_edges if e["target"]["revenue_id"] in ids]
            revenue = sum((r["amount"] for r in rows), Decimal(0))
            cost = sum((e["amount"] for e in expenses), Decimal(0))
            kg = sum((r["kg"] for r in rows), Decimal(0)) if all(r["kg"] is not None for r in rows) else None
            pcs = sum((r["pcs"] for r in rows), Decimal(0)) if all(r["pcs"] is not None for r in rows) else None
            drops = Decimal(len({r["drop_id"] for r in rows})) if all(r.get("drop_id") for r in rows) else None
            variable = sum((e["amount"] for e in expenses if e["pool"].get("behavior") == "VARIABLE"), Decimal(0))
            behavior_known = all(e["pool"].get("behavior") in ("FIXED", "VARIABLE") for e in expenses)
            groups.append({"dimension": group_by, "key": key, "revenue": revenue, "cost": cost, "profit": revenue-cost,
                "contribution_margin": revenue-variable if behavior_known else None,
                "kg": kg, "pcs": pcs, "drops": drops, "cost_per_kg": divide(cost, kg), "cost_per_pcs": divide(cost, pcs),
                "cost_per_drop": divide(cost, drops), "cost_revenue_ratio": divide(cost, revenue),
                "sources": sorted({r["source"] for r in rows} | {e["source"] for e in expenses} | {e["pool"]["source"] for e in expenses}),
                "mode": mode, "verification": "VERIFIED" if mode == "ACTUAL" else "UNVERIFIED"})
        total_revenue = sum((r["revenue"] for r in groups), Decimal(0))
        total_cost = sum((r["cost"] for r in groups), Decimal(0))
        return {"summary": {"revenue": total_revenue, "cost": total_cost, "profit": total_revenue-total_cost} if chosen else None,
            "groups": groups, "verification": "VERIFIED" if chosen and mode == "ACTUAL" else "UNVERIFIED" if chosen else "NO_DATA",
            "limitation": "Sổ chi tiết là một sổ thay thế có đối soát, không cộng vào sổ route/day. Tổng pool gồm cả phần dùng chung; kết quả chỉ trả scope được cấp quyền."}
