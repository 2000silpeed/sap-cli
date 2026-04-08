"""_scan_children 복잡 화면 탐색 테스트.

VL03N 같은 복잡 트랜잭션에서 발생하는 실제 사례를 재현:
- Children.Count 와 실제 접근 가능한 자식 수 불일치 (index out of range)
- 다양한 컴포넌트 타입 혼재 (필드, 버튼, 테이블, 탭, 컨테이너)
- 중첩 컨테이너 재귀 탐색
- 개별 요소 COM 속성 접근 실패
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from sap_cli.com_wrapper import SapGuiWrapper
from sap_cli.ontology.schema import ScreenOntology


# ---------------------------------------------------------------------------
# Fake COM helpers
# ---------------------------------------------------------------------------

class FakeChildren:
    """SAP GUI Children 컬렉션 모의 객체.

    bad_indices: 접근 시 IndexError를 발생시킬 인덱스 집합.
    SAP GUI COM에서 Count와 실제 접근 가능한 인덱스가 불일치하는 상황을 재현.
    """

    def __init__(self, items: list, *, bad_indices: set[int] | None = None):
        self._items = items
        self._bad_indices = bad_indices or set()
        self.Count = len(items) + len(self._bad_indices)

    def __call__(self, index: int):
        if index in self._bad_indices:
            raise IndexError(f"index out of range: {index}")
        # bad_indices 이전의 실제 아이템 위치 계산
        real_idx = index - sum(1 for b in self._bad_indices if b < index)
        if real_idx < 0 or real_idx >= len(self._items):
            raise IndexError(f"index out of range: {index}")
        return self._items[real_idx]


def _make_element(
    element_id: str,
    type_str: str,
    *,
    text: str = "",
    tooltip: str = "",
    changeable: bool = False,
    children: FakeChildren | None = None,
    icon_name: str = "",
    value: str = "",
    max_length: int = 0,
    row_count: int = 0,
    column_count: int = 0,
    visible_row_count: int = 0,
):
    """SAP GUI COM 요소 모의 객체 생성."""
    el = SimpleNamespace(
        Id=element_id,
        Type=type_str,
        Text=text,
        Tooltip=tooltip,
        Changeable=changeable,
        IconName=icon_name,
        Value=value,
        MaxLength=max_length,
        RowCount=row_count,
        ColumnCount=column_count,
        VisibleRowCount=visible_row_count,
    )
    if children is not None:
        el.Children = children
    return el


def _make_container(element_id: str, children_items: list, **kwargs):
    """컨테이너 요소 생성 (GuiSimpleContainer 등)."""
    return _make_element(
        element_id,
        "GuiSimpleContainer",
        children=FakeChildren(children_items),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def wrapper():
    """COM 연결 없이 SapGuiWrapper 인스턴스를 생성."""
    with patch.object(SapGuiWrapper, "__init__", lambda self: None):
        w = SapGuiWrapper()
        w._session = MagicMock()
        return w


def _empty_screen() -> ScreenOntology:
    return ScreenOntology(
        transaction="VL03N",
        screen_number=1000,
        program="SAPMV50A",
        title="Display Outbound Delivery",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestScanChildrenIndexOutOfRange:
    """Children.Count와 실제 접근 가능 인덱스 불일치 시나리오."""

    def test_partial_index_failure_still_collects_valid_elements(self, wrapper):
        """5개 중 2개가 index out of range → 나머지 3개는 정상 수집."""
        field1 = _make_element("wnd[0]/usr/txtVBELN", "GuiTextField", text="80000123")
        field2 = _make_element("wnd[0]/usr/txtPOSNR", "GuiTextField", text="10")
        btn = _make_element("wnd[0]/usr/btnEXEC", "GuiButton", text="실행", tooltip="Execute")

        # index 1, 3 접근 시 IndexError 발생
        container = _make_element(
            "wnd[0]/usr", "GuiUserArea",
            children=FakeChildren([field1, field2, btn], bad_indices={1, 3}),
        )

        screen = _empty_screen()
        wrapper._scan_children(container, screen)

        assert len(screen.fields) == 2  # txtVBELN, txtPOSNR
        assert len(screen.buttons) == 1  # btnEXEC
        assert screen.fields[0].id == "wnd[0]/usr/txtVBELN"
        assert screen.buttons[0].text == "실행"

    def test_all_indices_fail_returns_empty_screen(self, wrapper):
        """모든 인덱스 접근 실패 → 에러 없이 빈 결과."""
        container = _make_element(
            "wnd[0]/usr", "GuiUserArea",
            children=FakeChildren([], bad_indices={0, 1, 2}),
        )

        screen = _empty_screen()
        wrapper._scan_children(container, screen)

        assert len(screen.fields) == 0
        assert len(screen.buttons) == 0

    def test_children_property_access_failure(self, wrapper):
        """container.Children 자체 접근이 실패하는 경우."""
        container = MagicMock()
        type(container).Children = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("COM disconnected"))
        )

        screen = _empty_screen()
        # 에러 없이 빈 결과 반환
        wrapper._scan_children(container, screen)
        assert len(screen.fields) == 0


class TestScanChildrenComplexScreen:
    """VL03N 같은 복잡 화면 시나리오 - 다양한 컴포넌트 혼재."""

    def _build_vl03n_like_tree(self):
        """VL03N 납품 조회 화면과 유사한 구조.

        wnd[0]/usr
        ├── txtVBELN-VL    (납품번호 - TextField)
        ├── [index 2: bad]  (접근 불가)
        ├── cmbLFART       (납품유형 - ComboBox)
        ├── chkSELECT      (선택 - CheckBox)
        ├── sub:CONTAINER   (서브컨테이너)
        │   ├── txtWADAT    (출고일 - TextField)
        │   └── btnDETAIL  (상세 - Button)
        ├── [index 7: bad]  (접근 불가)
        ├── tabsTABSTRIP    (탭스트립 - TabStrip)
        │   └── tabpITEM   (품목 탭 - Tab)
        │       └── txtMATNR (자재번호 - TextField)
        └── shellGRID       (GridView - 테이블)
        """
        # 탭 내부 필드
        tab_field = _make_element("wnd[0]/usr/tabsTABSTRIP/tabpITEM/txtMATNR",
                                  "GuiTextField", text="MAT-001")
        tab = _make_element("wnd[0]/usr/tabsTABSTRIP/tabpITEM", "GuiTab",
                            text="품목",
                            children=FakeChildren([tab_field]))

        tabstrip = _make_element("wnd[0]/usr/tabsTABSTRIP", "GuiTabStrip",
                                 children=FakeChildren([tab]))

        # 서브컨테이너 내부
        sub_field = _make_element("wnd[0]/usr/sub/txtWADAT", "GuiTextField",
                                  text="2026-04-08", changeable=True)
        sub_btn = _make_element("wnd[0]/usr/sub/btnDETAIL", "GuiButton",
                                text="상세", tooltip="Show detail")
        sub_container = _make_container("wnd[0]/usr/sub", [sub_field, sub_btn])

        # GridView (테이블)
        grid = _make_element("wnd[0]/usr/shellGRID", "GuiGridView",
                             row_count=15, column_count=0, visible_row_count=10)

        # 최상위 요소들
        fld_vbeln = _make_element("wnd[0]/usr/txtVBELN-VL", "GuiTextField",
                                  text="80000123", changeable=False)
        cmb_lfart = _make_element("wnd[0]/usr/cmbLFART", "GuiComboBox",
                                  value="LF", changeable=True)
        chk_select = _make_element("wnd[0]/usr/chkSELECT", "GuiCheckBox",
                                   text="선택", changeable=True)

        # bad_indices={2, 7}: Count=10이지만 index 2, 7은 접근 불가
        usr_children = FakeChildren(
            [fld_vbeln, cmb_lfart, chk_select, sub_container, tabstrip, grid,
             # Label은 _SKIP_TYPES에 포함 → 건너뜀
             _make_element("wnd[0]/usr/lblINFO", "GuiLabel", text="Info")],
            bad_indices={2, 7},
        )
        return _make_element("wnd[0]/usr", "GuiUserArea", children=usr_children)

    def test_complex_screen_with_partial_failures(self, wrapper):
        """복잡 화면에서 일부 인덱스 실패해도 나머지 요소 모두 수집."""
        container = self._build_vl03n_like_tree()
        screen = _empty_screen()
        wrapper._scan_children(container, screen)

        # 필드: VBELN-VL, LFART(combo), SELECT(check), WADAT(sub 내부), MATNR(탭 내부)
        field_ids = {f.id for f in screen.fields}
        assert "wnd[0]/usr/txtVBELN-VL" in field_ids
        assert "wnd[0]/usr/cmbLFART" in field_ids
        assert "wnd[0]/usr/chkSELECT" in field_ids
        assert "wnd[0]/usr/sub/txtWADAT" in field_ids
        assert "wnd[0]/usr/tabsTABSTRIP/tabpITEM/txtMATNR" in field_ids

        # 버튼: sub 내부 btnDETAIL
        btn_ids = {b.id for b in screen.buttons}
        assert "wnd[0]/usr/sub/btnDETAIL" in btn_ids

        # 탭: tabpITEM
        tab_ids = {t.id for t in screen.tabs}
        assert "wnd[0]/usr/tabsTABSTRIP/tabpITEM" in tab_ids

        # 테이블: shellGRID
        table_ids = {t.id for t in screen.tables}
        assert "wnd[0]/usr/shellGRID" in table_ids
        assert screen.tables[0].row_count == 15

    def test_label_is_skipped(self, wrapper):
        """GuiLabel은 _SKIP_TYPES에 포함 → 필드로 수집되지 않아야 함."""
        container = self._build_vl03n_like_tree()
        screen = _empty_screen()
        wrapper._scan_children(container, screen)

        field_ids = {f.id for f in screen.fields}
        assert "wnd[0]/usr/lblINFO" not in field_ids


class TestScanChildrenElementProcessingError:
    """개별 요소의 속성(Id, Type 등) 접근 실패 시나리오."""

    def test_type_access_error_skips_element(self, wrapper):
        """child.Type 접근 에러 → 해당 요소만 건너뛰고 계속."""
        good_field = _make_element("wnd[0]/usr/txtOK", "GuiTextField", text="OK")

        bad_element = MagicMock()
        type(bad_element).Type = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("Type access failed"))
        )

        container = _make_element(
            "wnd[0]/usr", "GuiUserArea",
            children=FakeChildren([bad_element, good_field]),
        )

        screen = _empty_screen()
        wrapper._scan_children(container, screen)

        assert len(screen.fields) == 1
        assert screen.fields[0].id == "wnd[0]/usr/txtOK"

    def test_id_access_error_skips_element(self, wrapper):
        """child.Id 접근 에러 → 해당 요소만 건너뛰고 계속."""
        good_btn = _make_element("wnd[0]/usr/btnSAVE", "GuiButton", text="Save")

        bad_element = SimpleNamespace(Type="GuiTextField")
        # Id 속성에 접근하면 에러 (property로 못 만드므로 __getattr__ 트릭)
        class BadIdElement:
            Type = "GuiTextField"
            @property
            def Id(self):
                raise RuntimeError("Id access failed")

        container = _make_element(
            "wnd[0]/usr", "GuiUserArea",
            children=FakeChildren([BadIdElement(), good_btn]),
        )

        screen = _empty_screen()
        wrapper._scan_children(container, screen)

        assert len(screen.buttons) == 1
        assert screen.buttons[0].text == "Save"


class TestScanChildrenDepthLimit:
    """재귀 깊이 제한 테스트."""

    def test_depth_exceeds_limit_stops_recursion(self, wrapper):
        """depth > 8 → 탐색 중단."""
        field = _make_element("wnd[0]/deep/txtFIELD", "GuiTextField", text="deep")
        container = _make_element(
            "wnd[0]/deep", "GuiSimpleContainer",
            children=FakeChildren([field]),
        )

        screen = _empty_screen()
        wrapper._scan_children(container, screen, depth=9)

        assert len(screen.fields) == 0  # 깊이 초과로 탐색 안 됨

    def test_depth_at_limit_still_scans(self, wrapper):
        """depth == 8 → 마지막 레벨은 탐색."""
        field = _make_element("wnd[0]/deep/txtFIELD", "GuiTextField", text="deep")
        container = _make_element(
            "wnd[0]/deep", "GuiSimpleContainer",
            children=FakeChildren([field]),
        )

        screen = _empty_screen()
        wrapper._scan_children(container, screen, depth=8)

        assert len(screen.fields) == 1


class TestUserTcodeFallback:
    """SAP가 내부 tcode(SE38 등)를 반환할 때 원래 tcode로 보정하는 테스트.

    VL03N → DO 상세 화면에서 Info.Transaction이 SE38로 바뀌는 실제 사례 재현.
    """

    def test_run_transaction_stores_user_tcode(self, wrapper):
        """run_transaction 호출 시 _user_tcode에 원래 tcode가 저장된다."""
        wrapper._user_tcode = None
        wrapper.run_transaction("VL03N")
        assert wrapper._user_tcode == "VL03N"

    def test_run_transaction_with_slash_prefix(self, wrapper):
        """/nVL03N 형식도 tcode를 올바르게 저장."""
        wrapper._user_tcode = None
        wrapper.run_transaction("/nVL03N")
        assert wrapper._user_tcode == "VL03N"

    def test_internal_tcode_not_stored(self, wrapper):
        """SE38 같은 내부 tcode는 _user_tcode를 덮어쓰지 않는다."""
        wrapper._user_tcode = "VL03N"
        wrapper.run_transaction("SE38")
        assert wrapper._user_tcode == "VL03N"  # 변경되지 않음

    def test_get_screen_uses_user_tcode_when_sap_reports_internal(self, wrapper):
        """Info.Transaction=SE38일 때 _user_tcode(VL03N)를 사용."""
        wrapper._user_tcode = "VL03N"
        wrapper.session.Info.Transaction = "SE38"
        wrapper.session.Info.ScreenNumber = 200
        wrapper.session.Info.Program = "SAPMV50A"
        wrapper.session.FindById.return_value = SimpleNamespace(
            Text="Display Outbound Delivery",
            Type="GuiMainWindow",
            Id="wnd[0]",
            Children=FakeChildren([]),
        )

        screen = wrapper.get_screen()
        assert screen.transaction == "VL03N"

    def test_get_screen_keeps_real_tcode_when_not_internal(self, wrapper):
        """Info.Transaction=MM03처럼 정상 tcode면 그대로 사용."""
        wrapper._user_tcode = "VL03N"
        wrapper.session.Info.Transaction = "MM03"
        wrapper.session.Info.ScreenNumber = 100
        wrapper.session.Info.Program = "SAPLMGMM"
        wrapper.session.FindById.return_value = SimpleNamespace(
            Text="Display Material",
            Type="GuiMainWindow",
            Id="wnd[0]",
            Children=FakeChildren([]),
        )

        screen = wrapper.get_screen()
        assert screen.transaction == "MM03"  # VL03N이 아니라 MM03

    def test_get_screen_without_user_tcode_uses_reported(self, wrapper):
        """_user_tcode가 없으면 SAP가 보고하는 값을 그대로 사용."""
        wrapper._user_tcode = None
        wrapper.session.Info.Transaction = "SE38"
        wrapper.session.Info.ScreenNumber = 1000
        wrapper.session.Info.Program = "SAPMSYST"
        wrapper.session.FindById.return_value = SimpleNamespace(
            Text="ABAP Editor",
            Type="GuiMainWindow",
            Id="wnd[0]",
            Children=FakeChildren([]),
        )

        screen = wrapper.get_screen()
        assert screen.transaction == "SE38"  # fallback 없으므로 그대로
