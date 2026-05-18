"""Screen search helper tests."""

from __future__ import annotations

from sap_cli.dispatchers.connection import search_screen
from sap_cli.ontology.schema import SapTable, SapTableColumn, GuiComponentType


class _Screen:
    transaction = "SE16N"
    screen_number = 100
    fields = []
    buttons = []
    tabs = []

    def __init__(self):
        self.tables = [
            SapTable(
                id="wnd[0]/usr/cntlRESULT/shellcont/shell",
                name="",
                type=GuiComponentType.SHELL,
                sub_type="GridView",
                columns=[
                    SapTableColumn(name="FIELDNAME", title="Field Name", position=0),
                    SapTableColumn(name="SCRTEXT_L", title="Field Label", position=1),
                    SapTableColumn(name="MATNR", title="Material Number", position=2),
                ],
            )
        ]


def test_search_screen_matches_table_columns():
    result = search_screen(_Screen(), "material")

    assert result["total_matches"] == 1
    assert result["tables"][0]["id"] == "wnd[0]/usr/cntlRESULT/shellcont/shell"
    assert result["tables"][0]["matching_columns"] == [
        {"name": "MATNR", "title": "Material Number", "position": 2}
    ]
