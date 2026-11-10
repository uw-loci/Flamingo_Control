"""
The server's system metadata document, and the legacy ScopeSettings.txt made from it.

Server builds from 2026-10-06 on no longer have ScopeSettings or
ControlSettings: "Everything relocated to meta data file" (server source
commit f8378244c). The scope sends the whole document (its
SystemMetaData.txt) as ``META_DATA_DOCUMENT`` in answer to
``SUBSYSTEM_CONFIGURATION_GET``.

The document uses the same syntax as the old file, ``<Block>`` ... ``</Block>``
and ``Name = value``, but a different tree: stage limits are per axis under
``Instrument/Electronic box hardware/Stage motors and controllers/Stage x``,
the objective is ``Instrument/Microscope Hardware/Detection/Detection 1/
Detection lens/Magnification``, and so on (server ``MetaDataTemplate.cpp``).

Everything in this client that wants scope settings reads
``microscope_settings/ScopeSettings.txt`` in the OLD layout (the config
loader, the connection service, position presets, the configuration
migration). Rather than teach each of them a second layout,
``legacy_scope_settings_text`` renders the sections they read from the
document, in the old layout, and the raw document is kept beside it as
``SystemMetaData.txt``. Values are copied as text, not converted: the server
reads them with the same names and units it wrote the old file with.

Rendered: ``<Type>``, ``<Stage limits>``, ``<Filter wheel encoder counts>``,
``<Filter wheel position assignments>``. Not rendered, because nothing here
reads them: illumination offsets, camera overlap, LED settings. A value the
document leaves empty is left out, so readers fall back to their defaults
instead of failing on ``float("")``.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Tuple, Union

Node = Dict[str, Union[str, "Node"]]

_OPEN = re.compile(r"^<\s*(?P<name>[^/][^>]*)>$")
_CLOSE = re.compile(r"^</\s*(?P<name>[^>]+)>$")
_VALUE = re.compile(r"^(?P<k>[^=]+?)\s*=\s*(?P<v>.*)$")


def parse(text: str) -> Node:
    """The document as nested dicts: blocks are dicts, values are strings."""
    root: Node = {}
    stack: List[Node] = [root]
    for raw in (text or "").splitlines():
        line = raw.strip().replace("\x00", "")
        if not line or line.startswith("#"):
            continue
        m = _CLOSE.match(line)
        if m:
            if len(stack) > 1:
                stack.pop()
            continue
        m = _OPEN.match(line)
        if m:
            block: Node = {}
            stack[-1][m.group("name").strip()] = block
            stack.append(block)
            continue
        m = _VALUE.match(line)
        if m:
            stack[-1][m.group("k").strip()] = m.group("v").strip()
    return root


def get(doc: Node, path: Sequence[str]) -> Optional[str]:
    """A value by path below ``<MetaData>``, or None if absent or not a value."""
    node: Union[str, Node, None] = doc.get("MetaData", doc)
    for part in path:
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node if isinstance(node, str) else None


_INSTRUMENT = ("Instrument",)
_CHARACTERISTICS = _INSTRUMENT + ("Microscope characteristics",)
_STAGES = _INSTRUMENT + ("Electronic box hardware", "Stage motors and controllers")
_DETECTION_1 = _INSTRUMENT + ("Microscope Hardware", "Detection", "Detection 1")
_ILLUMINATION_SOURCE = _INSTRUMENT + (
    "Microscope Hardware",
    "Illumination setting",
    "Illumination source",
)

# (legacy key, document path) per legacy section, in the old file's order.
_TYPE: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("Microscope type", _CHARACTERISTICS + ("Type",)),
    ("Microscope name", _CHARACTERISTICS + ("Name",)),
    ("Microscope builder", _CHARACTERISTICS + ("Builders", "Index 1", "Name")),
    (
        "Objective lens magnification",
        _DETECTION_1 + ("Detection lens", "Magnification"),
    ),
    (
        "Tube lens design focal length (mm)",
        _DETECTION_1 + ("Tube lens focal length (mm)",),
    ),
    ("Tube lens length (mm)", _DETECTION_1 + ("Tube lens length (mm)",)),
)

_AXES = ("x", "y", "z", "r")
_STAGE_FIELDS = (
    ("Hard limit max", "Hard limit max"),
    ("Hard limit min", "Hard limit min"),
    ("Soft limit max", "Soft limit max"),
    ("Soft limit min", "Soft limit min"),
    ("Home", "Home"),
    ("Unload", "Unload"),
    ("Default velocity", "Default velocity"),
)


def _stage_limits() -> List[Tuple[str, Tuple[str, ...]]]:
    return [
        (f"{legacy} {axis}-axis", _STAGES + (f"Stage {axis}", field))
        for legacy, field in _STAGE_FIELDS
        for axis in _AXES
    ]


def _filter_wheel() -> List[Tuple[str, Tuple[str, ...]]]:
    wheel = _DETECTION_1 + ("Filter wheel 1",)
    return [
        (f"Filter wheel position {i}", wheel + (f"Filter wheel position {i}",))
        for i in range(1, 9)
    ] + [
        (f"Filter wheel description {i}", wheel + (f"Filter wheel description {i}",))
        for i in range(1, 9)
    ]


def _filter_assignments() -> List[Tuple[str, Tuple[str, ...]]]:
    return [
        (f"Laser {i}", _ILLUMINATION_SOURCE + (f"Laser {i}", "Filter wheel position"))
        for i in range(1, 8)
    ] + [("LED", _ILLUMINATION_SOURCE + ("LED setting", "Filter wheel position"))]


def legacy_scope_settings(doc: Node) -> Tuple[Dict[str, Dict[str, str]], List[str]]:
    """Sections in the old ScopeSettings layout, and the legacy keys the
    document left empty or did not have."""
    sections: Dict[str, Dict[str, str]] = {}
    missing: List[str] = []

    def fill(section: str, pairs) -> None:
        out: Dict[str, str] = {}
        for legacy, path in pairs:
            value = get(doc, path)
            if value:
                out[legacy] = value
            else:
                missing.append(f"{section}/{legacy}")
        sections[section] = out

    fill("Filter wheel encoder counts", _filter_wheel())
    fill("Filter wheel position assignments", _filter_assignments())
    fill("Stage limits", _stage_limits())
    fill("Type", _TYPE)
    # The old file had "<IP> <port>" on one line.
    ip = get(doc, _CHARACTERISTICS + ("Address", "IP"))
    port = get(doc, _CHARACTERISTICS + ("Address", "Port"))
    if ip:
        sections["Type"]["Microscope address"] = f"{ip} {port}".strip() if port else ip
    else:
        missing.append("Type/Microscope address")
    return sections, missing


def legacy_scope_settings_text(doc: Node) -> Tuple[str, List[str]]:
    """ScopeSettings.txt text in the old layout, and the keys left out."""
    sections, missing = legacy_scope_settings(doc)
    lines = [
        "# Rendered by Flamingo_Control from the server's system metadata document",
        "# (server builds from 2026-10-06 on). The document itself is in",
        "# SystemMetaData.txt next to this file. Edit settings on the server, not here.",
    ]
    for name, values in sections.items():
        lines.append(f"<{name}>")
        lines.extend(f"  {k} = {v}" for k, v in values.items())
        lines.append(f"</{name}>")
    return "\n".join(lines) + "\n", missing
