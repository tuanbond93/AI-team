"""Declared grains, exact whole-VND costs and traceable facts; no LLM arithmetic."""
from collections import defaultdict
from copy import deepcopy
from datetime import date
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, localcontext
from ..supra.domain import number, identity

D = Decimal
TABLES = {
    "scopes": ("scopeId", "period", "warehouse", "sourceMode", "populationComplete", "completenessConfirmed", "outsideCostsVnd", "marginTargetPct", "roundingPolicy"),
    "orders": ("orderCode", "scopeId", "serviceDate", "province", "vehicleType", "weight", "weightUnit", "pieces", "cartons", "quantityGrain", "laborRateId"),
    "legs": ("legId", "orderCode", "tripId"),
    "trips": ("tripId", "scopeId", "serviceDate", "routeGroup", "stage", "plate", "equipmentType", "capacityKg", "vehicleRateVnd", "currency", "allocationBasis", "manifestComplete"),
    "rates": ("rateId", "province", "validFrom", "validTo", "billingBasis", "minKg", "maxKg", "rateVnd", "currency"),
    "labor_rates": ("laborRateId", "validFrom", "validTo", "minKg", "maxKg", "ratePerKgVnd", "currency"),
    "pools": ("poolId", "scopeId", "allocationBasis", "amountVnd", "currency"),
}


def text(value):
    return "" if value is None else str(value).strip()


def required(row, key):
    value = text(row.get(key))
    if not value:
        raise ValueError("Thiếu "+key)
    return value


def day(value):
    return date.fromisoformat(text(value))


def flag(value):
    if value is True or text(value).lower() == "true":
        return True
    if value is False or text(value).lower() == "false":
        return False
    raise ValueError("Cần true/false khai báo rõ")


def positive(value, integer=False):
    result = number(value, integer=integer)
    if result <= 0:
        raise ValueError("Số phải dương")
    return result


def vnd(value):
    return number(value, integer=True)


def allocate_vnd(amount, weights):
    """Largest remainder, whole VND; stable key tie-break, legacy cents unchanged."""
    total = vnd(amount)
    parsed = {str(k): number(v) for k, v in weights.items()}
    denominator = sum(parsed.values(), D(0))
    if not parsed or denominator <= 0:
        raise ValueError("Tổng trọng số phân bổ phải dương")
    with localcontext() as ctx:
        ctx.prec = 50
        raw = {k: total*w/denominator for k, w in parsed.items()}
        result = {k: q.quantize(D(1), rounding=ROUND_FLOOR) for k, q in raw.items()}
        residual = int(total-sum(result.values()))
        for key in sorted(raw, key=lambda k: (-(raw[k]-result[k]), k))[:residual]:
            result[key] += 1
    assert sum(result.values()) == total
    return result


def issue(code, fact, sources, *, severity="WARNING", action=None, impact=None, formula=None, amount=None):
    return {"id": code+"-"+identity([fact, sources])[:12], "code": code, "severity": severity,
        "fact": fact, "sources": sorted(set(sources)), "cause": "Chưa xác minh nguyên nhân gốc.",
        "hypotheses": [], "verification_questions": ["Nguồn gốc và quy tắc ghi nhận nào giải thích sự kiện này?"],
        "impact": impact or "Chưa định lượng; chưa đủ bằng chứng về thiệt hại hoặc ngày lặp lại.",
        "impact_amount_vnd": amount, "impact_formula": formula,
        "actions": [{"action": action or "Đối chiếu dòng nguồn, bổ sung trường thiếu và nhập lại trước khi duyệt.",
            "owner": "Đầu mối Finance/vận hành — đề xuất, chờ xác nhận",
            "deadline": "Trước khi duyệt báo cáo — đề xuất, chờ chốt ngày"}]}


def bands(rows, price_key, kg, *, inclusive_end=False):
    result = []
    for row in rows:
        low = number(row.get("minKg"))
        high = number(row.get("maxKg"), optional=True)
        if high is not None and high <= low:
            raise ValueError("Nấc kg phải tăng")
        result.append((low, high, number(row.get(price_key)), row))
    result.sort(key=lambda b: b[0])
    end = D(0)
    for low, high, _, _ in result:
        if end is None or low != end:
            raise ValueError("Biểu nấc có khoảng trống/chồng lấn hoặc không bắt đầu từ 0")
        end = high
    if not result or (end is not None and (kg > end if inclusive_end else kg >= end)):
        raise ValueError("Biểu nấc chưa phủ trọng lượng đơn")
    return result


def progressive(kg, rows):
    steps = bands(rows, "ratePerKgVnd", kg, inclusive_end=True)
    raw = sum((max(D(0), min(kg, high if high is not None else kg)-low)*rate
               for low, high, rate, _ in steps), D(0))
    return raw.quantize(D(1), rounding=ROUND_HALF_UP)


def active(row, on):
    start, end = day(row.get("validFrom")), day(row.get("validTo"))
    if end < start:
        raise ValueError("Ngày hiệu lực đảo ngược")
    return start <= on <= end


def revenue_for(order, rates):
    matches = [r for r in rates if required(r, "province").casefold() == order["province"].casefold() and active(r, order["day"])]
    if not matches or any(required(r, "currency") != "VND" for r in matches):
        raise ValueError("Thiếu biểu giá tỉnh/ngày hoặc currency không phải VND")
    bases = {required(r, "billingBasis") for r in matches}
    if len(bases) != 1:
        raise ValueError("Biểu giá trùng cơ sở tính trong cùng tỉnh/ngày")
    basis = next(iter(bases))
    if basis in ("pieces", "cartons"):
        if len(matches) != 1 or order.get(basis) is None:
            raise ValueError("Biểu giá chồng hiệu lực hoặc thiếu số lượng đúng grain")
        return order[basis]*vnd(matches[0].get("rateVnd")), [matches[0]["_source"]]
    if basis == "weight_bracket":
        steps = bands(matches, "rateVnd", order["kg"])
        chosen = [r for low, high, _, r in steps if low <= order["kg"] and (high is None or order["kg"] < high)]
        if len(chosen) == 1:
            return vnd(chosen[0].get("rateVnd")), [chosen[0]["_source"]]
    raise ValueError("billingBasis cần pieces/cartons/weight_bracket (cước VND/đơn)")


def evaluate(tables, *, period=None, warehouses=None, cutoff=None):
    """B2B P&L; B2C retained in every declared shared-cost denominator."""
    tables = deepcopy(tables)
    issues, rejected = [], []
    for table in TABLES:
        tables.setdefault(table, [])
        for index, row in enumerate(tables[table]):
            row["_source"] = text(row.get("_source")) or f"in-memory::{table}:row {index+2}"
    def fail(code, message, rows, severity="WARNING"):
        refs = [r["_source"] for r in rows]
        issues.append(issue(code, message, refs, severity=severity))
        rejected.extend(refs)
    scopes, scope_ids = {}, set()
    for row in tables["scopes"]:
        try:
            key = required(row, "scopeId")
            if key in scope_ids:
                scopes.pop(key, None)
                raise ValueError("scopeId trùng; cách ly phạm vi")
            scope_ids.add(key)
            if period and required(row, "period") != period:
                continue
            if warehouses and required(row, "warehouse") not in warehouses:
                continue
            scope = dict(row, populationComplete=flag(row.get("populationComplete")), completenessConfirmed=flag(row.get("completenessConfirmed")))
            if required(row, "sourceMode") not in ("PLAN", "ACTUAL"):
                raise ValueError("sourceMode phải là PLAN/ACTUAL")
            if required(row, "roundingPolicy") != "HALF_UP_ORDER_VND":
                raise ValueError("Cần khai báo HALF_UP_ORDER_VND cho tiền lương mỗi đơn")
            scope["outside"] = vnd(row.get("outsideCostsVnd"))
            scope["target"] = number(row.get("marginTargetPct"), optional=True)
            if scope["target"] is not None and scope["target"] > 100:
                raise ValueError("Margin target vượt 100%")
            day(required(row, "period")+"-01")
            required(row, "warehouse")
            scopes[key] = scope
        except ValueError as exc:
            fail("SCOPE", str(exc), [row])
    if not scopes:
        issues.append(issue("MISSING_SCOPE", "Chưa có phạm vi tài chính hợp lệ đã khai báo.", []))
    declared_scopes = {text(r.get("scopeId")) for r in tables["scopes"] if text(r.get("scopeId"))}
    unassigned_cost, unassigned_b2b = False, False
    for table in ("orders", "trips", "pools"):
        for row in tables[table]:
            if text(row.get("scopeId")) not in declared_scopes:
                fail("UNKNOWN_SCOPE_REF", table+": scopeId rỗng/không tồn tại; không bỏ qua như ngoài phạm vi.", [row])
                if table == "orders" and row.get("vehicleType") != "B2C":
                    unassigned_b2b = True
                if table in ("trips", "pools"):
                    unassigned_cost = True
    filtered_orders = {text(r.get("orderCode")) for r in tables["orders"] if text(r.get("scopeId")) in declared_scopes and text(r.get("scopeId")) not in scopes}
    filtered_trips = {text(r.get("tripId")) for r in tables["trips"] if text(r.get("scopeId")) in declared_scopes and text(r.get("scopeId")) not in scopes}
    grouped = defaultdict(list)
    for row in tables["orders"]:
        if text(row.get("scopeId")) in scopes:
            grouped[text(row.get("orderCode"))].append(row)
    orders, invalid_population_scopes = {}, set()
    b2b_population_complete = not unassigned_b2b
    for key, rows in grouped.items():
        if len({identity({k: r.get(k) for k in TABLES["orders"]}) for r in rows}) > 1:
            invalid_population_scopes.update(r["scopeId"] for r in rows)
            if any(r.get("vehicleType") != "B2C" for r in rows):
                b2b_population_complete = False
            fail("ORDER_CONFLICT", "Facts cùng orderCode mâu thuẫn; cách ly toàn bộ mã, không chọn dòng đầu/cuối.", rows, "CRITICAL")
            continue
        row = rows[0]
        try:
            required(row, "orderCode")
            on = day(row.get("serviceDate"))
            if on.strftime("%Y-%m") != scopes[required(row, "scopeId")]["period"]:
                raise ValueError("Ngày đơn khác kỳ khai báo")
            if cutoff and on > cutoff:
                invalid_population_scopes.add(row["scopeId"])
                filtered_orders.add(key)
                continue
            if required(row, "vehicleType") not in ("B2B", "B2C"):
                raise ValueError("vehicleType chỉ phân loại B2B/B2C, không phải loại xe")
            unit = required(row, "weightUnit")
            if unit not in ("g", "kg"):
                raise ValueError("Thiếu/sai weightUnit g/kg; không suy đơn vị bằng độ lớn")
            kg = positive(row.get("weight"))/(1000 if unit == "g" else 1)
            quantities = {k: (positive(row[k], integer=True) if text(row.get(k)) else None) for k in ("pieces", "cartons")}
            grain = required(row, "quantityGrain")
            if grain not in quantities or quantities[grain] is None:
                raise ValueError("quantityGrain phải trỏ số pieces/cartons dương đã khai báo")
            orders[key] = dict(row, **quantities, kg=kg, day=on, province=required(row, "province"), sources=[r["_source"] for r in rows])
            if len(rows) > 1:
                issues.append(issue("REPEATED_FACT", "Facts đơn lặp giống nhau được tính một lần; chưa chứng minh dòng thừa.", [r["_source"] for r in rows], severity="IMPROVEMENT"))
        except ValueError as exc:
            invalid_population_scopes.update(r["scopeId"] for r in rows)
            if any(r.get("vehicleType") != "B2C" for r in rows):
                b2b_population_complete = False
            fail("ORDER_INPUT", str(exc), rows)
    trips, seen = {}, set()
    for row in tables["trips"]:
        if text(row.get("scopeId")) not in scopes:
            continue
        try:
            key = required(row, "tripId")
            if key in seen:
                trips.pop(key, None)
                raise ValueError("tripId trùng; không gộp chuyến/xe")
            seen.add(key)
            on = day(row.get("serviceDate"))
            if on.strftime("%Y-%m") != scopes[row["scopeId"]]["period"]:
                raise ValueError("Ngày chuyến khác kỳ")
            if cutoff and on > cutoff:
                filtered_trips.add(key)
                continue
            stage = int(positive(row.get("stage"), integer=True))
            if stage not in (1, 3) or required(row, "currency") != "VND":
                raise ValueError("Chuyến cần stage 1/3 và currency VND")
            if required(row, "allocationBasis") not in ("kg", "pieces", "cartons") or not flag(row.get("manifestComplete")):
                raise ValueError("Cần basis kg/pieces/cartons và manifest B2B/B2C đầy đủ")
            required(row, "routeGroup")
            if required(row, "equipmentType") in ("B2B", "B2C"):
                raise ValueError("equipmentType là loại xe, không phải luồng B2B/B2C")
            plate = "".join(text(row.get("plate")).upper().split()) or "CHUA_XAC_DINH"
            trips[key] = dict(row, stage=stage, capacity=positive(row.get("capacityKg")), rate=vnd(row.get("vehicleRateVnd")), plate=plate, day=on)
            if plate == "CHUA_XAC_DINH":
                issues.append(issue("UNKNOWN_PLATE", "Chuyến "+key+" chưa có biển số: chi phí tạm tính; giữ tripId riêng, không gộp các xe trống.", [row["_source"]]))
        except ValueError as exc:
            fail("TRIP_INPUT", str(exc), [row])
    manifests, leg_ids, memberships = defaultdict(list), set(), set()
    uncertain_orders, invalid_manifest_trips = set(), set()
    for row in tables["legs"]:
        try:
            code, trip, key = required(row, "orderCode"), required(row, "tripId"), required(row, "legId")
            if code not in orders or trip not in trips:
                if code in filtered_orders and trip in filtered_trips:
                    continue
                raise ValueError("Leg thiếu đơn/chuyến hợp lệ hoặc chạm dòng đã cách ly")
            if key in leg_ids or (code, trip) in memberships:
                raise ValueError("Leg trùng hoặc đơn lặp trong cùng manifest; cần grain rõ")
            leg_ids.add(key)
            memberships.add((code, trip))
            if orders[code]["scopeId"] != trips[trip]["scopeId"] or orders[code]["day"] != trips[trip]["day"]:
                raise ValueError("Đơn/chuyến khác scope hoặc ngày")
            manifests[trip].append(code)
        except ValueError as exc:
            uncertain_orders.add(text(row.get("orderCode")))
            invalid_manifest_trips.add(text(row.get("tripId")))
            if text(row.get("tripId")) not in trips:
                unassigned_cost = True
            fail("LEG_INPUT", str(exc), [row])
    allocation_rows, trip_metrics, order_cost = [], [], {k: {1: D(0), 2: D(0), 3: D(0)} for k in orders}
    coverage = {k: set() for k in orders}
    checks = []
    for key, trip in trips.items():
        codes = manifests[key]
        try:
            if not codes:
                raise ValueError("Manifest chuyến rỗng")
            if key in invalid_manifest_trips:
                raise ValueError("Manifest có dòng cách ly/thiếu thành viên; không phân bổ lại từ subset hợp lệ")
            if not scopes[trip["scopeId"]]["populationComplete"]:
                raise ValueError("Chưa xác nhận đủ population B2B/B2C cùng phạm vi")
            weights = {c: orders[c][trip["allocationBasis"]] for c in codes}
            if any(v is None for v in weights.values()):
                raise ValueError("Thiếu mẫu số cùng grain B2B/B2C")
            kg = sum((orders[c]["kg"] for c in codes), D(0))
            turns = max(1, int((kg/trip["capacity"]).to_integral_value(rounding=ROUND_CEILING)))
            amount = trip["rate"]*turns
            allocated = allocate_vnd(amount, weights)
            fill = kg/trip["capacity"]*100
            trip_metrics.append({"tripId": key, "plate": trip["plate"], "stage": trip["stage"], "kg": kg, "capacityKg": trip["capacity"],
                "estimatedTurns": turns, "fillPct": fill, "grain": "declared trip/day/routeGroup", "sources": [trip["_source"]],
                "limitation": "Lượt ước tính theo tải; chưa chứng minh vòng quay/lịch khả thi. Fill = kg manifest/capacity, chưa chia turns."})
            for code, value in allocated.items():
                order_cost[code][trip["stage"]] += value
                coverage[code].add(trip["stage"])
                allocation_rows.append({"component": key, "orderCode": code, "flow": orders[code]["vehicleType"], "stage": trip["stage"], "vnd": value,
                    "basis": trip["allocationBasis"], "weight": weights[code], "denominator": sum(weights.values()), "sources": [trip["_source"], *orders[code]["sources"]]})
            checks.append({"component": key, "original": amount, "allocated": sum(allocated.values()), "difference": amount-sum(allocated.values())})
            if fill < 60:
                issues.append(issue("LOW_FILL", f"Chuyến {key}: fill {fill}% trên manifest/ngày/tuyến đã khai báo, dưới 60%.", [trip["_source"]],
                    action="Kiểm tra lịch, giờ cam kết và manifest trước khi đề xuất ghép chuyến.", impact="Chưa lượng hóa tiền có thể tránh; chưa có phương án khả thi hoặc lịch lặp lại."))
            if kg > trip["capacity"]:
                warning = issue("CAPACITY_ESTIMATE", f"Manifest chuyến {key} có {kg} kg / tải {trip['capacity']} kg: cần {turns} lượt ƯỚC TÍNH theo tải, chưa kết luận một xe quá tải hoặc quay kịp.", [trip["_source"]],
                    action="Đối chiếu đơn vị g/kg, số lượt thực, thời gian và manifest từng lượt với người lập kế hoạch.")
                warning["hypotheses"] = ["Có thể manifest gộp nhiều lượt hoặc đơn vị nguồn sai; chưa kiểm chứng, không tự đổi đơn vị."]
                issues.append(warning)
        except (ValueError, TypeError) as exc:
            uncertain_orders.update(codes)
            fail("TRIP_COST", str(exc), [trip])
    pool_ids = set()
    for pool in tables["pools"]:
        if text(pool.get("scopeId")) not in scopes:
            continue
        codes = [c for c, o in orders.items() if o["scopeId"] == pool["scopeId"]]
        try:
            key = required(pool, "poolId")
            if key in pool_ids:
                raise ValueError("poolId trùng")
            pool_ids.add(key)
            basis = required(pool, "allocationBasis")
            if basis not in ("kg", "pieces", "cartons") or required(pool, "currency") != "VND" or not scopes[pool["scopeId"]]["populationComplete"]:
                raise ValueError("Handling cần VND/basis và population B2B/B2C đầy đủ")
            if pool["scopeId"] in invalid_population_scopes:
                raise ValueError("Population handling có đơn cách ly/ngoài cutoff; không tái tính mẫu số từ subset")
            weights = {c: orders[c][basis] for c in codes}
            if any(v is None for v in weights.values()):
                raise ValueError("Handling thiếu mẫu số B2B/B2C cùng grain")
            amount = vnd(pool.get("amountVnd"))
            allocated = allocate_vnd(amount, weights)
            for code, value in allocated.items():
                order_cost[code][2] += value
                coverage[code].add(2)
                allocation_rows.append({"component": key, "orderCode": code, "flow": orders[code]["vehicleType"], "stage": 2, "vnd": value,
                    "basis": basis, "weight": weights[code], "denominator": sum(weights.values()), "sources": [pool["_source"], *orders[code]["sources"]]})
            checks.append({"component": key, "original": amount, "allocated": sum(allocated.values()), "difference": amount-sum(allocated.values())})
        except (ValueError, TypeError) as exc:
            uncertain_orders.update(codes)
            fail("HANDLING", str(exc), [pool])
    pnl, all_flow_costs = [], []
    for code, order in orders.items():
        b2b = order["vehicleType"] == "B2B"
        rev, labor, refs = None, None, list(order["sources"])
        refs += [s for allocation in allocation_rows if allocation["orderCode"] == code for s in allocation["sources"]]
        if b2b:
            try:
                rev, rate_refs = revenue_for(order, tables["rates"])
                refs += rate_refs
            except ValueError as exc:
                fail("REVENUE_RATE", str(exc)+"; order "+code, [order])
        if b2b or text(order.get("laborRateId")):
            try:
                matches = [r for r in tables["labor_rates"] if required(r, "laborRateId") == required(order, "laborRateId") and active(r, order["day"])]
                if any(required(r, "currency") != "VND" for r in matches):
                    raise ValueError("Lương cần VND")
                labor = progressive(order["kg"], matches)
                refs += [r["_source"] for r in matches]
            except ValueError as exc:
                if b2b:
                    fail("LABOR_RATE", str(exc)+"; order "+code, [order])
        stages = {stage: order_cost[code][stage] if stage in coverage[code] and code not in uncertain_orders else None for stage in (1, 2, 3)}
        if stages[3] is not None and labor is not None:
            stages[3] += labor
        else:
            stages[3] = None
        if b2b and any(v is None for v in stages.values()):
            issues.append(issue("MISSING_STAGE", "Đơn "+code+" chưa đủ chi phí ba chặng/lương; tổng cost và GP chưa xác định.", refs))
        total = sum(stages.values(), D(0)) if all(v is not None for v in stages.values()) else None
        all_flow_costs.append({"orderCode": code, "flow": order["vehicleType"], "totalCost": total, "labor": labor})
        if not b2b:
            continue
        gp = rev-total if rev is not None and total is not None else None
        pnl.append({"orderCode": code, "scopeId": order["scopeId"], "kg": order["kg"], "pieces": order["pieces"], "cartons": order["cartons"],
            "revenue": rev, "stage1": stages[1], "stage2": stages[2], "stage3": stages[3], "labor": labor,
            "totalCost": total, "gp": gp, "marginPct": gp/rev*100 if gp is not None and rev else None, "sources": sorted(set(refs))})
        if gp is not None and gp < 0:
            issues.append(issue("NEGATIVE_ORDER_GP", f"Đơn {code}: doanh thu {rev}, chặng 1 {stages[1]}, chặng 2 {stages[2]}, chặng 3 {stages[3]}, GP {gp} VND.",
                refs, severity="CRITICAL", amount=-gp, formula="max(0, order TotalCost - order Revenue), phạm vi một đơn",
                impact=f"Chênh thiếu {-gp} VND ở đơn này; chưa có bằng chứng lặp mỗi ngày hoặc nguyên nhân gốc.",
                action="Đối chiếu cước đơn với phân bổ từng chuyến/pool và lương; xác minh nguyên nhân trước khi chốt phương án."))
    for scope in scopes.values():
        if not scope["completenessConfirmed"] or scope["outside"] > 0:
            issues.append(issue("COMPLETENESS", "Chưa xác nhận đủ chi phí hoặc có chi phí ngoài ba chặng chưa đưa vào P&L.", [scope["_source"]]))
        if scope["target"] is None:
            issues.append(issue("MISSING_TARGET", "Chưa có kỳ vọng biên lợi nhuận; chưa đánh giá đạt chuẩn.", [scope["_source"]]))
    if not pnl:
        issues.append(issue("MISSING_B2B_FACTS", "Chưa có facts B2B đủ điều kiện để tính P&L trong phạm vi đã chọn.", [s["_source"] for s in scopes.values()],
            action="Cung cấp orders B2B theo kỳ/kho đã chọn và liên kết legs/trips trước khi đối chiếu cước và chi phí."))
    def total(key):
        return sum((r[key] for r in pnl), D(0)) if pnl and all(r[key] is not None for r in pnl) else None
    totals = {key: total(key) for key in ("kg", "pieces", "cartons", "revenue", "stage1", "stage2", "stage3", "labor", "totalCost", "gp")}
    totals["orderCount"] = len(pnl)
    totals["marginPct"] = totals["gp"]/totals["revenue"]*100 if totals["gp"] is not None and totals["revenue"] else None
    for key, numerator in (("revPerPcs", "revenue"), ("costPerPcs", "totalCost"), ("gpPerPcs", "gp")):
        totals[key] = totals[numerator]/totals["pieces"] if totals[numerator] is not None and totals["pieces"] else None
    covered_subset = {"totals": deepcopy(totals), "b2b_facts_count": len(pnl), "population_complete": b2b_population_complete}
    if unassigned_cost:
        for key in ("stage1", "stage2", "stage3", "labor", "totalCost", "gp", "marginPct", "costPerPcs", "gpPerPcs"):
            totals[key] = None
    if not b2b_population_complete:
        totals = {key: None for key in totals}
        issues.append(issue("B2B_COVERAGE", "Có facts B2B cách ly/không xác định; số của subset còn lại không đại diện tổng B2B. Tổng chịu ảnh hưởng được giữ N/A.", sorted(set(rejected))))
    financial_complete = bool(scopes and pnl) and not rejected and all(s["completenessConfirmed"] and s["populationComplete"] and s["outside"] == 0 for s in scopes.values()) and all(totals[k] is not None for k in ("revenue", "stage1", "stage2", "stage3", "totalCost", "gp"))
    if totals["gp"] is not None and totals["gp"] < 0:
        issues.append(issue("NEGATIVE_GP", f"Phần tính được: doanh thu {totals['revenue']} VND, cost {totals['totalCost']} VND, GP {totals['gp']} VND.",
            [s for r in pnl for s in r["sources"]], severity="CRITICAL", amount=-totals["gp"], formula="max(0, TotalCost - Revenue), phần tính được trong phạm vi báo cáo",
            impact=f"Chênh thiếu {abs(totals['gp'])} VND trong phạm vi này; không quy thành thiệt hại/ngày hoặc tiết kiệm thu hồi.",
            action="Đối chiếu cước và chi phí từng chặng; xác minh chứng từ trước khi quyết định đổi xe/nhân sự."))
    targets_met = financial_complete and totals["marginPct"] is not None and all(s["target"] is not None for s in scopes.values())
    for key, scope in scopes.items():
        rows = [r for r in pnl if r["scopeId"] == key]
        rev = sum((r["revenue"] for r in rows if r["revenue"] is not None), D(0))
        gp = sum((r["gp"] for r in rows if r["gp"] is not None), D(0))
        if not b2b_population_complete or not rows or any(r["revenue"] is None or r["gp"] is None for r in rows):
            targets_met = False
        elif scope["target"] is not None and (not rev or gp/rev*100 < scope["target"]):
            targets_met = False
            issues.append(issue("BELOW_TARGET", "Biên GP phần tính được chưa đạt kỳ vọng đã khai báo cho "+key+".", [scope["_source"]]))
    status = "ĐỎ" if any(i["severity"] == "CRITICAL" for i in issues) else "XANH" if targets_met and not issues else "VÀNG"
    return {"profile": "Chief Auditor & Operational Financial Advisor", "totals": totals, "orders": pnl, "trips": trip_metrics,
        "allocations": allocation_rows, "all_flow_costs": all_flow_costs, "checks": checks, "issues": issues, "quarantined_sources": sorted(set(rejected)), "covered_subset": covered_subset,
        "financial_complete": bool(financial_complete), "status": status, "verification": "UNVERIFIED", "modes": sorted({s["sourceMode"] for s in scopes.values()}),
        "scope": "; ".join(f"{k}: {s['period']} / {s['warehouse']}" for k, s in scopes.items()),
        "sources": sorted({r["_source"] for rows in tables.values() for r in rows})}
