"""Synthetic data with reconciled costs; no real operational policy is implied."""
from io import BytesIO
from pathlib import Path
import pandas as pd
from .schema import SPECS


def demo_tables():
    rows = {k: [] for k in SPECS}
    amounts = {
        "2026-08": {"Duc Hoa": [100, 60, 40], "HCM": [80, 50, 30]},
        "2026-09": {"Duc Hoa": [130, 75, 40], "HCM": [84, 52, 30]},
    }
    for period, warehouses in amounts.items():
        for wh, actual in warehouses.items():
            route = "DH-HCM" if wh == "Duc Hoa" else "HCM-DN"
            current = period == "2026-09"
            for cat, amount in zip(["labor", "fleet", "rent"], actual):
                ref = f"DEMO-{period}-{wh}-{cat}"
                rows["costs"].append(dict(record_id=ref, period=period, warehouse=wh, category=cat,
                    document_ref=ref, amount_vnd=amount * 1e6, currency="VND", service_date=period + "-28"))
                budget = ({"Duc Hoa": [110, 65, 40], "HCM": [84, 51, 30]}[wh][["labor", "fleet", "rent"].index(cat)] if current else amount)
                rows["budgets"].append(dict(period=period, warehouse=wh, category=cat, budget_vnd=budget*1e6, currency="VND"))
            for day in (10, 20):
                date = f"{period}-{day}"
                volume = 1200 if current and wh == "Duc Hoa" else 1000
                sla = .92 if current and wh == "Duc Hoa" else .98
                rows["operations"].append(dict(date=date, period=period, warehouse=wh, route=route,
                    volume=volume, on_time=int(volume*sla), backlog=80 if current and wh == "Duc Hoa" else 15, quantity_unit="parcel"))
                rows["workforce"].append(dict(date=date, period=period, warehouse=wh, shift="day", stage="sort", goods_type="parcel",
                    regular_hours=80, ot_hours=20 if current and wh == "Duc Hoa" else 8, processed_volume=volume,
                    defects=24 if current and wh == "Duc Hoa" else 10, labor_cost_vnd=actual[0]*1e6/2,
                    hour_unit="hour", quantity_unit="parcel", currency="VND"))
                rows["fleet"].append(dict(trip_id=f"DEMO-{date}-{wh}", date=date, period=period, warehouse=wh, route=route,
                    loaded_kg=2500 if current and wh == "Duc Hoa" else 3500, capacity_kg=5000, loaded_m3=15, capacity_m3=30,
                    km=200, empty_km=80 if current and wh == "Duc Hoa" else 40, wait_hours=3 if current and wh == "Duc Hoa" else 1,
                    trip_cost_vnd=actual[1]*1e6/2, weight_unit="kg", volume_unit="m3", distance_unit="km", currency="VND"))
            for metric, target, direction in [("sla", 97, "min"), ("ot_rate", 15, "max"), ("empty_rate", 25, "max")]:
                rows["kpi"].append(dict(period=period, warehouse=wh, metric=metric, target=target, direction=direction,
                    assumption="DEMO - chờ quản lý xác nhận", unit="%"))
    return {k: pd.DataFrame(v, columns=SPECS[k].columns) for k, v in rows.items()}


def workbook_bytes(tables=None):
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for kind, df in (tables or demo_tables()).items():
            df.drop(columns=[c for c in df if c.startswith("_")]).to_excel(writer, sheet_name=kind, index=False)
    return output.getvalue()


def sample_workbook_bytes():
    source = Path(__file__).resolve().parents[1] / "data" / "DU_LIEU_DEMO.xlsx"
    return source.read_bytes() if source.is_file() else workbook_bytes()


def write_samples(folder="data"):
    dest = Path(folder)
    dest.mkdir(exist_ok=True)
    tables = demo_tables()
    (dest / "DU_LIEU_DEMO.xlsx").write_bytes(workbook_bytes(tables))
    for kind, df in tables.items():
        df.to_csv(dest / f"DEMO_{kind}.csv", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    write_samples()
