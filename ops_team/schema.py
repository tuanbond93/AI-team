"""Data contracts, not agent configuration. All amounts are VND."""
from dataclasses import dataclass


@dataclass(frozen=True)
class TableSpec:
    label: str
    grain: str
    key: tuple[str, ...]
    text: tuple[str, ...]
    numeric: tuple[str, ...]
    units: dict[str, str]
    dates: tuple[str, ...] = ()

    @property
    def columns(self):
        return list(dict.fromkeys((*self.key, *self.text, *self.numeric, *self.units, *self.dates)))


SPECS = {
    "costs": TableSpec("Chi phí thực tế", "Một bút toán / dòng chứng từ; record_id duy nhất toàn bảng",
        ("record_id",), ("period", "warehouse", "category", "document_ref"),
        ("amount_vnd",), {"currency": "VND"}, ("service_date",)),
    "budgets": TableSpec("Ngân sách", "Một kỳ × kho × loại chi phí",
        ("period", "warehouse", "category"), (), ("budget_vnd",), {"currency": "VND"}),
    "operations": TableSpec("Sản lượng & SLA", "Một ngày × kho × tuyến",
        ("date", "warehouse", "route"), ("period",),
        ("volume", "on_time", "backlog"), {"quantity_unit": "parcel"}, ("date",)),
    "workforce": TableSpec("Nhân sự", "Một ngày × kho × ca × công đoạn × loại hàng",
        ("date", "warehouse", "shift", "stage", "goods_type"), ("period",),
        ("regular_hours", "ot_hours", "processed_volume", "defects", "labor_cost_vnd"),
        {"hour_unit": "hour", "quantity_unit": "parcel", "currency": "VND"}, ("date",)),
    "fleet": TableSpec("Chuyến xe", "Một chuyến; trip_id duy nhất toàn bảng",
        ("trip_id",), ("period", "warehouse", "route"),
        ("loaded_kg", "capacity_kg", "loaded_m3", "capacity_m3", "km", "empty_km", "wait_hours", "trip_cost_vnd"),
        {"weight_unit": "kg", "volume_unit": "m3", "distance_unit": "km", "currency": "VND"}, ("date",)),
    "kpi": TableSpec("Mục tiêu KPI", "Một kỳ × kho × mã KPI",
        ("period", "warehouse", "metric"), ("direction", "assumption"), ("target",), {"unit": "%"}),
}

AGENT_NAMES = ("Coordinator", "Accounting", "Cost", "Operations", "Workforce", "Fleet", "KPI", "Auditor")
DOMAINS = {
    "Coordinator": "Điều phối & báo cáo quản trị", "Accounting": "Đối soát & chứng từ",
    "Cost": "Chi phí & ngân sách", "Operations": "Năng lực & SLA",
    "Workforce": "Giờ công & chất lượng", "Fleet": "Tải xe & hiệu suất",
    "KPI": "Công thức & mục tiêu", "Auditor": "Kiểm toán bằng chứng độc lập",
}
