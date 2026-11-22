"""Workflow text in the app's keys, whichever server build wrote it.

The server renamed several workflow keys on 2026-10-06
(``SourceF2V2/Shared/Workflow/WorkflowSettings.h``; the 3.0.0 copy is the
reference). A Workflow.txt or ``_Settings.txt`` the server writes uses the keys
of its own build; the app's templates, builders and readers use the 3.0.0 names.
This module is the one place that maps between them. Every workflow reader
parses with :func:`py2flamingo.utils.file_handlers.parse_settings_text` and then
calls :func:`to_app_keys`, so the readers only ever see the app's keys.

========================  =============================  ==============================
App key (3.0.0)           Server 2026-10-06 and later    Mapping
========================  =============================  ==============================
``AOI width``/``height``  ``ROI width``/``height``       renamed, same value
``Exposure time (us)``    ``Exposure time (ms)``         value x 1000
(same)                    ``Exposure time (ns)``         the 3.0.0 server's label; the
                                                         value is ms (x 1000)
``Left path = ON 1``      ``Left path recording = true`` ``ON 1`` / ``OFF 0``
``Laser N ... = P 1``     ``Laser N ... = P true``       recording ``1`` / ``0``
========================  =============================  ==============================

The recording words are read the way the server reads them
(``SystemSupport::settingBool``: true/1/on/yes, false/0/off/no). The server
reads the old path form too: any true word among the values means recording
(``WorkflowIlluminationPath::deserializeSettings``).

The send direction (the app's ``(us)`` to the key the connected server reads)
is :mod:`py2flamingo.core.workflow_exposure`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Union

from py2flamingo.utils.file_handlers import parse_settings_text

US_KEY = "Exposure time (us)"
# Server keys whose value is the exposure in milliseconds: the 2026-10-06+
# label, and the 3.0.0 label (which says ns but holds ms).
MS_KEYS = ("Exposure time (ms)", "Exposure time (ns)")

# App key -> the server's newer name for the same value.
RENAMED = {
    "AOI width": "ROI width",
    "AOI height": "ROI height",
}

PATH_SECTION = "Illumination Path"
SOURCE_SECTION = "Illumination Source"
SIDES = ("Left", "Right")

_TRUE = ("true", "1", "on", "yes")
_FALSE = ("false", "0", "off", "no")


def number(value: Any, default: Any = None) -> Any:
    """A workflow value as a float: ``"9,002"`` (a thousands comma) is 9002.0.
    ``default`` when the value is missing, blank or not a number."""
    try:
        return float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return default


def whole_number(value: Any, default: Any = None) -> Any:
    """A workflow value as an int, rounded: ``"2,048"`` is 2048, ``"2.6"`` is 3.
    ``default`` when the value is missing, blank or not a number."""
    as_float = number(value)
    if (
        as_float is None
        or as_float != as_float
        or as_float
        in (
            float("inf"),
            float("-inf"),
        )
    ):
        return default
    return int(round(as_float))


def setting_bool(text: Any, default: Optional[bool] = False) -> Optional[bool]:
    """The server's reading of a setting word (``SystemSupport::settingBool``)."""
    value = str(text).strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return default


def path_recording(value: Any) -> bool:
    """Whether a ``Left path`` value means recording: any true word, as the
    server reads it ("ON 1", "OFF 0", " 0", "true")."""
    return any(setting_bool(word) for word in str(value).split())


def _ms_to_us_text(value: str) -> str:
    raw = str(value).replace(",", "").strip()
    if not raw:
        return ""
    try:
        us = float(raw) * 1000.0
    except ValueError:
        return value
    return str(int(round(us))) if abs(us - round(us)) < 1e-6 else repr(us)


def _section_to_app_keys(name: str, section: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    has_us = US_KEY in section
    for key, value in section.items():
        if isinstance(value, dict):
            out[key] = value
            continue
        if key in MS_KEYS:
            if has_us:
                continue  # the app's own value wins
            out[US_KEY] = _ms_to_us_text(value)
            has_us = True
            continue
        app = next((a for a, new in RENAMED.items() if new == key), None)
        if app is not None:
            if app not in section:
                out[app] = value
            continue
        if name == PATH_SECTION and key.endswith(" path recording"):
            side = key[: -len(" path recording")]
            if f"{side} path" not in section:
                on = path_recording(value)
                out[f"{side} path"] = "ON 1" if on else "OFF 0"
            continue
        if name == SOURCE_SECTION:
            words = str(value).split()
            if len(words) == 2 and words[1].lower() in _TRUE + _FALSE:
                if words[1] not in ("0", "1"):
                    value = f"{words[0]} {1 if setting_bool(words[1]) else 0}"
        out[key] = value
    return out


def to_app_keys(sections: Dict[str, Any]) -> Dict[str, Any]:
    """A copy of flat ``{section: {key: value}}`` with every server key of
    the table above replaced by the app's key. App keys already present are
    kept as they are."""
    return {
        name: (_section_to_app_keys(name, body) if isinstance(body, dict) else body)
        for name, body in sections.items()
    }


def flatten_sections(tree: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """``{section: {key: value}}`` from a :func:`parse_settings_text` tree.

    The ``<Workflow Settings>`` wrapper is removed and every section, at any
    depth, appears once at the top level with its own keys (a section a file
    leaves open nests the next one; flattening undoes that). Keys outside any
    section are dropped."""
    root = tree.get("Workflow Settings")
    if not isinstance(root, dict):
        root = tree
    out: Dict[str, Dict[str, Any]] = {}

    def walk(level: Dict[str, Any]) -> None:
        for name, body in level.items():
            if not isinstance(body, dict):
                continue
            own = out.setdefault(name, {})
            for k, v in body.items():
                if not isinstance(v, dict):
                    own.setdefault(k, v)
            walk(body)

    walk(root)
    return out


def workflow_sections(text: Union[str, bytes]) -> Dict[str, Dict[str, Any]]:
    """Workflow text as ``{section: {key: value}}`` in the app's keys."""
    if isinstance(text, (bytes, bytearray)):
        text = bytes(text).decode("utf-8", errors="replace")
    return to_app_keys(flatten_sections(parse_settings_text(text)))


def read_workflow_sections(path: Union[str, Path]) -> Dict[str, Dict[str, Any]]:
    """A workflow file as ``{section: {key: value}}`` in the app's keys.

    Raises OSError when the file cannot be read."""
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return workflow_sections(f.read())


def find_value(tree: Dict[str, Any], key: str) -> Optional[str]:
    """The first ``key`` in file order, at any depth, or None.

    Works on a :func:`parse_settings_text` tree or on flat sections."""
    for name, body in tree.items():
        if isinstance(body, dict):
            found = find_value(body, key)
            if found is not None:
                return found
        elif name == key:
            return body
    return None
