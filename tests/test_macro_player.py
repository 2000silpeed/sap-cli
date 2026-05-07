"""MacroPlayer 단위 테스트 - COM 없이 트리 액션 매핑 검증."""

from types import SimpleNamespace

from sap_cli.macro.player import MacroPlayer
from sap_cli.macro.schema import Macro, MacroStep
from sap_cli.ontology.schema import ActionType


class FakeSap:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def tree_select_node(self, element_id: str, node_key: str) -> None:
        self.calls.append(("tree_select_node", element_id, node_key))

    def tree_expand_node(self, element_id: str, node_key: str) -> None:
        self.calls.append(("tree_expand_node", element_id, node_key))

    def tree_double_click_node(self, element_id: str, node_key: str) -> None:
        self.calls.append(("tree_double_click_node", element_id, node_key))

    def tree_collapse_node(self, element_id: str, node_key: str) -> None:
        self.calls.append(("tree_collapse_node", element_id, node_key))

    def get_status(self):
        return SimpleNamespace(type=SimpleNamespace(value="S"), message="")

    def get_session_info(self):
        return SimpleNamespace(screen_number=100)


def _macro(*steps: MacroStep) -> Macro:
    return Macro(
        id="m1",
        name="tree macro",
        steps=list(steps),
    )


def test_macro_player_executes_tree_actions():
    sap = FakeSap()
    player = MacroPlayer(sap)  # type: ignore[arg-type]
    tree_id = "wnd[0]/usr/cntlTREE/shellcont/shell"

    result = player.play(
        _macro(
            MacroStep(
                step_number=1,
                action=ActionType.SELECT_TREE_NODE,
                target_id=tree_id,
                value="0001",
            ),
            MacroStep(
                step_number=2,
                action=ActionType.EXPAND_TREE_NODE,
                target_id=tree_id,
                value="0002",
            ),
            MacroStep(
                step_number=3,
                action=ActionType.DOUBLE_CLICK_TREE_NODE,
                target_id=tree_id,
                value="0003",
            ),
            MacroStep(
                step_number=4,
                action=ActionType.COLLAPSE_TREE_NODE,
                target_id=tree_id,
                value="0004",
            ),
        )
    )

    assert result.success is True
    assert sap.calls == [
        ("tree_select_node", tree_id, "0001"),
        ("tree_expand_node", tree_id, "0002"),
        ("tree_double_click_node", tree_id, "0003"),
        ("tree_collapse_node", tree_id, "0004"),
    ]


def test_macro_player_tree_action_requires_node_key():
    sap = FakeSap()
    player = MacroPlayer(sap)  # type: ignore[arg-type]

    result = player.play(
        _macro(
            MacroStep(
                step_number=1,
                action=ActionType.SELECT_TREE_NODE,
                target_id="wnd[0]/usr/tree",
                value="",
            )
        )
    )

    assert result.success is False
    assert result.error_step == 1
    assert "node_key" in (result.step_results[0].error or "")
