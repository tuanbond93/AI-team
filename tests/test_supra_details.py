from copy import deepcopy
from decimal import Decimal
from datetime import datetime, timezone
import pytest
from ops_team.supra.domain import Principal
from ops_team.supra.ledger import DetailedLedger, DIMENSIONS
from ops_team.supra.daily import daily_incidents


def detailed():
    revenue = [dict(revenue_id="bill1", customer="SUPRA", region="PHU_THO", hub="VIET_TRI", route="VTRI", date="2026-10-26",
        vehicle_id="VEH-1", store_id="STORE-1", order_id="ORDER-1", drop_id="DROP-1", amount=1000,
        kg=10, pcs=30, drops=1, mode="ACTUAL", verification="VERIFIED", source="SYNTHETIC billing row1"),
        dict(revenue_id="bill2", customer="SUPRA", region="PHU_THO", hub="THANH_BA", route="TB", date="2026-10-27",
        vehicle_id="VEH-2", store_id="STORE-2", order_id="ORDER-2", drop_id="DROP-2", amount=2000,
        kg=20, pcs=60, drops=1, mode="ACTUAL", verification="VERIFIED", source="SYNTHETIC billing row2"),
        dict(revenue_id="ecom-bill", customer="ECOM", region="PHU_THO", hub="PRIVATE", route="PRIVATE", date="2026-10-26",
        vehicle_id="PRIVATE", store_id="PRIVATE", order_id="PRIVATE", drop_id="PRIVATE", amount=999999,
        kg=20, pcs=60, drops=1, mode="ACTUAL", verification="VERIFIED", source="PRIVATE source")]
    pools = [dict(cost_id="5T-HYBRID-ONE-INVOICE", amount=600, category="linehaul", behavior="FIXED",
        period_start="2026-10-01", period_end="2026-10-31", mode="ACTUAL", verification="VERIFIED", source="SYNTHETIC monthly invoice")]
    allocations = [dict(allocation_id=f"allocation-{i}", cost_id=pools[0]["cost_id"], revenue_id=r["revenue_id"], amount=v,
        mode="ACTUAL", verification="VERIFIED", source=f"SYNTHETIC allocation row{i}") for i, (r, v) in enumerate(zip(revenue, (100, 200, 300)))]
    return revenue, pools, allocations


@pytest.mark.parametrize("dimension", DIMENSIONS)
def test_all_dimensions_roll_to_province_without_other_customer(dimension):
    ledger = DetailedLedger(*detailed())
    result = ledger.query(Principal("reader"), dimension)
    assert result["summary"] == {"revenue": Decimal(3000), "cost": Decimal(300), "profit": Decimal(2700)}
    assert sum(r["cost"] for r in result["groups"]) == 300
    assert "PRIVATE" not in str(result) and "999999" not in str(result)


@pytest.mark.parametrize("fault", ["duplicate-pool", "duplicate-revenue", "duplicate-allocation", "overallocate", "underallocate", "wrong-period", "unattributed-revenue"])
def test_detailed_ledger_financial_gates(fault):
    revenue, pools, allocations = detailed()
    if fault == "duplicate-pool": pools.append(deepcopy(pools[0]))
    if fault == "duplicate-revenue": revenue.append(deepcopy(revenue[0]))
    if fault == "duplicate-allocation": allocations.append(deepcopy(allocations[0]))
    if fault == "overallocate": allocations[0]["amount"] = 101
    if fault == "underallocate": allocations[0]["amount"] = 99
    if fault == "wrong-period": revenue[0]["date"] = "2026-11-01"
    if fault == "unattributed-revenue": revenue.append({**revenue[0], "revenue_id": "missing-cost"})
    with pytest.raises(ValueError):
        DetailedLedger(revenue, pools, allocations).query(Principal("reader"))


def test_unverified_actual_and_scoped_filters():
    revenue, pools, allocations = detailed()
    ledger = DetailedLedger(revenue, pools, allocations)
    assert ledger.query(Principal("reader"), "route", {"route": "VTRI"})["summary"]["cost"] == 100
    with pytest.raises(PermissionError):
        ledger.query(Principal("reader"), filters={"customer": "ECOM"})
    revenue[0]["verification"] = "UNVERIFIED"
    assert ledger.query(Principal("reader"))["summary"] is None


def test_drop_unique_not_repeated_per_order_line():
    revenue, pools, allocations = detailed()
    revenue[1]["drop_id"] = revenue[0]["drop_id"]
    result = DetailedLedger(revenue, pools, allocations).query(Principal("reader"), "customer")
    assert result["groups"][0]["drops"] == 1
    assert result["groups"][0]["cost_per_drop"] == 300


def test_daily_rules_thresholds_sources_and_idempotency():
    event = dict(event_id="SYNTHETIC-EVENT", source="SYNTHETIC timeline row1", route="VTRI", hub="VIET_TRI",
        promised_at="2026-10-23T10:00:00+07:00", delivered_at=None,
        scheduled_dc_departure="2026-10-23T09:30:00+07:00", actual_dc_departure="2026-10-23T10:00:00+07:00",
        wait_minutes=90, wait_limit_minutes=60, stock_age_minutes=120, stock_limit_minutes=60,
        trip_cost_vnd=200, trip_cost_limit_vnd=100, store_cost_vnd=100, store_cost_limit_vnd=90,
        kg=2000, legal_kg=1900, legal_verified=True,
        predicted_dc_return="2026-10-23T17:30:00+07:00", latest_dc_pickup="2026-10-23T17:00:00+07:00")
    now = datetime(2026, 10, 24, tzinfo=timezone.utc)
    result = daily_incidents([event], now)
    assert len(result["incidents"]) == 8 and not result["external_effect"]
    assert all(r["sources"] and r["hypothesis"] == "Chưa xác minh nguyên nhân" for r in result["incidents"])
    assert [r["id"] for r in result["incidents"]] == [r["id"] for r in daily_incidents([event], now)["incidents"]]
    event.pop("wait_limit_minutes")
    assert any(g["missing"] == "wait_limit_minutes" for g in daily_incidents([event], now)["missing_data"])
    event["promised_at"] = "2026-10-23T10:00:00"
    with pytest.raises(ValueError, match="timezone"):
        daily_incidents([event], now)
