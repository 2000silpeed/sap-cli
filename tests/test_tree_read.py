from __future__ import annotations

from types import SimpleNamespace

from sap_cli.com_wrapper import SapGuiWrapper


class Collection:
    def __init__(self, values):
        self.values = values
        self.Count = len(values)

    def ElementAt(self, index):
        return self.values[index]


class PartialTree:
    Type = "GuiTree"
    SubType = "Tree"

    def GetAllNodeKeys(self):
        return Collection(["root", "a", "b"])

    def GetColumnNames(self):
        return Collection([])

    def GetColumnHeaders(self):
        return Collection([])

    def GetNodeTextByKey(self, key):
        return key.upper()

    def GetNodeItemHeaders(self, key):
        return Collection(["VALUE"])

    def GetItemText(self, key, column):
        return f"{key}:{column}"

    def GetHierarchyLevel(self, key):
        return {"root": 0, "a": 1, "b": 1}[key]

    def GetSubNodesCol(self, key):
        if key == "root":
            return Collection(["a"])
        raise RuntimeError("partial API failure")

    def GetParent(self, key):
        return "root" if key in {"a", "b"} else ""


def test_read_tree_uses_official_hierarchy_and_repairs_partial_children() -> None:
    wrapper = SapGuiWrapper()
    tree = PartialTree()
    wrapper._session = SimpleNamespace(
        Info=SimpleNamespace(User="TEST"),
        FindById=lambda element_id: tree,
    )

    roots = wrapper.read_tree("wnd[0]/usr/tree")

    assert [node.key for node in roots] == ["root"]
    assert [node.key for node in roots[0].children] == ["a", "b"]
    assert roots[0].children[0].level == 1
    assert roots[0].children[0].items == {"VALUE": "a:VALUE"}
