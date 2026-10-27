"""Entirely synthetic, explicit PLAN fixture and CSV templates, no business source."""
import csv
from io import StringIO, BytesIO
import zipfile
from .engine import TABLES


def demo_tables():
    scope = dict(scopeId="SYNTHETIC-S1", period="2026-10", warehouse="SYNTHETIC-DC", sourceMode="PLAN", populationComplete=True,
        completenessConfirmed=True, outsideCostsVnd=0, marginTargetPct=20, roundingPolicy="HALF_UP_ORDER_VND")
    orders = [dict(orderCode=k, scopeId=scope["scopeId"], serviceDate="2026-10-10", province=p, vehicleType=flow,
        weight=w, weightUnit="g", pieces=pcs, cartons=1, quantityGrain="pieces", laborRateId="L1")
        for k, w, pcs, p, flow in (("A", 1000, 2, "P1", "B2B"), ("B", 2000, 3, "P2", "B2B"), ("C", 1000, 1, "P3", "B2C"))]
    trips = [dict(tripId=key, scopeId=scope["scopeId"], serviceDate="2026-10-10", routeGroup="SYNTHETIC-R1", stage=stage,
        plate="demo 01", equipmentType="Van", capacityKg=5, vehicleRateVnd=amount, currency="VND", allocationBasis="kg", manifestComplete=True)
        for key, stage, amount in (("T1", 1, 10001), ("T3", 3, 6001))]
    legs = [dict(legId=f"{code}-{trip['tripId']}", orderCode=code, tripId=trip["tripId"]) for code in ("A", "B", "C") for trip in trips]
    rates = [dict(rateId="R"+p, province=p, validFrom="2026-10-01", validTo="2026-10-31", billingBasis="pieces", minKg=None, maxKg=None, rateVnd=rate, currency="VND") for p, rate in (("P1", 20000), ("P2", 15000))]
    labor = [dict(laborRateId="L1", validFrom="2026-10-01", validTo="2026-10-31", minKg=low, maxKg=high, ratePerKgVnd=rate, currency="VND") for low, high, rate in ((0, 1, 1000), (1, None, 500))]
    return dict(scopes=[scope], orders=orders, trips=trips, legs=legs, rates=rates, labor_rates=labor,
        pools=[dict(poolId="H1", scopeId=scope["scopeId"], allocationBasis="kg", amountVnd=1001, currency="VND")])


def csv_bundle(*, demo=False):
    tables = demo_tables() if demo else {}
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for table, headers in TABLES.items():
            stream = StringIO(newline="")
            writer = csv.DictWriter(stream, fieldnames=headers)
            writer.writeheader()
            writer.writerows(tables.get(table, []))
            archive.writestr(table+".csv", stream.getvalue().encode("utf-8-sig"))
    return buffer.getvalue()
