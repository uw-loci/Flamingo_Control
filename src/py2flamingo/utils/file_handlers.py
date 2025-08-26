
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
from typing import Any, Dict, Iterable, List, MutableMapping, Optional, Sequence, Tuple, Union

# ------------------------------
# Basic utilities
# ------------------------------

def ensure_dir(path: Union[str, Path]) -> Path:
    """Ensure a directory exists and return it as a Path."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p

def sanitize_filename(name: str, replacement: str = "_") -> str:
    """Sanitize a filename by replacing unsafe characters."""
    return re.sub(r"[^A-Za-z0-9._-]+", replacement, name).strip(replacement)

def backup_file(path: Union[str, Path]) -> Optional[Path]:
    """Create a simple .bak copy next to the file if it exists."""
    p = Path(path)
    if p.exists() and p.is_file():
        bak = p.with_suffix(p.suffix + ".bak")
        shutil.copy2(p, bak)
        return bak
    return None

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

def text_to_dict(file_path: Union[str, Path]) -> Dict[str, Any]:
    """Parse a legacy nested text file into a nested dict.

    Handles:
      - <Section> ... </Section>
      - key = value pairs
      - '#' comments
      - empty lines
    """
    file_path = Path(file_path)
    root: Dict[str, Any] = {}
    stack: List[Dict[str, Any]] = [root]
    if not file_path.exists():
        return root
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        for raw in f:
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
                k = m.group("k").strip()
                v = m.group("v").strip()
                stack[-1][k] = v
    return root

def workflow_to_dict(path) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists(): return {}
    result: Dict[str, Any] = {}
    current: Optional[str] = None
    with open(p, "r", encoding="utf-8", errors="ignore") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"): continue
            m = _SECTION_OPEN.match(line)
            if m:
                name = m.group("name").strip()
                current = None if name == "Workflow Settings" else name
                if current and current not in result: result[current] = {}
                continue
            m = _KEY_VAL.match(line)
            if m and current:
                k = m.group("k").strip(); v = m.group("v").strip()
                if isinstance(result[current], dict):
                    result[current][k] = v
                else:
                    result[current] = {k: v}
                continue
            if current and not line.startswith("<") and "=" not in line:
                # e.g. <Work Flow Type>\nStack
                result[current] = line
                current = None
                continue
    return result


def dict_to_workflow(path, wf_dict):
    p = Path(path); ensure_dir(p.parent)
    lines = ['<Workflow Settings>']
    def _emit(d, out):
        for k, v in d.items():
            if isinstance(v, dict):
                out.append(f"<{k}>"); _emit(v, out); out.append(f"</{k}>")
            else:
                out.append(f"{k} = {v}")
    _emit(wf_dict, lines)
    lines.append('</Workflow Settings>')
    safe_write(p, "\n".join(lines) + "\n")


def dict_to_text(data: Dict[str, Any]) -> str:
    def _emit(d: Dict[str, Any], indent: int = 0) -> str:
        lines = []
        for k, v in d.items():
            if isinstance(v, dict):
                lines.append(f"{' '*indent}<{k}>")
                lines.append(_emit(v, indent))
                lines.append(f"{' '*indent}</{k}>")
            else:
                lines.append(f"{' '*indent}{k} = {v}")
        return "\n".join(lines)
    return _emit(data) + ("\n" if data else "")


def dict_append_workflow(file_path: Union[str, Path], workflow_dict: Dict[str, Any]) -> None:
    """Append a dict to an existing legacy workflow file under <Workflow Settings>."""
    p = Path(file_path)
    ensure_dir(p.parent)
    with open(p, "a", encoding="utf-8", newline="\n") as f:
        f.write("\n<Workflow Settings>\n")
        for k, v in workflow_dict.items():
            if isinstance(v, dict):
                f.write(f"<{k}>\n")
                for sk, sv in v.items():
                    f.write(f"{sk} = {sv}\n")
                f.write(f"</{k}>\n")
            else:
                f.write(f"{k} = {v}\n")
        f.write("</Workflow Settings>\n")

def dict_comment(wf_dict: Dict[str, Any], comment: str) -> Dict[str, Any]:
    """Set a human-readable comment on a workflow dict (in-place + return)."""
    wf_dict["Comment"] = comment
    return wf_dict

def dict_save_directory(wf_dict: Dict[str, Any], directory: Union[str, Path]) -> Dict[str, Any]:
    """Set the save directory on a workflow dict (in-place + return)."""
    wf_dict["Save Directory"] = str(directory)
    return wf_dict

# ------------------------------
# Helpers for nested dict workflows
# ------------------------------

def find_section(wf_dict: Dict[str, Any], section: Sequence[str]) -> Optional[Dict[str, Any]]:
    """Find a nested section by path like ("Workflow Settings", "Imaging")."""
    d: Dict[str, Any] = wf_dict
    for part in section:
        node = d.get(part)
        if not isinstance(node, dict):
            return None
        d = node
    return d

def get_value(wf_dict: Dict[str, Any], section: Sequence[str], key: str, default: Any=None) -> Any:
    """Get a value from a nested section."""
    sec = find_section(wf_dict, section)
    if sec is None:
        return default
    return sec.get(key, default)

def set_value(wf_dict: Dict[str, Any], section: Sequence[str], key: str, value: Any) -> None:
    """Set a value inside a nested section (creates sections as needed)."""
    d: Dict[str, Any] = wf_dict
    for part in section:
        node = d.get(part)
        if not isinstance(node, dict):
            node = {}
            d[part] = node
        d = node
    d[key] = value

def merge_workflow_dicts(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
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

def read_metadata(metadata_path: Union[str, Path] = "microscope_settings/FlamingoMetaData.txt") -> Dict[str, str]:
    """Read FlamingoMetaData.txt (flat key=value style or nested sections)."""
    md = text_to_dict(metadata_path)
    # If nested, flatten one level for common keys
    flat: Dict[str, str] = {}
    def _flatten(d: Dict[str, Any]):
        for k, v in d.items():
            if isinstance(v, dict):
                _flatten(v)
            else:
                flat[k] = str(v)
    _flatten(md)
    return flat

def write_metadata(values: Dict[str, Any], metadata_path: Union[str, Path] = "microscope_settings/FlamingoMetaData.txt") -> None:
    """Write FlamingoMetaData.txt with provided values (flat dict at root)."""
    p = Path(metadata_path)
    ensure_dir(p.parent)
    lines = [f"{k} = {v}" for k, v in values.items()]
    safe_write(p, "\n".join(lines) + "\n")

def read_scope_settings(scope_path: Union[str, Path] = "microscope_settings/ScopeSettings.txt") -> Dict[str, Any]:
    """Read ScopeSettings.txt (nested legacy format) into a dict."""
    return text_to_dict(scope_path)

# ------------------------------
# Command list (for codes)
# ------------------------------

def parse_command_list(path: Union[str, Path]) -> Dict[str, Dict[str, str]]:
    """Parse command_list.txt into nested dict {header: {name: code}}."""
    data = text_to_dict(path)
    # Expected top-level sections are C headers like "CommandCodes.h"
    out: Dict[str, Dict[str, str]] = {}
    for header, inner in data.items():
        if isinstance(inner, dict):
            out[header] = {k: str(v) for k, v in inner.items()}
    return out

# ------------------------------
# CSV logging helpers
# ------------------------------

def save_csv_row(csv_path: Union[str, Path], row: Sequence[Any], header: Optional[Sequence[str]] = None) -> None:
    """Append a row to a CSV, creating with header if not exists."""
    p = Path(csv_path)
    ensure_dir(p.parent)
    new = not p.exists()
    with open(p, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new and header:
            w.writerow(header)
        w.writerow(list(row))

# ------------------------------
# Convenience for typical Flamingo paths
# ------------------------------

def workflow_path_for_sample(sample_name: str, base_dir: Union[str, Path] = "workflows") -> Path:
    """Build a safe workflow file path for a sample (e.g., workflows/<sample>.txt)."""
    safe = sanitize_filename(sample_name)
    return ensure_dir(base_dir) / f"{safe}.txt"
