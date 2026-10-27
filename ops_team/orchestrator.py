"""Versioned specialist work and independent, explicit audit decisions."""
import time
import json
from .agents import Exchange, RunResult, simulated, audit_answer, ROLE_METRICS
from .schema import AGENT_NAMES, DOMAINS
from .metrics import compute, reconcile, evaluate_targets, workforce_comparisons
from .providers import ProviderError, call_model, validate_output, validate_free_config
from .limits import RequestBudget


def run_team(validation, current, previous, warehouses, question, mode="Demo", configs=None,
             activated=False, data_label="DỮ LIỆU NGƯỜI DÙNG — CHƯA XÁC NHẬN ĐẦY ĐỦ", max_calls=24,
             time_limit=240, call_timeout=30, max_rounds=2, transport=None, progress=None,
             categories=None, scenarios=None, clock=None, free_pilot=False, token_budget=60000, usage_window=None):
    if mode not in ("Demo", "Live") or not warehouses or not current or (previous and previous >= current):
        raise ValueError("Chọn hai kỳ theo thứ tự và ít nhất một kho; chế độ Demo/Live.")
    if not question.strip() or len(question) > 8000:
        raise ValueError("Câu hỏi cần từ 1 đến 8.000 ký tự.")
    if not 1 <= max_calls <= 40 or not 1 <= time_limit <= 600 or not 0 <= max_rounds <= 2 or not 1 <= call_timeout <= 60:
        raise ValueError("Giới hạn lượt gọi/thời gian/vòng bổ sung không hợp lệ.")
    configs = configs or {}
    if mode == "Live":
        if not activated:
            raise ValueError("Live cần người dùng chủ động kích hoạt.")
        missing = [n for n in AGENT_NAMES if n not in configs or not configs[n].model.strip()
                   or (configs[n].provider != "Ollama (local)" and not configs[n].api_key)]
        if missing:
            raise ValueError("Cần cấu hình đủ 8 agent trước Live: " + ", ".join(missing))
        free_pilot = free_pilot or any(c.free_pilot for c in configs.values())
        if free_pilot:
            try:
                for name in AGENT_NAMES:
                    validate_free_config(configs[name])
            except ProviderError as exc:
                raise ValueError(str(exc)) from None
    if not 1000 <= token_budget <= 200000:
        raise ValueError("Ngân sách token mỗi lượt phải từ 1.000 đến 200.000.")
    budget = RequestBudget(max_calls=max_calls, max_tokens=token_budget)
    if usage_window is not None:
        budget.window = usage_window
    tick = clock or time.monotonic
    started = tick()
    now = compute(validation, current, warehouses, categories)
    before = compute(validation, previous, warehouses, categories)
    evidence = {**{k: m.to_dict() for k, m in now.items()}, **{"previous."+k: m.to_dict() for k, m in before.items()}}
    sampled = {k: {**m, "sources": m["sources"][:5], "source_count": len(m["sources"]),
                   "source_sample_only": len(m["sources"]) > 5} for k, m in evidence.items()}
    rec = reconcile(validation, current, warehouses)
    targets = evaluate_targets(validation, current, warehouses)
    groups = workforce_comparisons(validation, current, previous, warehouses)
    def with_source_sample(item, count):
        return {**item, "sources": item["sources"][:count], "source_count": len(item["sources"]),
                "source_sample_only": len(item["sources"]) > count}
    from .analysis import compare_periods
    comparison = compare_periods(validation, current, previous, warehouses, categories)
    private_fragments = [value for c in configs.values() for value in (c.api_key, c.instructions)
                         if value and len(value) >= 8]
    credentials = [c.api_key for c in configs.values() if c.api_key and len(c.api_key) >= 8]
    def redact(text):
        for value in private_fragments:
            text = text.replace(value, "[đã ẩn nội dung phiên]")
        return text
    def omit_question(value):
        if isinstance(value, str):
            return value.replace(question, "[câu hỏi quản trị của phiên]") if len(question) >= 24 else value
        if isinstance(value, list):
            return [omit_question(v) for v in value]
        if isinstance(value, dict):
            return {k: omit_question(v) for k, v in value.items()}
        return value
    exchanges, faults, history, returned = [], [], [], []
    latest, accepted = {}, set()
    specialist_names = set(AGENT_NAMES) - {"Coordinator", "Auditor"}
    calls, operations = 0, 0
    scope = f"{previous} → {current} / {', '.join(warehouses)} / chi phí: {', '.join(categories or ['tất cả'])}"

    def snapshot(e):
        return {"task_id": e.task_id, "logical_id": e.logical_id, "version": e.version,
                "agent": e.receiver, "source_status": e.source_status, "review_status": e.review_status,
                "answer": e.answer}

    def execute(sender, receiver, task, parent="", refs=None, round_number=0,
                phase="analysis", reserve=2, previous_version=None, review_tasks=None):
        nonlocal calls, operations
        e = Exchange(f"T{len(exchanges)+1:03}", parent, sender, receiver, task, scope,
                     "Nhận định có nguồn, giả thuyết, đề xuất và bất đồng; chờ duyệt", refs or [], round=round_number, phase=phase)
        e.logical_id = previous_version.logical_id if previous_version else e.task_id
        e.version = previous_version.version+1 if previous_version else 1
        e.replaces = previous_version.task_id if previous_version else ""
        exchanges.append(e)
        if receiver in specialist_names:
            latest[e.logical_id] = e
            if previous_version:
                accepted.discard(previous_version.task_id)
        if progress:
            progress(receiver, len(exchanges))
        remaining = time_limit - (tick()-started)
        # Demo uses the same scheduling budget, while API calls remains exactly 0.
        if max(operations, budget.attempts) >= max_calls-reserve or remaining <= (min(call_timeout, 2)*reserve):
            e.status, e.review_status = "Dừng giới hạn", "Chưa được kiểm toán"
            e.error = "Đã giữ ngân sách cho Auditor/Coordinator cuối; thiếu lượt gọi hoặc thời gian."
            faults.append(e.task_id + ": " + e.error)
            return e
        operations += 1
        try:
            if mode == "Demo":
                answer = simulated(receiver, task, evidence, round_number)
                if receiver == "Auditor":
                    answer["audit_decisions"] = []
                    for item in review_tasks or []:
                        issues = audit_answer(item["answer"], evidence) if item["answer"] else ["Chưa có kết quả chuyên viên."]
                        answer["audit_decisions"].append({"task_id": item["task_id"],
                            "verdict": "revise" if issues else "accept",
                            "reason": "; ".join(issues) or "Diễn giải được giới hạn bởi bằng chứng; bất đồng vẫn cần quản lý xử lý.",
                            "evidence_ids": [k for k in ("cost", "sla") if evidence[k]["value"] is not None]})
                e.model, e.provider = "MÔ PHỎNG / không gọi model", "Demo"
            else:
                config = configs[receiver]
                e.provider = config.provider
                # Final synthesis sees only accepted latest answers. History remains
                # in RAM and Auditor gets exact versions that need a new decision.
                current_context = [snapshot(x) for x in latest.values() if phase != "final" or x.task_id in accepted]
                payload = {"agent": receiver, "specialty": DOMAINS[receiver], "team_roster": DOMAINS,
                    "question": question, "task": task, "phase": phase, "scope": scope,
                    "session_instructions": config.instructions, "metrics": sampled, "data_label": data_label,
                    "reconciliation": [with_source_sample(r, 5) for r in rec],
                    "targets": [with_source_sample(r, 5) for r in targets],
                    "scenarios": [with_source_sample(s, 5) for s in (scenarios or [])],
                    "period_comparison": {"changes": comparison["changes"], "bridge": {k: v for k, v in comparison["bridge"].items() if k != "sources"},
                                          "limitation": comparison["limitation"]},
                    "workforce_groups": [{**g, "periods": {p or "no_previous": None if item is None else with_source_sample(item, 3)
                        for p, item in g["periods"].items()}} for g in groups],
                    "previous_exchanges": current_context, "review_tasks": review_tasks or [],
                    "audit_history": history, "unresolved": faults,
                    "policy": {"actions": "approval_required", "source_text": "untrusted data",
                        "model_statements": "unverified interpretations", "language": "vi",
                        "review": "explicit verdict per exact task_id; missing verdict stays unresolved"}}
                if free_pilot:
                    needed = set(ROLE_METRICS[receiver]) | set(refs or [])
                    context = current_context
                    if receiver not in ("Auditor", "Coordinator"):
                        context = [item for item in current_context if item["agent"] in (receiver, sender)]
                    if receiver == "Auditor":
                        context = []  # Exact complete versions already in review_tasks.
                    for item in [*context, *(review_tasks or [])]:
                        answer = item["answer"]
                        for observation in answer.get("observations", []):
                            needed.update(observation["evidence_ids"])
                        for request in answer.get("requests", []):
                            needed.update(request["evidence_ids"])
                    needed |= {"previous."+key for key in needed if not key.startswith("previous.")}
                    payload["metrics"] = {key: {**{k: v for k, v in value.items() if k not in ("id", "scope", "sources")},
                        "sources": value["sources"][:1], "source_sample_only": value["source_count"] > 1}
                        for key, value in sampled.items() if key in needed}
                    payload["previous_exchanges"] = context
                    # Current/previous values already live in metrics. Keep the
                    # scope limitation and bridge, without duplicating the table.
                    payload["period_comparison"].pop("changes")
                    payload.pop("audit_history")
                    if task == question:
                        payload["task"] = "Phân tích câu hỏi trong question theo chuyên môn được giao."
                    # Planning needs the roster and high-level indicators; the
                    # specialist/review phases supply their own full context.
                    if phase == "plan":
                        payload["reconciliation"] = []
                        payload["targets"] = []
                        payload["workforce_groups"] = []
                    if receiver not in ("Coordinator", "Auditor", "Accounting"):
                        payload["reconciliation"] = []
                    if receiver not in ("Coordinator", "Auditor", "Workforce"):
                        payload["workforce_groups"] = []
                    if receiver not in ("Coordinator", "Auditor", "KPI", "Operations"):
                        payload["targets"] = []
                    if receiver == "Coordinator":
                        payload["workforce_groups"] = []
                    payload["reconciliation"] = [{**r, "sources": r["sources"][:1],
                        "source_sample_only": r["source_count"] > 1} for r in payload["reconciliation"]]
                    payload["targets"] = [{**r, "sources": r["sources"][:1],
                        "source_sample_only": r["source_count"] > 1} for r in payload["targets"]]
                    payload["context_scope"] = {"metrics": "role + cited IDs and previous period; source sample 1 per metric; full local evidence retained",
                        "history": "complete current versions needed by this role; Auditor review_tasks never truncated; immutable older verdicts retained locally, unresolved faults included",
                        "output": "concise Vietnamese; up to 3 observations/proposals, no repeated metric catalog; audit every exact task_id"}
                if any(secret in json.dumps(payload, ensure_ascii=False) for secret in credentials):
                    raise ProviderError("Nội dung phân tích trùng khóa API trong phiên; hãy bỏ khóa khỏi câu hỏi/chỉ dẫn/dữ liệu trước khi gửi.")
                if free_pilot and transport is None:
                    try:
                        answer, e.model = call_model(config, payload, timeout=min(call_timeout, remaining), budget=budget,
                            deadline=time.monotonic()+remaining)
                    finally:
                        calls = budget.attempts
                else:
                    calls += 1
                    answer, e.model = (transport or call_model)(config, payload, timeout=min(call_timeout, remaining))
                e.provider = config.provider
                e.usage = answer.pop("_usage", {}) if isinstance(answer, dict) else {}
                serialized = json.dumps(answer, ensure_ascii=False)
                if any(value in serialized or value in str(e.model) for value in private_fragments):
                    raise ProviderError("Đầu ra chứa nội dung cấu hình riêng của phiên; đã chặn hiển thị và xuất.")
            e.answer = validate_output(omit_question(answer), set(evidence))
            e.status = "Đã trả lời"
            if receiver in specialist_names:
                issues = audit_answer(e.answer, evidence)
                e.source_status = "Đạt kiểm tra nguồn" if not issues else "Thiếu bằng chứng"
                e.status = "Chờ Auditor" if not issues else "Cần bổ sung nguồn"
        except ProviderError as exc:
            e.answer = {}
            e.status, e.review_status = "Lỗi API / đầu ra", "Chưa được kiểm toán"
            e.error = redact(str(exc))
            if any(value in str(e.model) for value in private_fragments):
                e.model = "Định danh bị chặn"
            faults.append(e.task_id + ": " + e.error)
        return e

    plan = execute("Người quản lý", "Coordinator", question, phase="plan")
    requested = plan.answer.get("selected_agents", [])
    chosen = list(dict.fromkeys(n for n in requested if n in specialist_names))
    if not chosen or any(n not in specialist_names for n in requested):
        faults.append("Coordinator chưa giao đủ nhiệm vụ hợp lệ; không tạo kế hoạch giả thay thế.")
    main = [execute("Coordinator", n, question, plan.task_id) for n in chosen]
    # Clarifications have their own logical versions, retain originating task, and
    # never recurse past two rounds or sixty exchanges even with adversarial output.
    queue = [(e, r, 1) for e in main for r in e.answer.get("requests", [])]
    while queue:
        origin, request, number = queue.pop(0)
        if number > max_rounds or request["to"] not in specialist_names or len(exchanges) >= 60:
            faults.append(origin.task_id + ": chưa đóng yêu cầu bổ sung: " + request["question"])
            continue
        reply = execute(origin.receiver, request["to"], request["question"], origin.logical_id,
                        request["evidence_ids"], number)
        if reply.answer:
            queue.extend((reply, r, number+1) for r in reply.answer.get("requests", []))

    audit_run = None
    # All corrections are re-audited. An acceptance is bound to a particular
    # version ID; it can never be inherited by a replacement or overwrite history.
    for wave in range(max_rounds+1):
        if not latest or all(x.task_id in accepted for x in latest.values()):
            break
        pending = list(latest.values())
        audit_run = execute("Coordinator", "Auditor", "Kiểm toán phiên bản đang chờ", plan.task_id,
                            phase="audit", reserve=1, review_tasks=[snapshot(e) for e in pending])
        if not audit_run.answer:
            break
        decisions = audit_run.answer.get("audit_decisions", [])
        if audit_run.answer.get("requests"):
            # Legacy JSON-mode adapters can express a return via requests, but
            # never an implicit acceptance. Repeated returns remain open.
            for request in audit_run.answer["requests"]:
                matches = [e for e in pending if e.receiver == request["to"]]
                if not matches:
                    faults.append(audit_run.task_id + ": Auditor yêu cầu agent/phiên bản ngoài phạm vi: " + request["to"])
                for match in matches:
                    decisions = [d for d in decisions if d["task_id"] != match.task_id]
                    decisions.append({"task_id": match.task_id, "verdict": "revise", "reason": request["question"],
                                      "evidence_ids": request["evidence_ids"]})
        ids = [d["task_id"] for d in decisions]
        expected = {e.task_id for e in pending}
        invalid = len(ids) != len(set(ids)) or any(i not in expected for i in ids)
        if invalid:
            faults.append(audit_run.task_id + ": Auditor dẫn phiên bản sai hoặc ra quyết định trùng; không tự chấp nhận.")
            break
        by_id = {d["task_id"]: d for d in decisions}
        corrections = []
        for e in pending:
            accepted.discard(e.task_id)
            decision = by_id.get(e.task_id)
            source_issues = audit_answer(e.answer, evidence) if e.answer else ["Không có đầu ra."]
            verdict = decision["verdict"] if decision else "unresolved"
            reason = decision["reason"] if decision else "Auditor chưa ra quyết định cho phiên bản này."
            if verdict == "accept" and (not decision["evidence_ids"] or any(
                    evidence[k]["value"] is None or not evidence[k]["sources"] for k in decision["evidence_ids"])):
                verdict, reason = "unresolved", "Auditor chưa dẫn bằng chứng đủ dữ liệu cho quyết định chấp nhận."
            if verdict == "accept" and source_issues:
                verdict, reason = "unresolved", "Nguồn chưa đạt dù Auditor muốn chấp nhận: " + "; ".join(source_issues)
            record = {"review_id": audit_run.task_id, "task_id": e.task_id, "logical_id": e.logical_id,
                      "version": e.version, "verdict": verdict, "reason": reason}
            history.append(record)
            if verdict == "accept":
                accepted.add(e.task_id)
                e.review_status, e.status = "Auditor chấp nhận phiên bản", "Đã được Auditor xem xét"
            else:
                e.review_status = "Auditor trả lại" if verdict == "revise" else "Chưa được Auditor chấp nhận"
                if e.answer:
                    e.status = e.review_status
                if verdict == "revise":
                    returned.append({"task_id": e.task_id, "version": e.version, "problems": [reason]})
                    if e.round < max_rounds and wave < max_rounds:
                        corrections.append((e, reason))
        if not corrections:
            break
        revised = False
        for old, reason in corrections:
            replacement = execute("Auditor", old.receiver, reason, old.logical_id,
                                  round_number=old.round+1, phase="revision", reserve=2, previous_version=old)
            revised = revised or bool(replacement.answer)
            for request in replacement.answer.get("requests", []):
                faults.append(replacement.task_id + ": yêu cầu phát sinh sau bổ sung, chưa đóng: " + request["question"])
        if not revised:
            break

    unresolved_versions = [e.task_id for e in latest.values() if e.task_id not in accepted]
    if unresolved_versions:
        faults.append("Phiên bản chưa được Auditor chấp nhận: " + ", ".join(unresolved_versions))
    if audit_run is None:
        audit_run = execute("Coordinator", "Auditor", "Không có kết quả đủ điều kiện; ghi nhận thiếu dữ liệu",
                            phase="audit", reserve=1, review_tasks=[])
    final = execute("Auditor", "Coordinator", "Tổng hợp bản được chấp nhận và liệt kê phần còn mở",
                    audit_run.task_id, phase="final", reserve=0)
    final_issues = audit_answer(final.answer, evidence) if final.answer else ["Chưa có kết luận Coordinator."]
    faults.extend(final_issues)
    faults.extend("Coordinator còn yêu cầu chưa đóng: " + r["question"] for r in final.answer.get("requests", []))
    disagreements = list(dict.fromkeys([*faults, *[d for e in latest.values() for d in e.answer.get("disagreements", [])],
                                       *audit_run.answer.get("disagreements", []), *final.answer.get("disagreements", [])]))
    metric_gaps = sorted({key for agent in chosen for key in ROLE_METRICS[agent] if now[key].value is None})
    if metric_gaps:
        disagreements.append("Chỉ số chưa đủ dữ liệu / mẫu số 0: " + ", ".join(now[k].label for k in metric_gaps))
    if previous and not comparison["comparable_cost_scope"]:
        disagreements.append(comparison["limitation"])
    source_pass = bool(latest) and all(e.answer and not audit_answer(e.answer, evidence) for e in latest.values())
    review_complete = bool(latest) and not unresolved_versions and bool(audit_run.answer)
    execution_complete = bool(chosen and plan.answer and final.answer) and not faults
    clean_rec = bool(rec) and all(r["difference"] == 0 for r in rec)
    business_pass = review_complete and execution_complete and not disagreements and clean_rec and not validation.issues
    audit = {"passed": business_pass, "sources_passed": source_pass, "review_complete": review_complete,
        "execution_complete": execution_complete, "returned": returned, "history": history,
        "metric_gaps": metric_gaps,
        "unresolved_versions": unresolved_versions, "accepted_task_ids": sorted(accepted),
        "checks": {"nguồn_được_tham_chiếu": source_pass, "không_join_fact_thô": True,
            "không_cộng_lại_nhân_sự_xe": True, "chi_tiết_khớp_sổ": clean_rec},
        "limitations": "Kiểm tra nguồn tách với quyết định Auditor. Chấp nhận diễn giải không xác nhận nhân quả, chứng từ gốc hay phê duyệt vận hành."}
    if free_pilot and mode == "Live":
        audit["free_pilot_budget"] = {"attempts_including_retry": budget.attempts, "max_calls": max_calls,
            "reserved_tokens_estimate": budget.reserved_tokens, "max_tokens_estimate": token_budget,
            "per_minute_estimate": budget.tokens_per_minute,
            "scope": "local RAM session window, not organization/day quota or billing verification"}
    if not execution_complete or not review_complete:
        status = "Chưa đủ kết quả — còn nhiệm vụ/kiểm toán chưa đóng"
    elif not business_pass:
        status = "Đã tổng hợp — còn bất đồng hoặc dữ liệu cần xác minh"
    else:
        status = "Đã kiểm toán nội dung — đề xuất vẫn chờ quản lý duyệt"
    result = RunResult(mode, data_label, current, previous, list(warehouses), now, before, exchanges, rec, targets, groups,
        validation.issues, disagreements, audit, calls, round(tick()-started, 2), status, question,
        sorted(accepted), [e.task_id for e in latest.values()], comparison,
        scenarios or [])
    for kind, data in validation.tables.items():
        selected = data[data.warehouse.isin(warehouses) & data.period.isin([current, previous])]
        for period, part in selected.groupby("period"):
            col = "date" if "date" in part else "service_date" if "service_date" in part else None
            result.data_coverage.append({"table": kind, "period": period, "rows": len(part),
                "date_min": part[col].min() if col else None, "date_max": part[col].max() if col else None,
                "observed_days": int(part[col].nunique()) if col else None})
    return result
