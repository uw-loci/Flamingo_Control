"""The package layering in docs/architecture.md, enforced.

Builds the import graph of ``src/py2flamingo`` with ``ast``, counting every
import: top of file, inside functions, and under ``if TYPE_CHECKING:`` (a
type-only import is harmless at run time but still says the lower layer knows
about the upper one, so it must be allowlisted like any other). Each module is
given a layer from LAYERS; an import from a lower layer into a higher one fails
unless it is in ALLOWED_UPWARD with the reason it is still there.

It also checks that the graph between packages has no cycle. A module at the
package root (``application``, ``main_window``, ``cli``, ...) counts as its own
node, because the root mixes the lowest module (``_version``) with the highest.

Run on its own: ``python tests/test_layering.py`` prints every upward edge and
every package cycle.
"""

import ast
import sys
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
PKG = "py2flamingo"

# Lowest layer first. A module belongs to the layer of the longest prefix that
# names it; "py2flamingo.views" covers views.dialogs, views.widgets, ...
LAYERS = [
    # 0. Leaves: the version string and the protocol (frames, codes, errors,
    #    server layouts, the socket reader, queues and events).
    ["py2flamingo", "py2flamingo._version", "py2flamingo.core"],
    # 1. Data: dataclasses and enums, no I/O beyond their own files.
    ["py2flamingo.models"],
    # 2. Pure helpers: parsers, geometry, tile metrics, the stitcher shim. No
    #    Qt widgets, no services, no connection.
    [
        "py2flamingo.utils",
        "py2flamingo.workflows",
        "py2flamingo.functions",
        "py2flamingo.resources",
        "py2flamingo.stitching",
        "py2flamingo.psf_analysis",
    ],
    # 2b. The hardware configuration (YAML + ScopeSettings + calibration) and
    #     the one pixel-size reader built on it. Reads files through utils'
    #     parsers, so it sits just above them.
    ["py2flamingo.configs"],
    # 3. Subsystem facades over the one connection, stores that own a file,
    #    and the voxel/tile data stores.
    ["py2flamingo.services", "py2flamingo.visualization"],
    # 4. Client-side scans, the pipeline engine, post-update verification,
    #    and the simulated server plus its phantom data.
    [
        "py2flamingo.acquisitions",
        "py2flamingo.pipeline.models",
        "py2flamingo.pipeline.builder",
        "py2flamingo.pipeline.engine",
        "py2flamingo.pipeline.services",
        "py2flamingo.pipeline.headless_io",
        "py2flamingo.verification",
        "py2flamingo.testing",
    ],
    # 5. Controllers: orchestrate one UI action over services; the headless
    #    pipeline host is the controller of a run without the editor.
    [
        "py2flamingo.controllers",
        "py2flamingo.pipeline.controllers",
        "py2flamingo.pipeline.headless_services",
    ],
    # 6. Views: windows, dialogs, panels, widgets, the pipeline editor.
    ["py2flamingo.views", "py2flamingo.pipeline.ui"],
    # 7. Composition roots and entry points.
    [
        "py2flamingo.application",
        "py2flamingo.main_window",
        "py2flamingo.component_factory",
        "py2flamingo.cli",
        "py2flamingo.__main__",
        "py2flamingo.pipeline",
        "py2flamingo.pipeline.cli",
        "py2flamingo.verification.__main__",
        "py2flamingo.psf_analysis.__main__",
        "py2flamingo.stitching.__main__",
    ],
]

# Upward imports that remain, each with why: {(importing module, imported
# module): "reason"}. Empty since 2026-11-22. A new entry needs a reason that
# would convince a reviewer, not "it was easier"; first try the ways a lower
# layer reaches up in docs/architecture.md (pass a callable, a Protocol, a
# registration hook, or move the helper down).
ALLOWED_UPWARD = {}

# The simulated server models the vendor server, so it may know the protocol
# and nothing of the client above it.
SYNTHETIC_SERVER = "py2flamingo.testing.synthetic_microscope"
SYNTHETIC_SERVER_MAY_IMPORT = ("py2flamingo.core",)


def _modules():
    """Map dotted module name -> path for every module under src/py2flamingo."""
    out = {}
    for path in sorted((SRC / PKG).rglob("*.py")):
        rel = path.relative_to(SRC).with_suffix("")
        parts = list(rel.parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        out[".".join(parts)] = path
    return out


def _resolve(name, modules):
    """The longest module that ``name`` (a module or module.attribute) names."""
    while name:
        if name in modules:
            return name
        name = name.rpartition(".")[0]
    return None


def _imports(mod, path, modules):
    """Yield (target module, line) for every py2flamingo import in one file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    is_pkg = path.name == "__init__.py"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = mod.split(".") if is_pkg else mod.split(".")[:-1]
                if node.level > 1:
                    base = base[: len(base) - (node.level - 1)]
                prefix = ".".join(base + ([node.module] if node.module else []))
            else:
                prefix = node.module or ""
            # "from pkg import name": pkg.name if it is a module, else pkg.
            names = [prefix + "." + a.name for a in node.names]
        else:
            continue
        for name in names:
            if name != PKG and not name.startswith(PKG + "."):
                continue
            target = _resolve(name, modules)
            if target is not None and target != mod:
                yield target, node.lineno


def _layer_of(mod):
    best = None
    for i, rules in enumerate(LAYERS):
        for rule in rules:
            if mod == rule or mod.startswith(rule + "."):
                if best is None or len(rule) > best[1]:
                    best = (i, len(rule))
    return None if best is None else best[0]


def _node_of(mod, modules):
    """The package a module belongs to; root and pipeline-root modules stand alone."""
    path = modules[mod]
    pkg = mod if path.name == "__init__.py" else mod.rpartition(".")[0]
    if pkg in (PKG, PKG + ".pipeline"):
        return mod
    return pkg


def build_graph():
    """Return (modules, edges) with edges[(a, b)] = first line of a's import of b."""
    modules = _modules()
    edges = {}
    for mod, path in modules.items():
        for target, line in _imports(mod, path, modules):
            # Importing a.b runs a/__init__ first; that is the package's own
            # business and not an edge of the importer.
            if mod.startswith(target + ".") and modules[target].name == "__init__.py":
                continue
            edges.setdefault((mod, target), line)
    return modules, edges


def upward_edges(modules, edges):
    out = []
    for (a, b), line in sorted(edges.items()):
        la, lb = _layer_of(a), _layer_of(b)
        if la is not None and lb is not None and lb > la:
            out.append((a, b, line, la, lb))
    return out


def package_cycles(modules, edges):
    """Strongly connected components (size > 1) of the package graph."""
    graph = defaultdict(set)
    nodes = set()
    for a, b in edges:
        na, nb = _node_of(a, modules), _node_of(b, modules)
        nodes.update((na, nb))
        # A package's __init__ importing its own children is the idiom of a
        # package re-exporting its members, not a cycle between packages.
        # Any other module of a package importing a sub-package is an edge.
        reexport = (
            a == na and modules[a].name == "__init__.py" and nb.startswith(na + ".")
        )
        if na != nb and not reexport:
            graph[na].add(nb)
    index, low, stack, on, comps, counter = {}, {}, [], set(), [], [0]
    sys.setrecursionlimit(10000)

    def visit(v):
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on.add(v)
        for w in graph[v]:
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                comps.append(sorted(comp))

    for v in sorted(nodes):
        if v not in index:
            visit(v)
    return comps, graph


class TestLayering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.modules, cls.edges = build_graph()

    def test_every_module_has_a_layer(self):
        missing = sorted(m for m in self.modules if _layer_of(m) is None)
        self.assertEqual(missing, [], "add these modules to LAYERS")

    def test_no_upward_import_outside_the_allowlist(self):
        bad = [
            f"{a}:{line} imports {b} (layer {la} -> {lb})"
            for a, b, line, la, lb in upward_edges(self.modules, self.edges)
            if (a, b) not in ALLOWED_UPWARD
        ]
        self.assertEqual(bad, [], "\n" + "\n".join(bad))

    def test_the_allowlist_has_no_stale_entries(self):
        live = {(a, b) for a, b, *_ in upward_edges(self.modules, self.edges)}
        stale = sorted(k for k in ALLOWED_UPWARD if k not in live)
        self.assertEqual(
            stale, [], "these edges are gone: remove them from ALLOWED_UPWARD"
        )

    def test_the_package_graph_has_no_cycle(self):
        comps, graph = package_cycles(self.modules, self.edges)
        lines = []
        for comp in comps:
            members = set(comp)
            lines.append("cycle: " + ", ".join(comp))
            for a in comp:
                for b in sorted(graph[a] & members):
                    lines.append(f"    {a} -> {b}")
        self.assertEqual(comps, [], "\n" + "\n".join(lines))

    def test_the_synthetic_server_knows_only_the_protocol(self):
        bad = sorted(
            b
            for (a, b) in self.edges
            if a == SYNTHETIC_SERVER
            and not any(
                b == p or b.startswith(p + ".") for p in SYNTHETIC_SERVER_MAY_IMPORT
            )
        )
        self.assertEqual(bad, [])

    def test_the_root_package_imports_nothing_but_the_version(self):
        """``import py2flamingo`` must stay free of Qt, napari and sockets."""
        bad = sorted(b for (a, b) in self.edges if a == PKG and b != PKG + "._version")
        self.assertEqual(bad, [])

    def test_the_checker_sees_function_level_and_relative_imports(self):
        """A canary, so an empty result means a clean graph, not a blind parser."""
        modules = {
            "py2flamingo": Path("__init__.py"),
            "py2flamingo.core": Path("__init__.py"),
            "py2flamingo.core.a": Path("a.py"),
            "py2flamingo.views": Path("__init__.py"),
            "py2flamingo.views.v": Path("v.py"),
        }
        src = (
            "def f():\n    from py2flamingo.views.v import Thing\n"
            "from ..views import v\n"
        )
        with tempfile.TemporaryDirectory() as folder:
            tmp = Path(folder) / "a.py"
            tmp.write_text(src, encoding="utf-8")
            found = {t for t, _ in _imports("py2flamingo.core.a", tmp, modules)}
        self.assertIn("py2flamingo.views.v", found)
        self.assertEqual(_layer_of("py2flamingo.views.v"), 7)
        self.assertEqual(_layer_of("py2flamingo.core.a"), 0)


def main():
    modules, edges = build_graph()
    missing = sorted(m for m in modules if _layer_of(m) is None)
    for m in missing:
        print("NO LAYER:", m)
    up = upward_edges(modules, edges)
    print(f"{len(modules)} modules, {len(edges)} module edges, {len(up)} upward")
    for a, b, line, la, lb in up:
        tag = "  (allowed)" if (a, b) in ALLOWED_UPWARD else ""
        print(f"  UP {la}->{lb} {a}:{line} -> {b}{tag}")
    comps, graph = package_cycles(modules, edges)
    print(f"{len(comps)} package cycles, sizes {[len(c) for c in comps]}")
    for comp in comps:
        members = set(comp)
        print("cycle:", ", ".join(comp))
        for a in comp:
            for b in sorted(graph[a] & members):
                ex = [
                    f"{x}:{ln} -> {y}"
                    for (x, y), ln in edges.items()
                    if _node_of(x, modules) == a and _node_of(y, modules) == b
                ]
                print(f"    {a} -> {b}   e.g. {ex[:3]}")


if __name__ == "__main__":
    main()
