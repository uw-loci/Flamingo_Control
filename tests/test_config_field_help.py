"""Every property-panel setting must explain itself on hover.

The labels name the settings; nothing on screen said what any of them meant,
what unit it was in, or which other field it depended on. Several decide where
the microscope points, and every float field accepts 0.00-99999.00 regardless,
so the widget offered no guidance either.

This test is the guard against the help drifting back out of step with the
schema: a new setting without a line of help fails here rather than shipping
as a bare spinbox.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \
        tests/test_config_field_help.py -q
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


def _schema_fields():
    from py2flamingo.pipeline.ui.property_panel import _CONFIG_SCHEMAS

    for node_type, schema in _CONFIG_SCHEMAS.items():
        for entry in schema:
            if len(entry) > 2 and entry[2] == "header":
                continue
            yield node_type, entry[0]


class TestEveryFieldHasHelp:
    def test_no_setting_is_left_unexplained(self, app):
        from py2flamingo.pipeline.ui.property_panel import _CONFIG_HELP

        missing = [
            f"{nt.name}.{key}"
            for nt, key in _schema_fields()
            if (nt, key) not in _CONFIG_HELP
        ]
        assert not missing, f"settings with no help text: {missing}"

    def test_no_help_is_left_for_a_setting_that_went_away(self, app):
        from py2flamingo.pipeline.ui.property_panel import _CONFIG_HELP

        known = set(_schema_fields())
        orphans = [
            f"{nt.name}.{key}" for nt, key in _CONFIG_HELP if (nt, key) not in known
        ]
        assert not orphans, f"help text for settings that no longer exist: {orphans}"

    def test_help_is_a_sentence_not_a_restated_label(self, app):
        from py2flamingo.pipeline.ui.property_panel import _CONFIG_HELP

        too_short = [
            f"{nt.name}.{key}"
            for (nt, key), text in _CONFIG_HELP.items()
            if len(text.split()) < 4
        ]
        assert not too_short, f"help text too thin to be useful: {too_short}"


class TestTheHelpReachesTheScreen:
    @staticmethod
    def _panel(app, node_type):
        from py2flamingo.pipeline.models.pipeline import Pipeline, create_node
        from py2flamingo.pipeline.ui.property_panel import PropertyPanel

        pipeline = Pipeline(name="t")
        node = create_node(node_type)
        pipeline.add_node(node)
        panel = PropertyPanel()
        panel.set_pipeline(pipeline)
        panel.show_node(node.id)
        return panel

    @pytest.mark.parametrize(
        "node_type_name",
        ["THRESHOLD", "WORKFLOW", "OVERVIEW_ANALYSIS", "POST_PROCESSING", "TIMED_LOOP"],
    )
    def test_each_field_widget_carries_a_tooltip(self, app, node_type_name):
        """Checked by key, not by count: the panel also holds Import buttons,
        which carry their own tooltips and are not schema fields."""
        from py2flamingo.pipeline.models.pipeline import NodeType
        from py2flamingo.pipeline.ui.property_panel import _CONFIG_SCHEMAS

        node_type = NodeType[node_type_name]
        panel = self._panel(app, node_type)
        bare = [
            entry[0]
            for entry in _CONFIG_SCHEMAS[node_type]
            if not (len(entry) > 2 and entry[2] == "header")
            and not panel._widgets[entry[0]].toolTip()
        ]
        assert not bare, f"{node_type_name} fields with no tooltip: {bare}"

    def test_the_label_is_hoverable_too(self, app):
        """A spinbox is a small target; people hover the name first."""
        from PyQt5.QtWidgets import QLabel

        from py2flamingo.pipeline.models.pipeline import NodeType

        panel = self._panel(app, NodeType.THRESHOLD)
        tips = [w.toolTip() for w in panel.findChildren(QLabel) if w.toolTip()]
        assert len(tips) >= 5

    def test_the_tooltip_leads_with_the_label(self, app):
        from py2flamingo.pipeline.models.pipeline import NodeType

        panel = self._panel(app, NodeType.THRESHOLD)
        tip = panel._widgets["gauss_sigma"].toolTip()
        assert tip.startswith("Gaussian Sigma")
        assert "VOXELS" in tip


class TestTheHelpSaysTheThingsTheLabelCannot:
    """Spot-checks on the facts that cost data if a reader guesses them."""

    def test_the_buffer_says_it_is_applied_per_side(self, app):
        from py2flamingo.pipeline.models.pipeline import NodeType
        from py2flamingo.pipeline.ui.property_panel import _CONFIG_HELP

        text = _CONFIG_HELP[(NodeType.WORKFLOW, "buffer_percent")]
        assert "EACH end" in text and "50%" in text

    def test_sigma_names_its_unit(self, app):
        from py2flamingo.pipeline.models.pipeline import NodeType
        from py2flamingo.pipeline.ui.property_panel import _CONFIG_HELP

        assert "VOXELS" in _CONFIG_HELP[(NodeType.THRESHOLD, "gauss_sigma")]

    def test_the_gradient_setting_warns_that_lower_selects(self, app):
        from py2flamingo.pipeline.models.pipeline import NodeType
        from py2flamingo.pipeline.ui.property_panel import _CONFIG_HELP

        assert (
            "LOWER" in _CONFIG_HELP[(NodeType.OVERVIEW_ANALYSIS, "gradient_threshold")]
        )

    def test_the_intensity_max_warns_about_bit_depth(self, app):
        from py2flamingo.pipeline.models.pipeline import NodeType
        from py2flamingo.pipeline.ui.property_panel import _CONFIG_HELP

        assert "8-BIT" in _CONFIG_HELP[(NodeType.OVERVIEW_ANALYSIS, "intensity_max")]

    def test_iterations_warns_that_the_runner_default_differs(self, app):
        from py2flamingo.pipeline.models.pipeline import NodeType
        from py2flamingo.pipeline.ui.property_panel import _CONFIG_HELP

        text = _CONFIG_HELP[(NodeType.TIMED_LOOP, "iterations")]
        assert "ONCE" in text

    def test_coupled_fields_name_what_they_depend_on(self, app):
        from py2flamingo.pipeline.models.pipeline import NodeType
        from py2flamingo.pipeline.ui.property_panel import _CONFIG_HELP

        assert "Opening Enabled" in _CONFIG_HELP[(NodeType.THRESHOLD, "opening_radius")]
        assert "unconnected" in _CONFIG_HELP[(NodeType.WORKFLOW, "use_input_position")]
        assert (
            "Deconvolution"
            in _CONFIG_HELP[(NodeType.POST_PROCESSING, "deconvolution_engine")]
        )
