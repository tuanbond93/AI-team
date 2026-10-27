"""Source conflicts are findings, never instructions or operating approval."""
from decimal import Decimal
from .finance import forecast
from .domain import divide


def baseline_reconciliation(dataset, document=None):
    a = dataset["assumptions"]
    refs = dataset["refs"]
    findings = []
    def add(code, state, measured, issue, needed, sources):
        findings.append({"id": code, "status": state, "computed": measured, "finding": issue,
            "missing_data": needed, "sources": sources, "verification": "DOCUMENT_ARITHMETIC_ONLY"})
    docs = document["records"] if document else []
    def doc_refs(fragment):
        return [r["source"] for r in docs if fragment in r["text"]] if document else dataset.get("document_citations", {}).get(fragment, [])
    periods = {days: forecast(dataset, days)["summary"] for days in (26, 30)}
    add("DAYS_26_30", "INCONSISTENT", {d: {"revenue": p["revenue"], "cost": p["cost"], "profit": p["profit"]} for d, p in periods.items()},
        "Nguồn dùng sản lượng tháng x30 nhưng lỗ tham chiếu gần kỳ 26 ngày. Xe 5T cố định 57 triệu chỉ tính một lần/tháng.",
        "Finance chốt lịch ngày vận hành, kỳ hợp đồng và chi phí cố định.", [refs["01_GIA_DINH!B5"], *doc_refs("3.003.480")])
    vt = sum(r["kg"] for r in dataset["routes"] if r["flow"] == "HUB VIỆT TRÌ")
    charge8 = a["truck8_trip"]*a["truck8_trips"]
    computed8 = divide(charge8, vt)
    add("TRUCK8_UNIT_RATE", "CORRECTED_WITH_EVIDENCE", {"cost_day": charge8, "kg": vt, "vnd_kg": computed8},
        "Hai chuyến theo giá nguồn chia 13.713 kg = khoảng 215,02 đ/kg, khác 178. Với mẫu số 13.700 kg là khoảng 215,23. Công thức này đã nằm trong pool 8T; không cộng khoản sửa lần hai.",
        "Báo giá/chuyến, kg thực chở và hóa đơn; đây chỉ là sửa số học từ nguồn kế hoạch.",
        [refs["01_GIA_DINH!B15"], refs["01_GIA_DINH!B16"], *doc_refs("178")])
    add("VIET_TRI_CAPACITY", "NEEDS_DATA", {"kg_day": vt, "nominal_kg": 4*a["capacity19"]*2,
        "utilization": divide(vt, 4*a["capacity19"]*2)},
        "Công suất danh định 15.200 kg/ngày và tỷ lệ khoảng 90,22% chưa chứng minh 2 vòng quay hoặc tải hợp pháp.",
        "Đăng kiểm/tải hợp pháp, thể tích, peak, tải Ecom, time windows và GPS thực.",
        [refs["03_DOI_XE!C7"], *doc_refs("4 xe 1.9T")])
    add("DIRECT_SECOND_PICKUP", "NEEDS_DATA", None,
        "Chưa có thời gian quay lại DC, bốc/dỡ và lịch chuyến 2 Đoan Hùng/Phù Ninh trước 17:00.",
        "GPS, giờ dock, thời gian phục vụ và cửa sổ nhận từng điểm.", [r["source"] for r in dataset["routes"] if r["id"] in ("DH", "PN")])
    add("TSTT_CONTRACT", "NEEDS_DATA", {"package_cost": a["ncc_package"], "package_drops": a["ncc_package_drops"], "actual_drops": a["ncc_drops"]},
        "P&L nguồn dùng proxy theo kg. Cơ chế 1,8 triệu/10 điểm chưa xác nhận tuyến tính, làm tròn gói, số xe hay gồm NVGH.",
        "Hợp đồng NCC, drop/xe/ngày, chứng từ và cơ chế lương tránh cộng trùng.", [refs["01_GIA_DINH!B21"], refs["01_GIA_DINH!B23"]])
    add("DC_THANH_BA_DISTANCE", "INCONSISTENT", {"description_km": 18, "schedule_km": 36},
        "Mô tả 18 km và lịch xe 36 km có thể khác một chiều/khứ hồi; chưa xác minh, không tự chọn định mức.",
        "Địa chỉ đúng, hành trình/GPS, một chiều hay khứ hồi.", [*doc_refs("18 km"), *doc_refs("36 km")])
    add("NETWORK_TOTALS", "CONFIRMED", {"routes": len(dataset["routes"]), "stores": sum(r["stores"] for r in dataset["routes"]),
        "kg_day": sum(r["kg"] for r in dataset["routes"])},
        "Tổng các ô kế hoạch khớp trong workbook; 11 địa bàn tách thành 12 luồng. CONFIRMED chỉ là đối chiếu tài liệu, không nghiệm thu thực tế.",
        "Danh sách 130 mã cửa hàng và sản lượng thực.", [r["source"] for r in dataset["routes"]])
    return findings
