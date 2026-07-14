"""Verified SAP GUI Scripting event signatures.

The generated API graph is intentionally not used as an allowlist here: PDF
examples can look like event declarations to a best-effort parser.  This small
catalog contains the 17 event declarations published for GuiApplication and
GuiSession in the SAP GUI Scripting API 8.00 PL08 documentation.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EventParameter:
    """One parameter in an official COM event signature."""

    name: str
    type: str
    sensitive: bool = False


@dataclass(frozen=True, slots=True)
class EventSpec:
    """A verified event that may be connected by an event backend."""

    scope: str
    object_type: str
    name: str
    parameters: tuple[EventParameter, ...] = ()
    callback_must_not_reenter: bool = True

    @property
    def sensitive_parameters(self) -> frozenset[str]:
        return frozenset(item.name for item in self.parameters if item.sensitive)


def _p(name: str, type_name: str, *, sensitive: bool = False) -> EventParameter:
    return EventParameter(name=name, type=type_name, sensitive=sensitive)


# SAP GUI Scripting API 8.00 PL08, GuiApplication and GuiSession events.
EVENT_CATALOG: tuple[EventSpec, ...] = (
    EventSpec(
        "application", "GuiApplication", "CreateSession",
        (_p("Session", "GuiSession"),),
    ),
    EventSpec(
        "application", "GuiApplication", "DestroySession",
        (_p("Session", "GuiSession"),),
    ),
    EventSpec(
        "application", "GuiApplication", "Error",
        (
            _p("ErrorId", "Long"),
            _p("Desc1", "String", sensitive=True),
            _p("Desc2", "String", sensitive=True),
            _p("Desc3", "String", sensitive=True),
            _p("Desc4", "String", sensitive=True),
        ),
    ),
    EventSpec(
        "application", "GuiApplication", "IgnoreSession",
        (_p("SessionMainWindowHandle", "Integer"),),
    ),
    EventSpec(
        "session", "GuiSession", "AbapScriptingEvent",
        (_p("param", "String", sensitive=True),),
    ),
    EventSpec(
        "session", "GuiSession", "Activated",
        (_p("Session", "GuiSession"),),
    ),
    EventSpec(
        "session", "GuiSession", "AutomationFCode",
        (_p("Session", "GuiSession"), _p("FunctionCode", "String")),
    ),
    EventSpec(
        "session", "GuiSession", "Change",
        (
            _p("Session", "GuiSession"),
            _p("Component", "GuiComponent"),
            _p("CommandArray", "Variant", sensitive=True),
        ),
    ),
    EventSpec(
        "session", "GuiSession", "ContextMenu",
        (_p("Session", "GuiSession"), _p("Component", "GuiVComponent")),
    ),
    EventSpec(
        "session", "GuiSession", "Destroy",
        (_p("Session", "GuiSession"),),
    ),
    EventSpec(
        "session", "GuiSession", "EndRequest",
        (_p("Session", "GuiSession"),),
    ),
    EventSpec(
        "session", "GuiSession", "Error",
        (
            _p("Session", "GuiSession"),
            _p("ErrorId", "Long"),
            _p("Desc1", "String", sensitive=True),
            _p("Desc2", "String", sensitive=True),
            _p("Desc3", "String", sensitive=True),
            _p("Desc4", "String", sensitive=True),
        ),
    ),
    EventSpec(
        "session", "GuiSession", "FocusChanged",
        (_p("Session", "GuiSession"), _p("NewFocusedControl", "GuiVComponent")),
    ),
    EventSpec(
        "session", "GuiSession", "HistoryOpened",
        (_p("Session", "GuiSession"), _p("NewFocusedControl", "GuiVComponent")),
    ),
    EventSpec(
        "session", "GuiSession", "Hit",
        (
            _p("Session", "GuiSession"),
            _p("Component", "GuiComponent"),
            _p("InnerObject", "String", sensitive=True),
        ),
    ),
    EventSpec(
        "session", "GuiSession", "ProgressIndicator",
        (_p("percentage", "Long"), _p("Text", "String", sensitive=True)),
    ),
    EventSpec(
        "session", "GuiSession", "StartRequest",
        (_p("Session", "GuiSession"),),
    ),
)


_BY_KEY = {(item.scope.lower(), item.name.lower()): item for item in EVENT_CATALOG}


def get_event_spec(scope: str, event_name: str) -> EventSpec | None:
    """Return a canonical verified event declaration, case-insensitively."""

    return _BY_KEY.get((scope.strip().lower(), event_name.strip().lower()))


def events_for_scope(scope: str) -> tuple[EventSpec, ...]:
    """List verified events for an application/session scope."""

    normalized = scope.strip().lower()
    return tuple(item for item in EVENT_CATALOG if item.scope == normalized)
