"""Exact money, provenance and access checks shared by Supra components."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, ROUND_FLOOR, localcontext
import hashlib
import json
from . import CUSTOMER, REGION

CATEGORIES = ("dc", "linehaul", "hub", "last_mile", "delivery_labor", "extra")
CENT = Decimal("0.01")


def number(value, *, optional=False, integer=False):
    if value is None or value == "":
        if optional:
            return None
        raise ValueError("Thiếu số bắt buộc; không tự thay bằng 0.")
    if isinstance(value, bool):
        raise ValueError("Boolean không phải số tài chính.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("Cần số thuần, không đoán dấu phân cách.") from None
    if not result.is_finite() or result < 0 or (integer and result != result.to_integral_value()):
        raise ValueError("Số phải hữu hạn, không âm và đúng đơn vị/số nguyên.")
    if result > Decimal("1e18") or result.as_tuple().exponent < -12:
        raise ValueError("Số vượt giới hạn độ lớn/độ chính xác đầu vào.")
    return result


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def divide(a, b):
    return a/b if b else None


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, allow_nan=False)


def identity(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def allocate(amount, weights):
    """Allocate once, in cents; distribute rounding residual deterministically."""
    total = money(number(amount))
    parsed = {str(k): number(v) for k, v in weights.items()}
    denominator = sum(parsed.values(), Decimal(0))
    if not parsed or denominator <= 0:
        raise ValueError("Phân bổ cần đối tượng và tổng trọng số dương.")
    with localcontext() as ctx:
        ctx.prec = 40
        raw = {k: total*v/denominator for k, v in parsed.items()}
        result = {k: v.quantize(CENT, rounding=ROUND_FLOOR) for k, v in raw.items()}
        pennies = int((total-sum(result.values()))/CENT)
        for k in sorted(raw, key=lambda key: (-(raw[key]-result[key]), key))[:pennies]:
            result[k] += CENT
    assert sum(result.values()) == total
    return result


@dataclass(frozen=True)
class Principal:
    name: str
    customer: str = CUSTOMER
    region: str = REGION
    permissions: frozenset = frozenset({"VIEW_PNL"})

    def require(self, permission, customer=CUSTOMER, region=REGION):
        if (not self.name or self.customer != customer or self.region != region
                or customer != CUSTOMER or region != REGION or permission not in self.permissions):
            raise PermissionError("Không có quyền cho khách hàng/khu vực hoặc tác vụ này.")


def require_scope(row):
    if row.get("customer", CUSTOMER) != CUSTOMER or row.get("region", REGION) != REGION:
        raise ValueError("Không nhập dữ liệu khách hàng/khu vực khác vào Supra Phú Thọ.")
