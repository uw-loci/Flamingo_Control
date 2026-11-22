# src/py2flamingo/utils/file_handlers.py
"""
Robust file I/O helpers for Flamingo workflows, settings, and metadata.

This module restores and extends the legacy text workflow/metadata helpers with
backward-compatible function names and signatures where possible.

Supported text formats (legacy-style):
  - Nested <Section> ... </Section> blocks
  - key = value lines inside sections
  - '#' comment lines
"""

from __future__ import annotations

import csv
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import (
    Any,
    Dict,
    Iterable,
    List,
    MutableMapping,
    Optional,
    Sequence,
    Tuple,
    Union,
)

# ------------------------------
# Basic utilities
# ------------------------------


def ensure_dir(path: Union[str, Path]) -> Path:
    """Ensure a directory exists and return it as a Path."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def safe_write(path: Union[str, Path], data: str, newline: str = "\n") -> None:
    """Atomically write text to path (with .tmp + replace)."""
    p = Path(path)
    ensure_dir(p.parent)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", newline=newline, encoding="utf-8") as f:
        f.write(data)
    os.replace(tmp, p)


# ------------------------------
# Legacy nested text format
# ------------------------------

_SECTION_OPEN = re.compile(r"^<(?P<name>[^/].*?)>\s*$")
_SECTION_CLOSE = re.compile(r"^</(?P<name>.*?)>\s*$")
_KEY_VAL = re.compile(r"^(?P<k>[^=#]+?)\s*=\s*(?P<v>.*)$")


def parse_settings_text(text: str) -> Dict[str, Any]:
    """Parse settings text (workflow, ScopeSettings, ControlSettings) into a
    nested dict, the one parser for the server's key = value format.

    Handles:
      - <Section> ... </Section>, nested; a section left open nests the next
      - key = value pairs (the value stripped, kept as text)
      - '#' comments
      - empty lines

    A key repeated at one level keeps its last value. Lines that are neither
    a tag nor ``key = value`` are ignored.
    """
    root: Dict[str, Any] = {}
    stack: List[Dict[str, Any]] = [root]
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _SECTION_OPEN.match(line)
        if m:
            name = m.group("name").strip()
            new: Dict[str, Any] = {}
            stack[-1][name] = new
            stack.append(new)
            continue
        m = _SECTION_CLOSE.match(line)
        if m:
            if len(stack) > 1:
                stack.pop()
            continue
        m = _KEY_VAL.match(line)
        if m:
            stack[-1][m.group("k").strip()] = m.group("v").strip()
    return root


def text_to_dict(file_path: Union[str, Path]) -> Dict[str, Any]:
    """Parse a settings text file with :func:`parse_settings_text`.

    A file that does not exist gives an empty dict.
    """
    file_path = Path(file_path)
    if not file_path.exists():
        return {}
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        return parse_settings_text(f.read())


def dict_to_workflow(path, wf_dict):
    p = Path(path)
    ensure_dir(p.parent)
    lines = ["<Workflow Settings>"]

    def _emit(d, out):
        for k, v in d.items():
            if isinstance(v, dict):
                out.append(f"<{k}>")
                _emit(v, out)
                out.append(f"</{k}>")
            else:
                out.append(f"{k} = {v}")

    _emit(wf_dict, lines)
    lines.append("</Workflow Settings>")
    safe_write(p, "\n".join(lines) + "\n")


def set_section_values(
    file_path: Union[str, Path], section: str, values: Dict[str, str]
) -> None:
    """Set ``key = value`` lines inside one top-level ``<section>`` of a text file.

    Every other line (comments, indentation, other sections, line endings) is
    kept as it is. A key already in the section has its value replaced; a
    missing key is added before ``</section>``; a missing section is appended
    at the end of the file. The file is rewritten atomically.
    """
    p = Path(file_path)
    raw = p.read_bytes().decode("utf-8", errors="replace")
    newline = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.splitlines()
    pending = dict(values)
    in_section = False
    indent = "  "
    out: List[str] = []
    for line in lines:
        stripped = line.strip()
        m_open = _SECTION_OPEN.match(stripped)
        m_close = _SECTION_CLOSE.match(stripped)
        if m_open and m_open.group("name").strip() == section:
            in_section = True
        elif in_section and m_close and m_close.group("name").strip() == section:
            for k, v in pending.items():
                out.append(f"{indent}{k} = {v}")
            pending.clear()
            in_section = False
        elif in_section:
            m = _KEY_VAL.match(stripped)
            if m:
                indent = line[: len(line) - len(line.lstrip())]
                key = m.group("k").strip()
                if key in pending:
                    out.append(f"{indent}{key} = {pending.pop(key)}")
                    continue
        out.append(line)
    if pending:
        out.append(f"<{section}>")
        out.extend(f"  {k} = {v}" for k, v in pending.items())
        out.append(f"</{section}>")
    safe_write(p, newline.join(out) + newline, newline="")


# ------------------------------
# Helpers for nested dict workflows
# ------------------------------


def merge_workflow_dicts(
    base: Dict[str, Any], override: Dict[str, Any]
) -> Dict[str, Any]:
    """Deep merge override into base (dicts only), returning base for chaining."""
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            merge_workflow_dicts(base[k], v)
        else:
            base[k] = v
    return base


# ------------------------------
# Metadata / settings convenience
# ------------------------------


# ------------------------------
# Command list (for codes)
# ------------------------------


# ------------------------------
# CSV logging helpers
# ------------------------------


# ------------------------------
# Convenience for typical Flamingo paths
# ------------------------------
