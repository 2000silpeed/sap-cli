from __future__ import annotations

from sap_cli.scripting_coverage import default_coverage_report
from sap_cli.scripting_live_coverage import (
    load_live_tested_members,
    record_live_member,
)


def test_live_coverage_evidence_is_value_free_deduplicated_and_reported(
    tmp_path, monkeypatch
):
    target = tmp_path / "live.json"
    monkeypatch.setenv("SAP_CLI_API_LIVE_COVERAGE_FILE", str(target))
    member_id = "method:GuiGridView.GetCellValue"

    record_live_member(member_id, api_version="8.00 PL08")
    record_live_member(member_id, api_version="8.00 PL08")

    assert load_live_tested_members() == {member_id: ["8.00 PL08"]}
    report = default_coverage_report(include_gaps=False)
    assert report["gap_counts"]["live_untested"] == report["counts"]["total"] - 1
    assert target.stat().st_mode & 0o777 == 0o600


def test_live_coverage_rejects_unstructured_or_business_member_identifiers(tmp_path):
    target = tmp_path / "live.json"
    for member_id in ("method:GuiGridView.Get Cell", "secret:MATNR", "method:GuiGridView.GetCellValue:123"):
        try:
            record_live_member(member_id, api_version="8.00 PL08", path=target)
        except ValueError:
            continue
        raise AssertionError("unsafe member identifier was accepted")


def test_live_coverage_rejects_a_well_formed_member_not_in_official_graph(tmp_path):
    try:
        record_live_member(
            "method:GuiGridView.NotInOfficialGraph", api_version="8.00 PL08", path=tmp_path / "live.json"
        )
    except ValueError:
        return
    raise AssertionError("unknown graph member was accepted")
