"""Scoped application API reused by Streamlit and the optional loopback API."""
from copy import deepcopy
from .domain import Principal, allocate, divide, require_scope
from .finance import forecast, actual_pnl, summarize
from .reconciliation import baseline_reconciliation
from .domain import identity, CATEGORIES, number


class SupraService:
    def __init__(self, dataset, document=None):
        require_scope(dataset)
        self.dataset = deepcopy(dataset)
        self.document = deepcopy(document)
        if document and self.dataset.get("document_source", {}).get("sha256") != document["source"]["sha256"]:
            self.dataset["document_source"] = deepcopy(document["source"])
            self.dataset["document_citations"] = {fragment: [row["source"] for row in document["records"] if fragment in row["text"]]
                for fragment in ("3.003.480", "178", "4 xe 1.9T", "18 km", "36 km")}
            self.dataset["version"] = identity([self.dataset["version"], document["source"]["sha256"], "document-citations-v1"])

    def query_pnl(self, principal, *, mode="PLAN", days=None, hub=None, route=None, start=None, end=None):
        principal.require("VIEW_PNL")
        if mode == "PLAN":
            result = forecast(self.dataset, days)
            selected = [r for r in result["routes"] if (not hub or r["hub"] == hub) and (not route or r["route"] == route)]
            if (hub or route) and not selected:
                raise ValueError("Hub/tuyến không tồn tại trong phạm vi.")
            if hub or route:
                result["routes"] = selected
                result["summary"] = {**summarize(selected), "mode": "PLAN", "verification": "UNVERIFIED", "days": result["summary"]["days"]}
                ids = {r["route"] for r in selected}
                result["allocations"] = [r for r in result["allocations"] if r["route"] in ids]
                result["hubs"] = {k: summarize([r for r in selected if r["hub"] == k]) for k in {r["hub"] for r in selected}}
            return result
        if mode != "ACTUAL":
            raise ValueError("Chọn PLAN hoặc ACTUAL; không gộp hai loại số liệu.")
        routes = {r["id"]: r for r in self.dataset["routes"]}
        allowed = {key for key, r in routes.items() if (not route or key == route) and (not hub or r["hub"] == hub)}
        if not allowed:
            raise ValueError("Hub/tuyến không tồn tại.")
        records = [r for r in self.dataset["actuals"] if r.get("route") in allowed or r.get("route") not in routes]
        result = actual_pnl(records, routes, start=start, end=end)
        if self.dataset["issues"]:
            result["summary"] = summarize([])
            result["verification"] = "PARTIAL"
            result["rejected"].extend(self.dataset["issues"])
        result.update(customer="SUPRA", region="PHU_THO", mode="ACTUAL", version=self.dataset["version"])
        return result

    def reconciliation(self, principal):
        principal.require("VIEW_PNL")
        return baseline_reconciliation(self.dataset, self.document)

    def store_plan(self, principal, days=None):
        principal.require("VIEW_PNL")
        plan = forecast(self.dataset, days)
        result = []
        for route in plan["routes"]:
            stores = [s for s in self.dataset["stores"] if s["route"] == route["route"]]
            if len(stores) != int(route["stores"]):
                raise ValueError("Danh mục điểm giao không khớp tuyến.")
            # No kg/store data: explicit equal allocation is PLAN only. Original
            # temporary keys stay labelled; never manufacture real store IDs.
            costs = allocate(route["cost"], {s["id"]: 1 for s in stores})
            revenues = allocate(route["revenue"], {s["id"]: 1 for s in stores})
            for store in stores:
                result.append({"temporary_key": store["id"], "real_store_id": store["real_id"], "route": route["route"],
                    "cost": costs[store["id"]], "revenue": revenues[store["id"]],
                    "profit": revenues[store["id"]]-costs[store["id"]], "mode": "PLAN", "verification": "UNVERIFIED",
                    "allocation_basis": "Chia đều theo điểm tạm, KHÔNG là cost-to-serve thực; chưa có kg/cửa hàng", "source": store["source"]})
        return result

    def import_actual(self, principal, imported, store):
        principal.require("IMPORT_ACTUAL")
        new = deepcopy(self.dataset)
        # Replacement snapshot: route/day records are not appended to duplicate
        # prior versions. Other clients keep their immutable old service version.
        new["actuals"] = [{**r, "verification": "UNVERIFIED"} for r in imported["records"]]
        new["issues"] = []  # Canonical replacement CSV is parsed transactionally.
        new["version"] = identity([self.dataset["version"], imported["source"]["sha256"], "actual-import-v1"])
        new["source"] = {**imported["source"], "plan_source_version": self.dataset["version"]}
        store.save_version(new, new["version"]+":import", "IMPORT_ACTUAL", principal.name,
            {"version": new["version"], "parent_version": self.dataset["version"], "rows": len(new["actuals"]), "source": imported["source"]})
        return SupraService(new, self.document)

    def verify_actual(self, principal, store, verification_ref):
        principal.require("VERIFY_FINANCE")
        if not verification_ref.strip():
            raise ValueError("Cần tham chiếu biên bản đối soát Finance; không suy ra từ nhãn ĐỦ trong Excel.")
        new = deepcopy(self.dataset)
        if new["issues"]:
            raise ValueError("Nguồn còn dòng cách ly; không được xác minh tổng actual thiếu dòng.")
        candidate = [{**r, "verification": "VERIFIED"} for r in new["actuals"]]
        check = actual_pnl(candidate, {r["id"] for r in new["routes"]})
        if not candidate or check["rejected"]:
            raise ValueError("Chưa đủ pcs hóa đơn, giá nguồn, chứng từ, sáu khoản phí và SLA để Finance xác minh.")
        new["actuals"] = [{**r, "verification_ref": verification_ref[:2000], "verified_by": principal.name} for r in candidate]
        new["version"] = identity([self.dataset["version"], principal.name, verification_ref, "finance-verification-v1"])
        store.save_version(new, new["version"]+":verify", "FINANCE_ATTESTATION", principal.name,
            {"parent_version": self.dataset["version"], "verification_ref": verification_ref[:2000],
             "meaning": "human Finance attestation; app did not independently validate the external documents"})
        return SupraService(new, self.document)

    def pilot_status(self, principal):
        from datetime import date, timedelta
        principal.require("VIEW_PNL")
        start = date.fromisoformat(self.dataset["pilot_start"])
        expected = {(str(start+timedelta(days=n)), r["id"]) for n in range(14) for r in self.dataset["routes"]}
        actual = self.query_pnl(principal, mode="ACTUAL", start=str(start), end=str(start+timedelta(days=13)))
        covered = {(r["date"], r["route"]) for r in actual["records"]}
        return {"start": str(start), "end": str(start+timedelta(days=13)), "expected_route_days": len(expected),
            "verified_route_days": len(covered & expected), "missing_route_days": sorted(expected-covered),
            "complete_route_ledger": expected <= covered and not actual["rejected"],
            "complete_pilot": False, "store_vehicle_reconciliation": "Chưa có sổ điểm/xe có chứng từ đã đối soát",
            "verified_savings_vnd": None, "mode": "SHADOW", "actual": actual}

    def query_detail(self, principal, group_by="route", mode="ACTUAL", **filters):
        from .ledger import DetailedLedger
        principal.require("VIEW_PNL")
        ledger = self.dataset.get("detail_ledger")
        if not ledger:
            return {"summary": None, "groups": [], "verification": "NO_DATA", "missing": "Chưa có sổ doanh thu + pool + phân bổ cấp đơn/xe/điểm có chứng từ"}
        return DetailedLedger(ledger["revenue"], ledger["pools"], ledger["allocations"]).query(principal, group_by, filters, mode)

    def attach_legacy(self, principal, store, snapshot):
        principal.require("ANALYZE")
        require_scope(snapshot)
        if snapshot.get("verification") != "UNVERIFIED" or snapshot.get("mode") != "HISTORICAL_SOURCE_MODEL":
            raise ValueError("Snapshot lịch sử không được nâng thành ACTUAL/VERIFIED.")
        if self.dataset.get("legacy_snapshot", {}).get("version") == snapshot["version"]:
            return self
        new = deepcopy(self.dataset)
        new["legacy_snapshot"] = deepcopy(snapshot)
        new["version"] = identity([self.dataset["version"], snapshot["version"], "read-only-legacy"])
        store.save_version(new, new["version"]+":legacy", "READ_ONLY_SOURCE_LINK", principal.name,
            {"source": snapshot["source"], "source_version": snapshot["version"], "parent_version": self.dataset["version"],
             "verification": "UNVERIFIED", "published_to_external_store": False})
        return SupraService(new, self.document)

    def import_business_json(self, principal, store, content, filename, kind):
        import json
        from .ingestion import source_record
        from .ledger import DIMENSIONS, DetailedLedger
        from .daily import daily_incidents
        principal.require("IMPORT_ACTUAL")
        if len(content) > 20000000:
            raise ValueError("JSON vượt 20 MB.")
        def bad_constant(value):
            raise ValueError("JSON chứa NaN/Infinity không hợp lệ.")
        raw = json.loads(content.decode("utf-8-sig"), parse_float=number, parse_constant=bad_constant)
        source = source_record(content, filename)
        new = deepcopy(self.dataset)
        if kind == "detail_ledger":
            allowed = {
                "revenue": {*DIMENSIONS, "revenue_id", "amount", "kg", "pcs", "drops", "drop_id"},
                "pools": {"cost_id", "amount", "category", "behavior", "period_start", "period_end"},
                "allocations": {"allocation_id", "cost_id", "revenue_id", "amount"}}
            data = {}
            for table, columns in allowed.items():
                if not isinstance(raw.get(table), list) or not raw[table] or len(raw[table]) > 100000:
                    raise ValueError("Cần ba bảng chi tiết không rỗng, tối đa 100.000 dòng/bảng.")
                data[table] = []
                for i, item in enumerate(raw[table]):
                    if not isinstance(item, dict):
                        raise ValueError("Dòng chi tiết phải là object.")
                    if table == "revenue":
                        require_scope(item)
                    data[table].append({**{k: v for k, v in item.items() if k in columns}, "mode": "ACTUAL", "verification": "UNVERIFIED",
                        "source": f"{source['file']}#{source['sha256'][:12]}::{table}[{i+1}]"})
            # Financial invariants are checked before storing, without granting
            # VERIFIED to input claims. PLAN here only exercises conservation.
            check = {table: [{**r, "mode": "PLAN"} for r in rows] for table, rows in data.items()}
            DetailedLedger(check["revenue"], check["pools"], check["allocations"]).query(principal, mode="PLAN")
            new[kind] = data
        elif kind == "operational_events":
            if not isinstance(raw, list) or len(raw) > 100000:
                raise ValueError("Timeline JSON phải là danh sách tối đa 100.000 sự kiện.")
            allowed = {"event_id", "customer", "region", "hub", "route", "promised_at", "delivered_at", "scheduled_dc_departure", "actual_dc_departure",
                "wait_minutes", "wait_limit_minutes", "stock_age_minutes", "stock_limit_minutes", "trip_cost_vnd", "trip_cost_limit_vnd",
                "store_cost_vnd", "store_cost_limit_vnd", "kg", "legal_kg", "underload_threshold", "predicted_dc_return", "latest_dc_pickup"}
            events = [{**{k: v for k, v in item.items() if k in allowed}, "legal_verified": False, "verification": "UNVERIFIED",
                "source": f"{source['file']}#{source['sha256'][:12]}::events[{i+1}]"} for i, item in enumerate(raw)]
            if len({e.get("event_id") for e in events}) != len(events):
                raise ValueError("Trùng event_id; không chọn tùy ý một bản.")
            daily_incidents(events)
            new[kind] = events
        else:
            raise ValueError("Loại dữ liệu không được hỗ trợ.")
        new["version"] = identity([self.dataset["version"], source["sha256"], kind])
        store.save_version(new, new["version"]+":business-import", "IMPORT_BUSINESS_DATA", principal.name,
            {"kind": kind, "source": source, "parent_version": self.dataset["version"], "verification": "UNVERIFIED"})
        return SupraService(new, self.document)

    def verify_detail(self, principal, store, verification_ref):
        from .ledger import DetailedLedger
        principal.require("VERIFY_FINANCE")
        if not verification_ref.strip() or not self.dataset.get("detail_ledger"):
            raise ValueError("Cần sổ chi tiết và biên bản Finance đối soát từng pool/đơn/dòng phân bổ.")
        new = deepcopy(self.dataset)
        for table in new["detail_ledger"].values():
            for row in table:
                row["verification"] = "VERIFIED"
                row["verified_by"] = principal.name
        ledger = new["detail_ledger"]
        DetailedLedger(ledger["revenue"], ledger["pools"], ledger["allocations"]).query(principal)
        new["version"] = identity([self.dataset["version"], verification_ref, principal.name, "detail-verify-v1"])
        store.save_version(new, new["version"]+":verify", "FINANCE_ATTESTATION", principal.name,
            {"parent_version": self.dataset["version"], "verification_ref": verification_ref[:2000], "scope": "detailed_ledger", "independent_document_validation": False})
        return SupraService(new, self.document)
