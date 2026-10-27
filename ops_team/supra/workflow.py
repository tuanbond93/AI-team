"""Idempotent incidents, four-eye decisions, shadow execution and rollback."""
from .domain import identity, number, Principal
from .capacity import fleet_review
from datetime import datetime, timezone, date, timedelta


def detect_incidents(dataset, finance, actual):
    result = []
    def add(kind, route, severity, fact, refs, missing, action):
        key = "INC-"+identity(["SUPRA", "PHU_THO", dataset["pilot_start"][:7], kind, route])[:16]
        result.append({"id": key, "customer": "SUPRA", "region": "PHU_THO", "hub": route,
            "detected_at": datetime.now(timezone.utc).isoformat(),
            "source_version": dataset["version"],
            "route": route, "kind": kind, "severity": severity, "fact": fact, "sources": refs,
            "cost_impact": None, "sla_impact": None, "hypothesis": "Chưa xác minh nguyên nhân; không coi tương quan là nhân quả",
            "confidence": "ARITHMETIC_ONLY", "missing_data": missing, "proposed_action": action,
            "approver": "Finance + Quản lý vận hành có thẩm quyền", "status": "OPEN", "outcome": None})
    for row in finance["routes"]:
        if row["profit"] < 0:
            add("PLAN_ROUTE_LOSS", row["route"], "HIGH", f"PLAN P&L {row['profit']} VND; chưa là lỗ actual", [row["source"]],
                "Hóa đơn, drop, phí NCC và giờ giao thực", "Đối soát tuyến, không cắt xe từ proxy")
    for fleet in fleet_review(dataset):
        if fleet.get("utilization") is not None and fleet["utilization"] > number(".9"):
            add("CAPACITY_RISK", fleet["id"], "HIGH", f"Tải danh định {fleet['utilization']}; vòng quay chưa xác minh", [fleet["source"]],
                "; ".join(fleet["missing"]), "Kiểm tra peak, Ecom và lịch dock trước tăng tải")
    start = date.fromisoformat(dataset["pilot_start"])
    expected = {(str(start+timedelta(days=i)), r["id"]) for i in range(14) for r in dataset["routes"]}
    covered = {(r["date"], r["route"]) for r in actual["records"]}
    if not expected <= covered or actual["rejected"]:
        add("ACTUAL_NOT_RECONCILED", "ALL", "HIGH", f"Pilot mới đối soát {len(expected & covered)}/{len(expected)} ngày-tuyến; chưa có P&L actual đầy đủ", [dataset["source"]["file"]+"#"+dataset["source"]["sha256"][:12]],
            "168 ngày-tuyến, pcs tính cước, sáu khoản phí, chứng từ và SLA", "Thu thập pilot shadow; không ghi nhận tiết kiệm thực tế")
    return result


def proposal(before, after, *, title, sources, constraints, missing, rollback, approver, sla_impact=None):
    before, after = number(before, optional=True), number(after, optional=True)
    if not sources or not rollback or not approver:
        raise ValueError("Proposal cần nguồn, rollback và người phê duyệt.")
    return {"id": "PROP-"+identity([title, before, after, sources, constraints, missing, sla_impact])[:16],
        "customer": "SUPRA", "region": "PHU_THO", "title": title, "current_option": "Phạm vi kế hoạch hiện tại",
        "proposed_option": title, "before_vnd": before, "after_vnd": after,
        "expected_savings_vnd": before-after if before is not None and after is not None else None,
        "verified_savings_vnd": None, "sla_impact": sla_impact, "constraints": constraints,
        "missing_data": missing, "sources": sources, "confidence": "PLAN_UNVERIFIED",
        "rollback": rollback, "approver": approver, "status": "PROPOSED", "creator": "Supra Account AI"}


class ShadowWorkflow:
    def __init__(self, store):
        self.store = store

    def register(self, principal, item, kind="PROPOSAL"):
        principal.require("ANALYZE")
        if item.get("customer") != principal.customer or item.get("region") != principal.region:
            raise PermissionError("Đối tượng ngoài phạm vi.")
        body = {**item, "creator": principal.name}
        if kind == "INCIDENT":
            fingerprint = identity({k: v for k, v in body.items() if k not in ("detected_at", "source_version", "creator")})
            with self.store.lock:
                prior = self.store.events(item["id"])
                incident_events = [e for e in prior if e["kind"] in ("INCIDENT", "INCIDENT_UPDATE")]
                if incident_events and incident_events[-1]["body"].get("fingerprint") == fingerprint:
                    return item["id"]
                body["fingerprint"] = fingerprint
                event_key = item["id"]+":facts:"+fingerprint
                self.store.append(event_key, item["id"], "INCIDENT_UPDATE" if incident_events else "INCIDENT", principal.name, body)
        else:
            self.store.append(item["id"], item["id"], kind, principal.name, body)
        return item["id"]

    def state(self, principal, entity):
        principal.require("VIEW_PNL")
        events = self.store.events(entity)
        if not events:
            raise ValueError("Không có đối tượng trong sổ sự kiện.")
        body = dict(events[0]["body"])
        for event in events[1:]:
            if event["kind"] == "INCIDENT_UPDATE":
                body.update(event["body"])
        status = next((e["body"]["status"] for e in reversed(events) if "status" in e["body"]), "OPEN")
        return {**body, "status": status, "events": events}

    def decide(self, principal, entity, approved, reason):
        principal.require("APPROVE")
        state = self.state(principal, entity)
        if state["status"] != "PROPOSED" or principal.name == state.get("creator") or not reason.strip():
            raise ValueError("Chỉ duyệt proposal đang chờ, cần lý do và người duyệt khác người tạo.")
        with self.store.lock:
            # Version/state checked again under lock to avoid racing decisions.
            if self.state(principal, entity)["status"] != "PROPOSED":
                raise ValueError("Proposal đã có quyết định.")
            self.store.append(entity+":decision", entity, "DECISION", principal.name,
                {"status": "APPROVED_SHADOW" if approved else "REJECTED", "reason": reason[:2000], "operational_permission": False})

    def execute(self, principal, entity):
        principal.require("EXECUTE_SHADOW")
        state = self.state(principal, entity)
        if state["status"] == "SHADOW_EXECUTED":
            return {"external_effect": False, "idempotent": True}
        if state["status"] != "APPROVED_SHADOW":
            raise PermissionError("Chưa được duyệt shadow; không thực thi vận hành thật.")
        self.store.append(entity+":shadow", entity, "SHADOW_EXECUTION", principal.name,
            {"status": "SHADOW_EXECUTED", "external_effect": False, "verified_savings_vnd": None})
        return {"external_effect": False, "idempotent": False}

    def rollback(self, principal, entity, reason):
        principal.require("APPROVE")
        state = self.state(principal, entity)
        if state["status"] not in ("APPROVED_SHADOW", "SHADOW_EXECUTED", "OUTCOME_RECORDED") or not reason.strip():
            raise ValueError("Không có shadow đã duyệt/thực hiện để rollback.")
        self.store.append(entity+":rollback", entity, "ROLLBACK", principal.name,
            {"status": "ROLLED_BACK", "reason": reason[:2000], "external_effect": False})

    def record_outcome(self, principal, entity, baseline_service, current_service, evidence_ref):
        principal.require("VERIFY_FINANCE")
        state = self.state(principal, entity)
        if state["status"] != "SHADOW_EXECUTED" or not evidence_ref.strip():
            raise ValueError("Cần shadow đã thực hiện và biên bản đo lường Finance.")
        if baseline_service.dataset["version"] == current_service.dataset["version"]:
            raise ValueError("Cần hai phiên bản ACTUAL đã đối soát độc lập.")
        before = baseline_service.query_detail(principal)
        after = current_service.query_detail(principal)
        if any(r["verification"] != "VERIFIED" or not r["summary"] for r in (before, after)):
            raise ValueError("Không đo outcome bằng PLAN, dữ liệu thiếu hoặc chưa Finance xác minh.")
        if {r["key"] for r in before["groups"]} != {r["key"] for r in after["groups"]}:
            raise ValueError("Phạm vi tuyến khác nhau; chưa so sánh như cùng một thử nghiệm.")
        body = {"status": "OUTCOME_RECORDED", "before_version": baseline_service.dataset["version"],
            "after_version": current_service.dataset["version"], "observed_cost_change_vnd": after["summary"]["cost"]-before["summary"]["cost"],
            "expected_savings_vnd": state["expected_savings_vnd"], "verified_savings_vnd": None,
            "evidence_ref": evidence_ref[:2000], "external_effect": False,
            "limitation": "Chênh lệch actual quan sát được; chưa chuẩn hóa sản lượng, thời gian và counterfactual, không xác nhận tiết kiệm nhân quả."}
        self.store.append(entity+":outcome", entity, "OUTCOME", principal.name, body)
        return body


class MockGTalkAdapter:
    def __init__(self, store):
        self.store = store

    def send(self, principal, entity, message):
        principal.require("NOTIFY_MOCK")
        key = entity+":gtalk-mock"
        created = self.store.append(key, entity, "GTALK_MOCK", principal.name,
            {"delivery": "DRY_RUN_NOT_SENT", "business_message": message[:2000], "external_effect": False})
        return {"delivery": "DRY_RUN_NOT_SENT", "idempotent": not created, "external_effect": False}
