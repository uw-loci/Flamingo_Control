"""Schema-coverage guard: every ``config.get("...")`` key in a runner module
must be either present in ``_CONFIG_SCHEMAS[node_type]`` or whitelisted in
``LEGACY_KEYS[node_type]``.

This protects against silent drift where someone adds a new ``config.get(...)``
to a runner without giving the property panel a way to edit it. When the
coverage test fails, the fix is one of:
  - add the key to ``_CONFIG_SCHEMAS[node_type]`` so users can edit it, OR
  - add the key to ``LEGACY_KEYS[node_type]`` (with a comment explaining why
    the schema must not expose it — auto-derived, managed by a custom widget,
    legacy fallback, etc.).
"""

import os
import re
import sys
import unittest
from pathlib import Path

# Set Qt platform before any PyQt5 import via property_panel.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Self-contained path setup (see test_pipeline_models.py for rationale).
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from py2flamingo.pipeline.models.pipeline import NodeType
from py2flamingo.pipeline.ui.property_panel import _CONFIG_SCHEMAS, LEGACY_KEYS

RUNNERS_DIR = (
    Path(__file__).parent.parent
    / "src"
    / "py2flamingo"
    / "pipeline"
    / "engine"
    / "node_runners"
)

NODE_TYPE_TO_RUNNER_FILE = {
    NodeType.WORKFLOW: "workflow_runner.py",
    NodeType.THRESHOLD: "threshold_runner.py",
    NodeType.FOR_EACH: "foreach_runner.py",
    NodeType.CONDITIONAL: "conditional_runner.py",
    NodeType.EXTERNAL_COMMAND: "external_command_runner.py",
    NodeType.SAMPLE_VIEW_DATA: "sample_view_data_runner.py",
    NodeType.OVERVIEW_ANALYSIS: "overview_analysis_runner.py",
    NodeType.POST_PROCESSING: "post_processing_runner.py",
    NodeType.TIMED_LOOP: "timed_loop_runner.py",
}

# Matches static-key gets via ``node.config.get("name")``, ``config.get("name")``,
# or ``cfg.get("name")`` — those are the aliases the existing runners use for
# ``node.config``. If a new runner introduces a different alias, add it here.
# Dynamic keys (e.g. ``config.get(f"channel_{i}")``) are intentionally skipped.
CONFIG_GET_RE = re.compile(
    # Leading \b prevents matches inside other identifiers (e.g. coord_config,
    # focus_cfg, display_cfg) which read from a different service dict.
    r"""\b(?:node\.config|config|cfg)\.get\(\s*["']([A-Za-z_][A-Za-z0-9_]*)["']"""
)


def schema_keys(node_type: NodeType) -> set:
    """Extract config keys from ``_CONFIG_SCHEMAS[node_type]``.

    Header entries (widget_type='header') are not real config keys; their key
    starts with an underscore. Filter those out.
    """
    keys = set()
    for entry in _CONFIG_SCHEMAS.get(node_type, []):
        key = entry[0]
        widget_type = entry[2]
        if widget_type == "header" or key.startswith("_"):
            continue
        keys.add(key)
    return keys


def runner_config_keys(node_type: NodeType) -> set:
    """Scan the matching runner module for static ``config.get`` keys."""
    fname = NODE_TYPE_TO_RUNNER_FILE[node_type]
    text = (RUNNERS_DIR / fname).read_text()
    return set(CONFIG_GET_RE.findall(text))


class TestPropertyPanelCoverage(unittest.TestCase):
    def test_every_node_type_has_a_runner_file(self):
        # Defensive: catches typos in the NODE_TYPE_TO_RUNNER_FILE mapping.
        for nt, fname in NODE_TYPE_TO_RUNNER_FILE.items():
            self.assertTrue(
                (RUNNERS_DIR / fname).exists(),
                f"{nt.name}: missing runner file {fname}",
            )

    def test_runner_keys_covered_by_schema_or_legacy(self):
        all_failures = []
        for nt in NodeType:
            used = runner_config_keys(nt)
            allowed = schema_keys(nt) | LEGACY_KEYS.get(nt, set())
            missing = used - allowed
            if missing:
                all_failures.append(f"{nt.name}: {sorted(missing)}")

        if all_failures:
            self.fail(
                "config.get() keys not in _CONFIG_SCHEMAS or LEGACY_KEYS:\n  "
                + "\n  ".join(all_failures)
                + "\n\nFix by either adding the key to the schema (so users "
                "can edit it via the property panel) or to LEGACY_KEYS in "
                "src/py2flamingo/pipeline/ui/property_panel.py (with a comment "
                "explaining why)."
            )

    def test_legacy_keys_dont_overlap_schema(self):
        # LEGACY_KEYS is meant for keys the schema intentionally omits; if a
        # key is in both, that's a contradiction worth flagging.
        for nt, legacy in LEGACY_KEYS.items():
            overlap = legacy & schema_keys(nt)
            self.assertFalse(
                overlap,
                f"{nt.name}: LEGACY_KEYS overlaps schema: {sorted(overlap)}",
            )


if __name__ == "__main__":
    unittest.main()
