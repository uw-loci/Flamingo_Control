#!/usr/bin/env python3
"""Remove functions named in a dead-code list, one file at a time.

Companion to ``find_dead_code.py``. Hand-editing hundreds of functions invites
slips; deleting them all in one unreviewed sweep invites worse. This does the
mechanical part exactly -- AST line ranges, including decorators and the
docstring -- so the judgement can stay with the human reading ``--show`` first.

    python3 tools/remove_dead_code.py --show <file>          # what would go
    python3 tools/remove_dead_code.py --apply <file>         # do it

Reads the entries for that file from ``dead_code_baseline.txt``. Never touches a
name that is not in the list, so anything the scanner is unsure about survives.

Deliberately refuses to remove:
  * a function whose body contains anything other than statements it owns --
    nested defs are removed with their parent, which is intended, but a
    decorator that registers the function elsewhere is a use the scanner may
    have missed, so decorated functions are reported and SKIPPED.
  * the last method of a class, which would leave an empty class body and a
    SyntaxError. Those are listed for manual handling.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

REPO = Path(__file__).resolve().parents[1]


def _dead_names_for(path: Path, baseline: Path) -> Set[str]:
    """Qualified names ('Class.method' / '<module>.func') listed for `path`."""
    wanted = str(path)
    names = set()
    for line in baseline.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "::" not in line:
            continue
        file_part, qualified = line.split("::", 1)
        if file_part == wanted:
            names.add(qualified)
    return names


def _spans(path: Path, dead: Set[str]) -> Tuple[List[Tuple[int, int, str]], List[str]]:
    """(start, end, label) line spans to delete, plus skipped names."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    spans: List[Tuple[int, int, str]] = []
    skipped: List[str] = []

    def walk(node, prefix: str, siblings: int):
        for child in getattr(node, "body", []):
            if isinstance(child, ast.ClassDef):
                walk(
                    child,
                    (
                        f"{prefix}{child.name}."
                        if prefix == ""
                        else f"{prefix}{child.name}."
                    ),
                    len(child.body),
                )
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified = f"{prefix or '<module>.'}{child.name}"
                if qualified not in dead:
                    continue
                if child.decorator_list:
                    # A decorator can register the function somewhere the
                    # scanner never looked. Not worth the risk automatically.
                    skipped.append(f"{qualified} (decorated)")
                    continue
                if siblings <= 1:
                    skipped.append(f"{qualified} (only member of its class)")
                    continue
                start = child.lineno
                end = child.end_lineno
                spans.append((start, end, qualified))

    walk(tree, "", len(tree.body))
    return spans, skipped


def _apply(path: Path, spans) -> int:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    drop = set()
    for start, end, _label in spans:
        for n in range(start, end + 1):
            drop.add(n)
        # Trailing blank lines that belonged to the removed block.
        n = end + 1
        while n <= len(lines) and lines[n - 1].strip() == "":
            drop.add(n)
            n += 1
    kept = [ln for i, ln in enumerate(lines, start=1) if i not in drop]
    path.write_text("".join(kept), encoding="utf-8")
    return len(spans)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", type=Path)
    ap.add_argument("--baseline", type=Path, default=REPO / "dead_code_baseline.txt")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    path = args.file
    dead = _dead_names_for(path, args.baseline)
    if not dead:
        print(f"nothing listed for {path}")
        return 0

    spans, skipped = _spans(path, dead)
    print(f"{path}: {len(spans)} removable, {len(skipped)} skipped")
    for _s, _e, label in spans:
        print(f"  - {label}")
    for label in skipped:
        print(f"  ! {label}")

    if args.apply and spans:
        removed = _apply(path, spans)
        try:
            ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            print(f"REVERT NEEDED: {path} no longer parses: {exc}", file=sys.stderr)
            return 2
        print(f"removed {removed} from {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
