"""Read-only, lossless SAP GUI session scanner.

The scanner deliberately works against the small COM protocol exposed by SAP
GUI Scripting instead of depending on pywin32 types.  This keeps it usable for
real sessions and deterministic fake-session tests alike.  No UI action method
is called: only collection indexing and documented read/query methods are used.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from sap_cli.scripting_ontology import component_capability

from .schema import (
    CollectionColumn,
    CollectionCell,
    CollectionDescriptor,
    CollectionNode,
    UiComponent,
    UiState,
    WindowDescriptor,
    modal_window_signature,
    normalize_structural_text,
)


_MISSING = object()
_WINDOW_TYPES = {"GuiFrameWindow", "GuiMainWindow", "GuiModalWindow"}
_SENSITIVE_TYPES = {"GuiPasswordField"}
_VALUE_TEXT_TYPES = {
    "GuiTextField",
    "GuiCTextField",
    "GuiOkCodeField",
    "GuiPasswordField",
    "GuiTextedit",
    "GuiTextEdit",
    "GuiComboBox",
}
_TEXT_EDITOR_TYPES = {"GuiTextedit", "GuiTextEdit", "GuiAbapEditor"}
_SENSITIVE_NAME = re.compile(
    r"(?:PASSW|PASSWORD|PWD|PIN|SECRET|TOKEN|CREDENTIAL|BAPIKEY|APIKEY)", re.I
)


@dataclass(slots=True)
class ScanMetrics:
    """COM boundary measurements for one scan."""

    attribute_reads: int = 0
    collection_calls: int = 0
    query_calls: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)
    elapsed_ms: float = 0.0
    components: int = 0
    collections: int = 0

    @property
    def com_calls(self) -> int:
        return self.attribute_reads + self.collection_calls + self.query_calls

    def as_dict(self) -> dict[str, Any]:
        return {
            "attribute_reads": self.attribute_reads,
            "collection_calls": self.collection_calls,
            "query_calls": self.query_calls,
            "com_calls": self.com_calls,
            "error_count": len(self.errors),
            "errors": list(self.errors),
            "elapsed_ms": self.elapsed_ms,
            "components": self.components,
            "collections": self.collections,
        }


class UiMapScanner:
    """Capture every reachable window/component without changing SAP state."""

    def __init__(self, *, clock: Callable[[], float] = time.perf_counter) -> None:
        self._clock = clock
        self.metrics = ScanMetrics()
        self._components: list[UiComponent] = []
        self._collections: list[CollectionDescriptor] = []
        self._logical_ids: set[str] = set()
        self._object_ids: set[int] = set()
        self._active_tabs: dict[str, str] = {}
        self._selected_tab_sap_ids: set[str] = set()

    def scan(self, session: Any, *, tcode: str = "") -> UiState:
        """Return a complete passive snapshot of ``session``.

        Individual COM failures are recorded and skipped.  A bad Children index
        therefore cannot hide later valid siblings.
        """

        started = self._clock()
        self.metrics = ScanMetrics()
        self._components = []
        self._collections = []
        self._logical_ids = set()
        self._object_ids = set()
        self._active_tabs = {}
        self._selected_tab_sap_ids = set()

        info = self._read(session, "Info", None, "session")
        # A caller hint only fills an unavailable session field.  Treating it
        # as authoritative would silently contaminate a TCode map after a
        # button/menu navigated into another transaction.
        live_transaction = self._text(self._read(info, "Transaction", "", "Info"))
        transaction = live_transaction or tcode
        program = self._text(self._read(info, "Program", "", "Info"))
        screen_number = self._integer(self._read(info, "ScreenNumber", 0, "Info"), 0)

        active = self._read(session, "ActiveWindow", None, "session")
        active_id = self._text(self._read(active, "Id", "", "ActiveWindow"))
        windows = self._session_windows(session)
        stack: list[WindowDescriptor] = []
        for index, window in enumerate(windows):
            component = self._visit(window, parent="", path=f"window[{index}]")
            if component is None:
                continue
            modal = component.type == "GuiModalWindow" or bool(
                component.static_properties.get("modal", False)
            )
            stack.append(
                WindowDescriptor(
                    sap_id=component.sap_id,
                    type=component.type,
                    title=component.text,
                    modal=modal,
                    active=component.sap_id == active_id,
                    signature=modal_window_signature(
                        component.type, component.sap_id, component.text
                    ),
                )
            )

        active_title = self._text(self._read(active, "Text", "", "ActiveWindow"))
        title = active_title or (stack[-1].title if stack else "")
        focused = ""
        for component in self._components:
            if component.static_properties.get("focused"):
                focused = component.logical_id
                break

        modal_windows = [window for window in stack if window.modal]
        modal_signature = "|".join(window.signature for window in modal_windows)
        self.metrics.components = len(self._components)
        self.metrics.collections = len(self._collections)
        self.metrics.elapsed_ms = max(0.0, (self._clock() - started) * 1000.0)

        statusbar = next(
            (item for item in self._components if item.type == "GuiStatusbar"), None
        )
        status_metadata = (
            {
                key: value
                for key, value in statusbar.static_properties.items()
                if key in {"message_type", "message_id", "message_number"}
            }
            if statusbar is not None
            else {}
        )
        state = UiState(
            tcode=transaction,
            program=program,
            screen_number=screen_number,
            title=title,
            active_tabs=dict(self._active_tabs),
            active_subscreens=[
                component.logical_id
                for component in self._components
                if component.type in {"GuiSimpleContainer", "GuiCustomControl"}
                and component.visible is not False
            ],
            focused_component=focused,
            window_stack=stack,
            modal_signature=modal_signature,
            components=self._components,
            collections=self._collections,
            normalized_status={
                "active_window": active_id,
                **({"statusbar": status_metadata} if status_metadata else {}),
                "scan_metrics": self.metrics.as_dict(),
            },
            captured_at=datetime.now(timezone.utc).isoformat(),
        )
        return state.ensure_state_id()

    def _session_windows(self, session: Any) -> list[Any]:
        windows: list[Any] = []
        children = self._read(session, "Children", None, "session")
        windows.extend(self._collection_items(children, "session.Children"))
        active = self._read(session, "ActiveWindow", None, "session")
        if active is not None and all(item is not active for item in windows):
            windows.append(active)
        return windows

    @staticmethod
    def _text_signature(value: str) -> str:
        """Differentiate window/modal meanings without echoing their text."""

        normalized = normalize_structural_text(value)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

    def _visit(self, obj: Any, *, parent: str, path: str) -> UiComponent | None:
        if obj is None or id(obj) in self._object_ids:
            return None
        self._object_ids.add(id(obj))

        sap_id = self._text(self._read(obj, "Id", "", path)) or path
        component_type = self._text(self._read(obj, "Type", "Unknown", sap_id)) or "Unknown"
        subtype = self._text(self._read(obj, "SubType", "", sap_id))
        logical_id = self._unique_logical_id(sap_id)
        name = self._text(self._read(obj, "Name", "", sap_id)) or self._name_from_id(sap_id)
        # Rich editors expose their body through line APIs rather than a
        # reliable Text property.  A UI map stores structure only, never the
        # body, so avoid an unnecessary/error-prone COM read here.
        text = (
            ""
            if component_type in _TEXT_EDITOR_TYPES
            else self._text(self._read(obj, "Text", "", sap_id))
        )
        tooltip = self._text(self._read(obj, "Tooltip", "", sap_id))
        icon = self._text(self._read(obj, "IconName", "", sap_id))
        sensitive = component_type in _SENSITIVE_TYPES or bool(_SENSITIVE_NAME.search(name or sap_id))
        value_text = component_type in _VALUE_TEXT_TYPES or component_type == "GuiStatusbar"

        static: dict[str, Any] = {}
        for source, target in (
            ("IsModal", "modal"),
            ("IsContainer", "container"),
            ("HasFocus", "focused"),
        ):
            value = self._read(obj, source, _MISSING, sap_id, report_missing=False)
            if value is not _MISSING:
                static[target] = self._scalar(value)

        volatile: dict[str, Any] = {}
        if text and value_text:
            volatile["text"] = "<redacted>"
        for attr, key in (
            ("Key", "key"),
            ("Value", "value"),
            ("CaretPosition", "caret_position"),
        ):
            value = self._read(obj, attr, _MISSING, sap_id, report_missing=False)
            if value is not _MISSING:
                volatile[key] = (
                    "<redacted>"
                    if (sensitive or value_text) and key in {"key", "value"}
                    else self._scalar(value)
                )

        if component_type in _TEXT_EDITOR_TYPES:
            editor_meta = self._text_editor_metadata(obj, sap_id)
            if editor_meta:
                # Keep live document size out of structural fingerprints and
                # persisted search text.  Content is available only through
                # an explicit bounded read of the live editor.
                volatile["text_editor"] = editor_meta

        if component_type == "GuiComboBox":
            entries = self._combo_entries(obj, sap_id, sensitive)
            static["entries"] = entries
            static["entries_count"] = len(entries)

        if component_type == "GuiStatusbar":
            for source, target in (
                ("MessageType", "message_type"),
                ("MessageId", "message_id"),
                ("MessageNumber", "message_number"),
            ):
                value = self._read(obj, source, _MISSING, sap_id, report_missing=False)
                if value is not _MISSING:
                    static[target] = self._scalar(value)

        bounds: dict[str, int] = {}
        for attr, key in (
            ("ScreenLeft", "left"),
            ("ScreenTop", "top"),
            ("Width", "width"),
            ("Height", "height"),
        ):
            value = self._read(obj, attr, _MISSING, sap_id, report_missing=False)
            if value is not _MISSING:
                bounds[key] = self._integer(value, 0)

        if component_type == "GuiTab":
            # GuiTab exposes no Selected property; its active state comes from
            # the parent GuiTabStrip.SelectedTab, recorded when the strip was
            # visited just above in the tree walk.
            selected_flag: bool | None = sap_id in self._selected_tab_sap_ids
        else:
            selected_flag = self._optional_bool(obj, "Selected", sap_id)

        capability = component_capability(component_type, subtype)
        actions = sorted({str(item) for item in capability.get("actions", [])})
        component = UiComponent(
            logical_id=logical_id,
            sap_id=sap_id,
            type=component_type,
            sub_type=subtype,
            role=self._role(component_type, subtype),
            parent_logical_id=parent,
            alternate_locators=self._alternate_locators(
                sap_id, component_type, subtype, name, text, parent
            ),
            name=name,
            text="<redacted>" if (sensitive or value_text) and text else text,
            tooltip=tooltip,
            icon_name=icon,
            visible=self._optional_bool(obj, "Visible", sap_id),
            enabled=self._optional_bool(obj, "Enabled", sap_id),
            changeable=self._optional_bool(obj, "Changeable", sap_id),
            required=self._optional_bool(obj, "Required", sap_id),
            selected=selected_flag,
            bounds=bounds,
            static_properties=static,
            volatile_properties=volatile,
            supported_actions=actions,
            sensitive=sensitive,
            provenance={"source": "sap_gui_scripting", "path": path, "read_only": True},
        )
        self._components.append(component)

        if component_type == "GuiTabStrip":
            # Must run before the child GuiTabs are visited so each tab can be
            # scanned with the correct selected state.
            self._record_selected_tab(obj, sap_id)

        descriptor = self._collection_descriptor(obj, component)
        if descriptor is not None:
            self._collections.append(descriptor)

        children = self._read(obj, "Children", None, sap_id, report_missing=False)
        for index, child in enumerate(self._collection_items(children, f"{sap_id}.Children")):
            child_component = self._visit(
                child, parent=logical_id, path=f"{path}.Children[{index}]"
            )
            if child_component is not None:
                component.children.append(child_component.logical_id)

        if component_type == "GuiTab" and component.selected:
            self._active_tabs[parent or sap_id] = logical_id
        return component

    def _record_selected_tab(self, strip: Any, context: str) -> None:
        """Note which GuiTab is the active one for this GuiTabStrip.

        GuiTab has no Selected property, so the only reliable signal is the
        strip's SelectedTab. Without it the active tab is unknown, so guided
        traversal finds no reversible sibling to restore to and offers no tab
        candidate — the strip's tabs never get mapped and the job ends early.
        """
        selected = self._read(strip, "SelectedTab", None, context, report_missing=False)
        if not selected or selected is _MISSING:
            return
        selected_id = self._text(self._read(selected, "Id", "", context))
        if selected_id:
            self._selected_tab_sap_ids.add(selected_id)

    def _text_editor_metadata(self, obj: Any, context: str) -> dict[str, int]:
        """Read small, value-free editor metadata for map diagnostics."""

        metadata: dict[str, int] = {}
        for attr, key in (
            ("LineCount", "line_count"),
            ("NumberOfUnprotectedTextParts", "unprotected_part_count"),
        ):
            value = self._read(obj, attr, _MISSING, context, report_missing=False)
            if value is _MISSING:
                continue
            try:
                metadata[key] = max(0, int(value))
            except (TypeError, ValueError):
                continue
        if "line_count" not in metadata:
            value = self._query(obj, "GetLineCount", default=_MISSING, context=context)
            if value is not _MISSING:
                try:
                    metadata["line_count"] = max(0, int(value))
                except (TypeError, ValueError):
                    pass
        return metadata

    def _combo_entries(
        self, obj: Any, context: str, sensitive: bool
    ) -> list[dict[str, Any]]:
        """Capture every stable ComboBox entry without selecting one."""

        entries = self._read(obj, "Entries", None, context, report_missing=False)
        values: list[dict[str, Any]] = []
        for index, entry in enumerate(self._collection_items(entries, f"{context}.Entries")):
            payload: dict[str, Any] = {"position": index}
            for attr, key in (("Key", "key"), ("Value", "value"), ("Pos", "position")):
                value = self._read(entry, attr, _MISSING, f"{context}.Entries[{index}]", report_missing=False)
                if value is _MISSING:
                    continue
                payload[key] = (
                    "<redacted>"
                    if sensitive and key in {"key", "value"}
                    else self._scalar(value)
                )
            values.append(payload)
        return values

    def _collection_descriptor(
        self, obj: Any, component: UiComponent
    ) -> CollectionDescriptor | None:
        ctype, subtype = component.type, component.sub_type.lower()
        if ctype == "GuiGridView" or (ctype == "GuiShell" and "grid" in subtype):
            return self._grid_descriptor(obj, component.logical_id)
        if ctype == "GuiApoGrid":
            return self._apo_grid_descriptor(obj, component.logical_id)
        if ctype == "GuiTableControl":
            return self._table_descriptor(obj, component.logical_id)
        if ctype == "GuiTree" or (ctype == "GuiShell" and "tree" in subtype):
            return self._tree_descriptor(obj, component.logical_id)
        if ctype == "GuiCalendar":
            return self._calendar_descriptor(obj, component.logical_id)
        if ctype == "GuiShell":
            return CollectionDescriptor(
                component_logical_id=component.logical_id,
                kind="other",
                volatile_properties={"sub_type": component.sub_type, "preserved_raw": True},
            )
        return None

    def _grid_descriptor(self, obj: Any, logical_id: str) -> CollectionDescriptor:
        count = self._integer(self._read(obj, "ColumnCount", 0, logical_id), 0)
        columns: list[CollectionColumn] = []
        for index in range(max(0, count)):
            column_id = self._text(
                self._query(obj, "ColumnOrder", index, default=str(index), context=logical_id)
            )
            titles = self._query(
                obj, "GetColumnTitles", column_id, default=[], context=logical_id
            )
            title_values = self._sequence(titles, f"{logical_id}.{column_id}.titles")
            title = self._text(title_values[0]) if title_values else column_id
            data_type = self._text(
                self._query(
                    obj, "GetColumnDataType", column_id, default="", context=logical_id
                )
            )
            position = self._integer(
                self._query(
                    obj, "GetColumnPosition", column_id, default=index, context=logical_id
                ),
                index,
            )
            columns.append(
                CollectionColumn(
                    id=column_id,
                    title=title,
                    type=data_type,
                    position=position,
                    visible=True,
                )
            )

        toolbar: list[dict[str, Any]] = []
        toolbar_count = self._integer(
            self._read(obj, "ToolbarButtonCount", 0, logical_id), 0
        )
        for index in range(max(0, toolbar_count)):
            entry: dict[str, Any] = {"position": index}
            for method, key, default in (
                ("GetToolbarButtonId", "id", ""),
                ("GetToolbarButtonText", "text", ""),
                ("GetToolbarButtonTooltip", "tooltip", ""),
                ("GetToolbarButtonIcon", "icon", ""),
                ("GetToolbarButtonType", "type", ""),
                ("GetToolbarButtonEnabled", "enabled", None),
                ("GetToolbarButtonChecked", "checked", None),
            ):
                value = self._query(obj, method, index, default=default, context=logical_id)
                if value not in (None, ""):
                    entry[key] = self._scalar(value)
            toolbar.append(entry)

        first = self._optional_int(obj, "FirstVisibleRow", logical_id)
        visible_count = self._optional_int(obj, "VisibleRowCount", logical_id)
        visible_range = None
        if first is not None and visible_count is not None:
            visible_range = (first, max(first, first + max(visible_count, 1) - 1))
        selected = self._parse_indices(
            self._read(obj, "SelectedRows", "", logical_id, report_missing=False)
        )
        filtered = [
            column.id
            for column in columns
            if bool(
                self._query(
                    obj,
                    "IsColumnFiltered",
                    column.id,
                    default=False,
                    context=logical_id,
                )
            )
        ]
        sorted_columns = [
            column.id
            for column in columns
            if self._text(
                self._query(
                    obj,
                    "GetColumnSortType",
                    column.id,
                    default="",
                    context=logical_id,
                )
            ).lower()
            not in {"", "none", "0"}
        ]
        return CollectionDescriptor(
            component_logical_id=logical_id,
            kind="grid" if not toolbar else "alv",
            columns=columns,
            row_count=self._optional_int(obj, "RowCount", logical_id),
            total_column_count=count,
            visible_range=visible_range,
            horizontal_position=self._scroll_position(obj, "HorizontalScrollbar", logical_id),
            vertical_position=self._scroll_position(obj, "VerticalScrollbar", logical_id),
            selection_mode=self._text(
                self._read(obj, "SelectionMode", "", logical_id, report_missing=False)
            ),
            selected_rows=selected,
            toolbar_actions=toolbar,
            visible_cells=self._grid_visible_cells(
                obj, logical_id, columns, first, visible_count
            ),
            filtered_columns=filtered,
            sorted_columns=sorted_columns,
            coverage={
                "columns": "complete",
                "toolbar_buttons": "complete",
                "visible_cells": "structural_redacted",
                "context_menu_actions": "not_observed_until_opened",
            },
            provenance={"source": "sap_gui_scripting", "read_only": True},
            volatile_properties={
                "current_row": self._scalar(
                    self._read(obj, "CurrentCellRow", None, logical_id, report_missing=False)
                ),
                "current_column": self._scalar(
                    self._read(obj, "CurrentCellColumn", "", logical_id, report_missing=False)
                ),
            },
        )

    def _table_descriptor(self, obj: Any, logical_id: str) -> CollectionDescriptor:
        columns_obj = self._read(obj, "Columns", None, logical_id, report_missing=False)
        columns: list[CollectionColumn] = []
        raw_column_count = self._collection_count(columns_obj, f"{logical_id}.Columns")
        for index, column in enumerate(self._collection_items(columns_obj, f"{logical_id}.Columns")):
            column_id = self._text(self._read(column, "Name", str(index), logical_id))
            title = self._text(
                self._read(column, "Title", "", logical_id, report_missing=False)
            ) or self._text(self._read(column, "Text", column_id, logical_id, report_missing=False))
            position = self._integer(
                self._read(column, "Position", index, logical_id, report_missing=False), index
            )
            columns.append(
                CollectionColumn(
                    id=column_id,
                    title=title,
                    position=position,
                    editable=self._optional_bool(column, "Changeable", logical_id),
                    visible=self._optional_bool(column, "Visible", logical_id),
                )
            )
        first = self._optional_int(obj, "FirstVisibleRow", logical_id)
        visible_count = self._optional_int(obj, "VisibleRowCount", logical_id)
        visible_range = None
        if first is not None and visible_count is not None:
            visible_range = (first, max(first, first + max(visible_count, 1) - 1))
        return CollectionDescriptor(
            component_logical_id=logical_id,
            kind="table",
            columns=columns,
            row_count=self._optional_int(obj, "RowCount", logical_id),
            total_column_count=raw_column_count,
            visible_range=visible_range,
            horizontal_position=self._scroll_position(obj, "HorizontalScrollbar", logical_id),
            vertical_position=self._scroll_position(obj, "VerticalScrollbar", logical_id),
            selection_mode=self._text(
                self._read(obj, "SelectionMode", "", logical_id, report_missing=False)
            ),
            selected_rows=self._parse_indices(
                self._read(obj, "SelectedRows", "", logical_id, report_missing=False)
            ),
            visible_cells=self._table_visible_cells(
                obj, logical_id, columns, first, visible_count
            ),
            coverage={
                "columns": "complete" if len(columns) == raw_column_count else "partial",
                "rows": "metadata_only",
                "visible_cells": "structural_redacted",
                "context_menu_actions": "not_observed_until_opened",
            },
            provenance={"source": "sap_gui_scripting", "read_only": True},
        )

    def _apo_grid_descriptor(self, obj: Any, logical_id: str) -> CollectionDescriptor:
        count = self._integer(self._read(obj, "ColumnCount", 0, logical_id), 0)
        columns = [
            CollectionColumn(id=str(index), position=index, visible=None)
            for index in range(max(0, count))
        ]
        first = self._optional_int(obj, "FirstVisibleRow", logical_id)
        visible_count = self._optional_int(obj, "VisibleRowCount", logical_id)
        visible_range = (
            (first, max(first, first + max(visible_count, 1) - 1))
            if first is not None and visible_count is not None
            else None
        )
        return CollectionDescriptor(
            component_logical_id=logical_id,
            kind="apo_grid",
            columns=columns,
            row_count=self._optional_int(obj, "RowCount", logical_id),
            total_column_count=count,
            visible_range=visible_range,
            horizontal_position=self._scroll_position(obj, "HorizontalScrollbar", logical_id),
            vertical_position=self._scroll_position(obj, "VerticalScrollbar", logical_id),
            visible_cells=self._grid_visible_cells(obj, logical_id, columns, first, visible_count),
            coverage={"columns": "index_only", "visible_cells": "structural_redacted", "context_menu_actions": "not_observed_until_opened"},
            provenance={"source": "sap_gui_scripting", "read_only": True},
        )

    def _calendar_descriptor(self, obj: Any, logical_id: str) -> CollectionDescriptor:
        orientation = self._read(obj, "horizontal", _MISSING, logical_id, report_missing=False)
        focused = self._read(obj, "FocusedElement", _MISSING, logical_id, report_missing=False)
        volatile: dict[str, Any] = {}
        for attr in ("FirstVisibleDate", "LastVisibleDate", "FocusDate", "SelectionInterval", "startSelection", "endSelection", "Today"):
            value = self._read(obj, attr, _MISSING, logical_id, report_missing=False)
            if value is not _MISSING:
                volatile[attr] = "<redacted>"
        return CollectionDescriptor(
            component_logical_id=logical_id,
            kind="calendar",
            sample_policy="calendar_structure_only",
            coverage={"calendar_cells": "not_sampled", "context_menu_actions": "not_observed_until_opened"},
            provenance={"source": "sap_gui_scripting", "read_only": True},
            volatile_properties={
                **({"horizontal": self._scalar(orientation)} if orientation is not _MISSING else {}),
                **({"focused_element": self._scalar(focused)} if focused is not _MISSING else {}),
                **volatile,
            },
        )

    def _grid_visible_cells(
        self,
        obj: Any,
        logical_id: str,
        columns: list[CollectionColumn],
        first: int | None,
        visible_count: int | None,
    ) -> list[CollectionCell]:
        if first is None or visible_count is None:
            return []
        cells: list[CollectionCell] = []
        for row in range(first, first + max(0, visible_count)):
            for column in columns:
                cell_type = self._text(self._query(obj, "GetCellType", row, column.id, default="", context=logical_id))
                changeable = self._query(obj, "GetCellChangeable", row, column.id, default=None, context=logical_id)
                selected = self._query(obj, "IsCellSelected", row, column.id, default=None, context=logical_id)
                hotspot = bool(self._query(obj, "IsCellHotspot", row, column.id, default=False, context=logical_id))
                f4 = bool(self._query(obj, "HasCellF4Help", row, column.id, default=False, context=logical_id))
                affordances = self._cell_affordances(cell_type, changeable, hotspot, f4)
                if cell_type or changeable is not None or selected is not None or affordances:
                    cells.append(CollectionCell(absolute_row=row, column_id=column.id, column_position=column.position, cell_type=cell_type, changeable=None if changeable is None else bool(changeable), selected=None if selected is None else bool(selected), affordances=affordances))
        return cells

    def _table_visible_cells(
        self,
        obj: Any,
        logical_id: str,
        columns: list[CollectionColumn],
        first: int | None,
        visible_count: int | None,
    ) -> list[CollectionCell]:
        if visible_count is None:
            return []
        cells: list[CollectionCell] = []
        for visible_row in range(max(0, visible_count)):
            for column in columns:
                cell = self._query(obj, "GetCell", visible_row, column.position, default=None, context=logical_id)
                if cell is None:
                    continue
                cell_type = self._text(self._read(cell, "Type", "", logical_id, report_missing=False))
                changeable = self._optional_bool(cell, "Changeable", logical_id)
                selected = self._optional_bool(cell, "Selected", logical_id)
                affordances = self._cell_affordances(cell_type, changeable, False, False)
                cells.append(CollectionCell(absolute_row=(first or 0) + visible_row, visible_row=visible_row, column_id=column.id, column_position=column.position, cell_type=cell_type, changeable=changeable, selected=selected, affordances=affordances))
        return cells

    @staticmethod
    def _cell_affordances(cell_type: str, changeable: Any, hotspot: bool, f4: bool) -> list[str]:
        normalized = cell_type.casefold()
        actions: set[str] = set()
        if changeable is True:
            actions.add("set_cell")
        if "button" in normalized:
            actions.add("press_cell")
        if "check" in normalized:
            actions.add("set_cell")
        if "radio" in normalized:
            actions.add("select_cell")
        if hotspot or "link" in normalized or "hotspot" in normalized:
            actions.add("open_cell")
        if f4 or "list" in normalized:
            actions.add("value_help")
        return sorted(actions)

    def _tree_descriptor(self, obj: Any, logical_id: str) -> CollectionDescriptor:
        names = self._sequence(
            self._query(obj, "GetColumnNames", default=[], context=logical_id),
            f"{logical_id}.column_names",
        )
        titles = self._sequence(
            self._query(obj, "GetColumnTitles", default=[], context=logical_id),
            f"{logical_id}.column_titles",
        )
        columns = [
            CollectionColumn(
                id=self._text(name),
                title=self._text(titles[index]) if index < len(titles) else self._text(name),
                position=index,
            )
            for index, name in enumerate(names)
        ]
        selected_nodes = self._sequence(
            self._query(obj, "GetSelectedNodes", default=[], context=logical_id),
            f"{logical_id}.selected_nodes",
        )
        selected_set = {self._text(item) for item in selected_nodes}
        node_keys = self._sequence(
            self._query(obj, "GetAllNodeKeys", default=[], context=logical_id),
            f"{logical_id}.node_keys",
        )
        nodes: list[CollectionNode] = []
        expanded_keys: list[str] = []
        for raw_key in node_keys:
            key = self._text(raw_key)
            text = self._text(
                self._query(obj, "GetNodeTextByKey", key, default="", context=logical_id)
            )
            parent_key = self._text(
                self._query(obj, "GetParent", key, default="", context=logical_id)
            )
            path = self._text(
                self._query(obj, "GetNodePathByKey", key, default="", context=logical_id)
            )
            children_count = self._query(
                obj, "GetNodeChildrenCount", key, default=None, context=logical_id
            )
            expandable = self._query(
                obj, "IsFolderExpandable", key, default=None, context=logical_id
            )
            expanded = self._query(
                obj, "IsFolderExpanded", key, default=None, context=logical_id
            )
            if bool(expanded):
                expanded_keys.append(key)
            nodes.append(
                CollectionNode(
                    key=key,
                    text=text,
                    parent_key=parent_key,
                    path=path,
                    depth=path.count("/") if path else None,
                    expandable=(
                        bool(expandable)
                        if expandable is not None
                        else None
                        if children_count is None
                        else self._integer(children_count, 0) > 0
                    ),
                    expanded=None if expanded is None else bool(expanded),
                    selected=key in selected_set,
                    alternate_locators=[
                        {"strategy": "node_key", "value": key},
                        {"strategy": "node_path", "value": path},
                        {"strategy": "parent_text", "parent": parent_key, "text": text},
                    ],
                )
            )
        return CollectionDescriptor(
            component_logical_id=logical_id,
            kind="tree",
            columns=columns,
            nodes=nodes,
            total_column_count=len(columns),
            total_node_count=len(node_keys),
            selection_mode=self._text(
                self._read(obj, "SelectionMode", "", logical_id, report_missing=False)
            ),
            selected_node_keys=[self._text(item) for item in selected_nodes],
            expanded_node_keys=expanded_keys,
            vertical_position=self._scroll_position(obj, "VerticalScrollbar", logical_id),
            coverage={
                "columns": "complete",
                "nodes": "observed",
                "unexpanded_descendants": "not_observed_until_expanded",
                "context_menu_actions": "not_observed_until_opened",
            },
            provenance={"source": "sap_gui_scripting", "read_only": True},
        )

    def _read(
        self,
        obj: Any,
        attr: str,
        default: Any,
        context: str,
        *,
        report_missing: bool = False,
    ) -> Any:
        if obj is None:
            return default
        self.metrics.attribute_reads += 1
        try:
            return getattr(obj, attr)
        except Exception as exc:
            if report_missing or not isinstance(exc, AttributeError):
                self._error(context, attr, exc)
            return default

    def _query(
        self,
        obj: Any,
        method: str,
        *args: Any,
        default: Any,
        context: str,
    ) -> Any:
        """Call an allowlisted read-only query/accessor method."""

        if not (method.startswith(("Get", "Is", "Has")) or method == "ColumnOrder"):
            raise ValueError(f"unsafe scanner method: {method}")
        member = self._read(obj, method, None, context, report_missing=False)
        if member is None or not callable(member):
            return default
        self.metrics.query_calls += 1
        try:
            return member(*args)
        except Exception as exc:
            self._error(context, method, exc)
            return default

    def _collection_items(self, collection: Any, context: str) -> list[Any]:
        if collection is None:
            return []
        if isinstance(collection, (list, tuple)):
            return list(collection)
        count = self._collection_count(collection, context)
        items: list[Any] = []
        for index in range(max(0, count)):
            errors: list[Exception] = []
            for getter in (
                lambda: collection(index),
                lambda: collection.ElementAt(index),
                lambda: collection.Item(index),
            ):
                self.metrics.collection_calls += 1
                try:
                    items.append(getter())
                    break
                except Exception as exc:
                    errors.append(exc)
            else:
                self._error(context, f"index[{index}]", errors[-1])
        return items

    def _collection_count(self, collection: Any, context: str) -> int:
        if collection is None:
            return 0
        if isinstance(collection, (list, tuple)):
            return len(collection)
        for attr in ("Count", "Length"):
            value = self._read(collection, attr, _MISSING, context, report_missing=False)
            if value is not _MISSING:
                return self._integer(value, 0)
        try:
            return len(collection)
        except Exception:
            return 0

    def _sequence(self, value: Any, context: str) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, (list, tuple)):
            return list(value)
        if isinstance(value, str):
            return [value] if value else []
        if self._collection_count(value, context):
            return self._collection_items(value, context)
        try:
            return list(value)
        except (TypeError, ValueError):
            return [value]

    def _optional_bool(self, obj: Any, attr: str, context: str) -> bool | None:
        value = self._read(obj, attr, _MISSING, context, report_missing=False)
        return None if value is _MISSING else bool(value)

    def _optional_int(self, obj: Any, attr: str, context: str) -> int | None:
        value = self._read(obj, attr, _MISSING, context, report_missing=False)
        return None if value is _MISSING else self._integer(value, 0)

    def _scroll_position(self, obj: Any, attr: str, context: str) -> int | None:
        scrollbar = self._read(obj, attr, None, context, report_missing=False)
        if scrollbar is None:
            return None
        return self._optional_int(scrollbar, "Position", f"{context}.{attr}")

    def _alternate_locators(
        self,
        sap_id: str,
        component_type: str,
        subtype: str,
        name: str,
        text: str,
        parent: str,
    ) -> list[dict[str, Any]]:
        locators: list[dict[str, Any]] = [{"strategy": "sap_id", "value": sap_id}]
        if name:
            locators.append(
                {
                    "strategy": "parent_type_name",
                    "parent": parent,
                    "type": component_type,
                    "sub_type": subtype,
                    "name": name,
                }
            )
        if text and component_type in {"GuiButton", "GuiTab", "GuiMenu", "GuiLabel"}:
            locators.append(
                {
                    "strategy": "parent_type_text",
                    "parent": parent,
                    "type": component_type,
                    "text": text,
                }
            )
        return locators

    def _unique_logical_id(self, sap_id: str) -> str:
        candidate = sap_id
        suffix = 2
        while candidate in self._logical_ids:
            candidate = f"{sap_id}#{suffix}"
            suffix += 1
        self._logical_ids.add(candidate)
        return candidate

    @staticmethod
    def _plain_sequence(value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, (list, tuple)):
            return list(value)
        if isinstance(value, str):
            return [value] if value else []
        try:
            return list(value)
        except (TypeError, ValueError):
            return [value]

    @staticmethod
    def _parse_indices(value: Any) -> list[int]:
        if isinstance(value, (list, tuple)):
            values = value
        else:
            values = re.split(r"[,;\s]+", str(value or "").strip())
        result: set[int] = set()
        for item in values:
            token = str(item).strip()
            if not token:
                continue
            match = re.fullmatch(r"(-?\d+)-(-?\d+)", token)
            if match:
                start, end = int(match.group(1)), int(match.group(2))
                result.update(range(start, end + (1 if end >= start else -1), 1 if end >= start else -1))
            else:
                try:
                    result.add(int(token))
                except ValueError:
                    continue
        return sorted(result)

    @staticmethod
    def _scalar(value: Any) -> Any:
        if value is _MISSING or value is None or isinstance(value, (str, int, float, bool)):
            return None if value is _MISSING else value
        return str(value)

    @staticmethod
    def _integer(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _text(value: Any) -> str:
        return "" if value is None or value is _MISSING else str(value)

    @staticmethod
    def _name_from_id(sap_id: str) -> str:
        return sap_id.rsplit("/", 1)[-1] if sap_id else ""

    @staticmethod
    def _role(component_type: str, subtype: str) -> str:
        if component_type in _WINDOW_TYPES:
            return "window"
        if component_type in {"GuiGridView", "GuiTableControl", "GuiTree"}:
            return "collection"
        if component_type == "GuiShell":
            return f"shell:{subtype or 'unknown'}"
        return component_type.removeprefix("Gui").lower() or "unknown"

    def _error(self, context: str, operation: str, exc: Exception) -> None:
        self.metrics.errors.append(
            {
                "context": context,
                "operation": operation,
                "type": type(exc).__name__,
                "message": str(exc),
            }
        )


__all__ = ["ScanMetrics", "UiMapScanner"]
