"""Section headers in the property panel must show their names.

The header branch rendered the schema tuple's DEFAULT instead of its LABEL.
OVERVIEW_ANALYSIS passes its text in both slots and so looked fine, which hid
the bug; POST_PROCESSING passes "" as the default, so its three sections drew
as unlabelled horizontal rules and the names "Voxel Geometry", "Preprocessing"
and "Output" sat unused in the source.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \
        tests/test_property_panel_headers.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

pytest.importorskip("PyQt5")


@pytest.fixture(scope="module")
def app():
    from PyQt5.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _headers(app, node_type):
    from PyQt5.QtWidgets import QLabel

    from py2flamingo.pipeline.models.pipeline import Pipeline, create_node
    from py2flamingo.pipeline.ui.property_panel import PropertyPanel

    pipeline = Pipeline(name="t")
    node = create_node(node_type)
    pipeline.add_node(node)
    panel = PropertyPanel()
    panel.set_pipeline(pipeline)
    panel.show_node(node.id)
    return [
        w.text()
        for w in panel.findChildren(QLabel)
        if "border-bottom" in (w.styleSheet() or "")
    ]


def test_post_processing_sections_are_named(app):
    from py2flamingo.pipeline.models.pipeline import NodeType

    assert _headers(app, NodeType.POST_PROCESSING) == [
        "Voxel Geometry",
        "Preprocessing",
        "Output",
    ]


def test_overview_analysis_sections_still_render(app):
    from py2flamingo.pipeline.models.pipeline import NodeType

    assert _headers(app, NodeType.OVERVIEW_ANALYSIS) == [
        "Thresholds",
        "Post-processing",
    ]


def test_no_header_is_blank(app):
    """Any node type whose schema gains a header must name it."""
    from py2flamingo.pipeline.models.pipeline import NodeType
    from py2flamingo.pipeline.ui.property_panel import _CONFIG_SCHEMAS

    for node_type, schema in _CONFIG_SCHEMAS.items():
        if not any(len(e) > 2 and e[2] == "header" for e in schema):
            continue
        assert all(h.strip() for h in _headers(app, node_type)), node_type.name
