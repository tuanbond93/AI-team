from pathlib import Path
from decimal import Decimal
import hashlib
import json
import pytest
from ops_team.supra.legacy import read_legacy_month, source_validator
from ops_team.supra.ingestion import read_workbook
from ops_team.supra.service import SupraService
from ops_team.supra.store import EventStore
from ops_team.supra.domain import Principal

ROOT = Path.home()/"Desktop"/"Super Tracking chi phí Supra"
VIEWER = Principal("reader")
needs_source = pytest.mark.skipif(not (ROOT/"src/data/multiMonthSupraData.json").exists() or not (ROOT/"Supra_Phu_Tho_PnL_CostToServe_Pilot14Ngay.xlsx").exists(),
    reason="Cần nguồn Supra local của owner; repo sạch không chứa dữ liệu thật.")
ANALYST = Principal("Supra Account AI", permissions=frozenset({"VIEW_PNL", "ANALYZE"}))


@needs_source
def test_read_existing_snapshot_reuses_engine_and_scopes_only_phu_tho():
    path = ROOT/"src/data/multiMonthSupraData.json"
    original = path.read_bytes()
    snapshot = read_legacy_month(VIEWER, ROOT)
    assert snapshot["validator_reused"] == "supra_engine.validate_result"
    assert snapshot["verification"] == "UNVERIFIED" and snapshot["mode"] == "HISTORICAL_SOURCE_MODEL"
    assert not snapshot["source_complete"] and snapshot["source_weight_unit"] == "g"
    assert snapshot["summary"]["profit"] == snapshot["summary"]["revenue"]-snapshot["summary"]["cost"]
    assert all(r["province"] == "Phú Thọ" for r in snapshot["lineage"])
    assert "Sơn La" not in str(snapshot) and "Điện Biên" not in str(snapshot)
    assert sum(Decimal(str(r["revenue"])) for r in snapshot["lineage"]) == snapshot["summary"]["revenue"]
    assert path.read_bytes() == original


@needs_source
@pytest.mark.parametrize("month", ["2026-06", "2026-08", "2026-01"])
def test_old_unreconciled_snapshot_cannot_be_accepted(month):
    with pytest.raises(ValueError):
        read_legacy_month(VIEWER, ROOT, month)


def test_changed_engine_blocked_without_running_source(tmp_path):
    marker = tmp_path/"must-not-exist"
    source = tmp_path/"engine.py"
    source.write_text(f"open({str(marker)!r}, 'w').write('bad')", encoding="utf-8")
    with pytest.raises(ValueError, match="version"):
        source_validator(source)
    assert not marker.exists()


@needs_source
def test_legacy_snapshot_kept_separate_and_versioned():
    dataset = read_workbook((ROOT/"Supra_Phu_Tho_PnL_CostToServe_Pilot14Ngay.xlsx").read_bytes(), "plan.xlsx")
    service, store = SupraService(dataset), EventStore()
    plan = service.query_pnl(VIEWER)["summary"]["cost"]
    snapshot = read_legacy_month(VIEWER, ROOT)
    linked = service.attach_legacy(ANALYST, store, snapshot)
    assert linked.query_pnl(VIEWER)["summary"]["cost"] == plan
    assert linked.query_pnl(VIEWER, mode="ACTUAL")["summary"]["profit"] is None
    assert linked.attach_legacy(ANALYST, store, snapshot).dataset["version"] == linked.dataset["version"]
    restored = SupraService(store.load_snapshot(VIEWER, linked.dataset["version"]))
    assert restored.dataset["legacy_snapshot"]["verification"] == "UNVERIFIED"
    with pytest.raises(PermissionError):
        read_legacy_month(Principal("other", region="HCM"), ROOT)
