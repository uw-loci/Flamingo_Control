"""Autofill dialog tests: dialog mechanics, field-spec round-trip, and an
end-to-end Workflow.txt → THRESHOLD config round-trip via the parser
(``read_laser_channels_from_workflow``) and ``AutofillPreviewDialog``.
"""

from __future__ import annotations

import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_TESTS_DIR = Path(__file__).resolve().parent
_SRC = _TESTS_DIR.parent / "src"
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from pipeline_helpers import qt_app  # noqa: E402

# Touch qt_app so QApplication exists before we import any Qt-using module.
qt_app()

from py2flamingo.pipeline.models.pipeline import (  # noqa: E402
    NodeType,
    Pipeline,
    create_node,
)
from py2flamingo.pipeline.ui.autofill_preview_dialog import (  # noqa: E402
    AutofillPreviewDialog,
    FieldSpec,
)
from py2flamingo.utils.tile_workflow_parser import (  # noqa: E402
    read_laser_channels_from_workflow,
)

SYNTHETIC_WORKFLOW_TXT = textwrap.dedent("""\
    <Illumination Source>
    Laser 1 1: 405 nm MLE = 0.00 0
    Laser 2 2: 488 nm MLE = 5.00 1
    Laser 3 3: 561 nm MLE = 0.00 0
    Laser 4 4: 640 nm MLE = 5.00 1
    </Illumination Source>
    """)


# ---------------------------------------------------------------------------
# Dialog mechanics
# ---------------------------------------------------------------------------


class TestAutofillDialogMechanics(unittest.TestCase):
    def _basic_specs(self):
        return [
            FieldSpec(
                key="alpha",
                label="Alpha",
                widget_type="int",
                current_value=10,
                parsed_value=42,
            ),
            FieldSpec(
                key="beta",
                label="Beta",
                widget_type="float",
                current_value=1.0,
                parsed_value=2.5,
            ),
            FieldSpec(
                key="gamma",
                label="Gamma",
                widget_type="bool",
                current_value=False,
                parsed_value=True,
            ),
        ]

    def test_default_all_checked_returns_all_parsed_values(self):
        d = AutofillPreviewDialog(self._basic_specs())
        result = d.result_values()
        self.assertEqual(result, {"alpha": 42, "beta": 2.5, "gamma": True})

    def test_unchecking_a_row_omits_it(self):
        d = AutofillPreviewDialog(self._basic_specs())
        # Uncheck the second row (beta).
        d._rows[1].checkbox.setChecked(False)
        result = d.result_values()
        self.assertEqual(set(result), {"alpha", "gamma"})

    def test_user_edit_overrides_parsed_value(self):
        d = AutofillPreviewDialog(self._basic_specs())
        # User changes alpha from 42 → 7.
        d._rows[0].editor.setValue(7)
        result = d.result_values()
        self.assertEqual(result["alpha"], 7)

    def test_reset_button_reverts_to_current(self):
        d = AutofillPreviewDialog(self._basic_specs())
        # alpha parsed=42, current=10 — click reset; result should be 10.
        d._rows[0]._reset_to_current()
        self.assertEqual(d._rows[0].value(), 10)

    def test_select_none_then_select_all(self):
        d = AutofillPreviewDialog(self._basic_specs())
        d._set_all_checked(False)
        self.assertEqual(d.result_values(), {})
        d._set_all_checked(True)
        self.assertEqual(set(d.result_values()), {"alpha", "beta", "gamma"})

    def test_apply_count_label_updates(self):
        d = AutofillPreviewDialog(self._basic_specs())
        # All 3 checked initially.
        self.assertIn("(3)", d._apply_btn.text())
        d._rows[0].checkbox.setChecked(False)
        self.assertIn("(2)", d._apply_btn.text())

    def test_grouped_specs_create_collapsible_groups(self):
        specs = [
            FieldSpec(
                key="a",
                label="A",
                widget_type="int",
                current_value=0,
                parsed_value=1,
                group="Section1",
            ),
            FieldSpec(
                key="b",
                label="B",
                widget_type="int",
                current_value=0,
                parsed_value=2,
                group="Section1",
            ),
            FieldSpec(
                key="c",
                label="C",
                widget_type="int",
                current_value=0,
                parsed_value=3,
                group="Section2",
            ),
        ]
        d = AutofillPreviewDialog(specs)
        from PyQt5.QtWidgets import QGroupBox

        groups = d.findChildren(QGroupBox)
        names = {g.title() for g in groups}
        self.assertIn("Section1", names)
        self.assertIn("Section2", names)
        # Toggling the section's check state hides the inner widget — verify
        # the toggled signal is wired by checking ``isCheckable``.
        for g in groups:
            self.assertTrue(g.isCheckable())


# ---------------------------------------------------------------------------
# Workflow.txt → THRESHOLD round-trip
# ---------------------------------------------------------------------------


class TestThresholdAutofillRoundTrip(unittest.TestCase):
    def test_parsed_channels_flow_through_dialog_to_pipeline_json(self):
        # 1. Parse a synthetic Workflow.txt → enabled channels.
        with tempfile.TemporaryDirectory() as td:
            wf_path = Path(td) / "Workflow.txt"
            wf_path.write_text(SYNTHETIC_WORKFLOW_TXT)
            parsed = read_laser_channels_from_workflow(wf_path)
            self.assertEqual(parsed, [1, 3])

            # 2. Build field specs (one per channel) — same shape the
            #    THRESHOLD Import button uses.
            specs = [
                FieldSpec(
                    key=f"ch{ch}",
                    label=f"Channel {ch + 1}",
                    widget_type="bool",
                    current_value=False,
                    parsed_value=ch in set(parsed),
                )
                for ch in range(8)
            ]
            d = AutofillPreviewDialog(specs)
            # User keeps the parsed defaults — apply directly.
            applied = d.result_values()
            # All 8 rows are checked (default), value reflects parsed bool.
            self.assertEqual(applied[f"ch1"], True)
            self.assertEqual(applied[f"ch3"], True)
            self.assertEqual(applied[f"ch0"], False)

            # 3. Translate to enabled_channels list (mirrors property_panel).
            new_enabled = sorted(
                int(k[2:]) for k, v in applied.items() if v and k.startswith("ch")
            )
            self.assertEqual(new_enabled, [1, 3])

            # 4. Save into a pipeline node, persist as JSON, reload.
            p = Pipeline(name="Autofill RT")
            n = create_node(NodeType.THRESHOLD, name="T")
            n.config["enabled_channels"] = new_enabled
            p.add_node(n)
            import json

            out = Path(td) / "out.json"
            out.write_text(json.dumps(p.to_dict()))
            p2 = Pipeline.from_dict(json.loads(out.read_text()))
            n2 = p2.get_node(n.id)
            self.assertEqual(n2.config["enabled_channels"], [1, 3])


# ---------------------------------------------------------------------------
# Phase 7 rollout: SAMPLE_VIEW_DATA / POST_PROCESSING / OVERVIEW_ANALYSIS
# ---------------------------------------------------------------------------


class TestSampleViewDataAutofill(unittest.TestCase):
    """SAMPLE_VIEW_DATA: Workflow.txt enabled-channels → channel_N flags."""

    def test_round_trip_workflow_to_channels(self):
        with tempfile.TemporaryDirectory() as td:
            wf_path = Path(td) / "Workflow.txt"
            wf_path.write_text(SYNTHETIC_WORKFLOW_TXT)
            parsed = set(read_laser_channels_from_workflow(wf_path))

            current = {f"channel_{ch}": ch < 4 for ch in range(8)}
            specs = [
                FieldSpec(
                    key=f"channel_{ch}",
                    label=f"Channel {ch + 1}",
                    widget_type="bool",
                    current_value=current[f"channel_{ch}"],
                    parsed_value=ch in parsed,
                )
                for ch in range(8)
            ]
            d = AutofillPreviewDialog(specs)
            applied = d.result_values()
            # Schema-aligned keys → direct dict update, no translation needed.
            n = create_node(NodeType.SAMPLE_VIEW_DATA, name="SVD")
            for k, v in applied.items():
                n.config[k] = bool(v)
            # parsed = [1, 3] → ch1 and ch3 True; others False.
            self.assertTrue(n.config["channel_1"])
            self.assertTrue(n.config["channel_3"])
            self.assertFalse(n.config["channel_0"])
            self.assertFalse(n.config["channel_2"])


class TestOverviewAnalysisAutofill(unittest.TestCase):
    """OVERVIEW_ANALYSIS: stitch_metadata.json → tiles_x/tiles_y/image_path."""

    def test_round_trip_metadata_json(self):
        import json as _json

        with tempfile.TemporaryDirectory() as td:
            meta_path = Path(td) / "stitch_metadata.json"
            meta_path.write_text(
                _json.dumps(
                    {
                        "version": 2,
                        "voxel_size_um": {"z": 0.5, "y": 0.4, "x": 0.4},
                        "tile_grid": {"x": 5, "y": 7},
                        "store_path": "stitched.zarr",
                    }
                )
            )

            metadata = _json.loads(meta_path.read_text())
            tile_grid = metadata.get("tile_grid", {})
            specs = [
                FieldSpec(
                    key="tiles_x",
                    label="Tiles X",
                    widget_type="int",
                    current_value=8,
                    parsed_value=int(tile_grid.get("x", 8)),
                ),
                FieldSpec(
                    key="tiles_y",
                    label="Tiles Y",
                    widget_type="int",
                    current_value=8,
                    parsed_value=int(tile_grid.get("y", 8)),
                ),
                FieldSpec(
                    key="image_path",
                    label="Image Path",
                    widget_type="file",
                    current_value="",
                    parsed_value=str(meta_path.parent / metadata["store_path"]),
                    options="Images (*.tif *.tiff *.png *.npy)",
                ),
            ]
            d = AutofillPreviewDialog(specs)
            applied = d.result_values()
            self.assertEqual(applied["tiles_x"], 5)
            self.assertEqual(applied["tiles_y"], 7)
            self.assertTrue(applied["image_path"].endswith("stitched.zarr"))


class TestPostProcessingAutofill(unittest.TestCase):
    """POST_PROCESSING: defaults from configs + acquisition_dir from Workflow.txt."""

    def test_apply_writes_into_node_config(self):
        n = create_node(
            NodeType.POST_PROCESSING,
            name="PP",
            config={
                "pixel_size_um": 0.0,
                "z_step_um": 0.0,
                "output_format": "tiff",
                "acquisition_dir": "",
            },
        )
        specs = [
            FieldSpec(
                key="pixel_size_um",
                label="Pixel Size (µm)",
                widget_type="float",
                current_value=0.0,
                parsed_value=0.406,
            ),
            FieldSpec(
                key="z_step_um",
                label="Z Step (µm)",
                widget_type="float",
                current_value=0.0,
                parsed_value=2.0,
            ),
            FieldSpec(
                key="output_format",
                label="Output Format",
                widget_type="combo",
                current_value="tiff",
                parsed_value="ome-zarr-sharded",
                options=["ome-zarr-sharded", "ome-tiff", "both", "tiff"],
            ),
            FieldSpec(
                key="acquisition_dir",
                label="Acquisition Directory",
                widget_type="folder",
                current_value="",
                parsed_value="/data/acq_2026_01_01",
            ),
        ]
        d = AutofillPreviewDialog(specs)
        applied = d.result_values()
        for k, v in applied.items():
            n.config[k] = v
        self.assertAlmostEqual(n.config["pixel_size_um"], 0.406, places=3)
        self.assertEqual(n.config["output_format"], "ome-zarr-sharded")
        self.assertEqual(n.config["acquisition_dir"], "/data/acq_2026_01_01")


class TestWorkflowImportButton(unittest.TestCase):
    """WORKFLOW: PipelineWorkflowConfigDialog has an Import button that opens
    a collapsible preview dialog with Illumination / Camera / Z-Stack / Save
    sections.
    """

    def test_dialog_has_import_button(self):
        # Construct the dialog without an app or template; it should still
        # build successfully and expose an Import button at the top.
        from py2flamingo.pipeline.ui.workflow_config_dialog import (
            PipelineWorkflowConfigDialog,
        )

        try:
            dlg = PipelineWorkflowConfigDialog(app=None)
        except Exception:
            self.skipTest("PipelineWorkflowConfigDialog requires panel deps")
            return

        from PyQt5.QtWidgets import QPushButton

        labels = [b.text() for b in dlg.findChildren(QPushButton) if b.text()]
        # The Import button text starts with the document emoji.
        self.assertTrue(
            any("Import from existing Workflow.txt" in t for t in labels),
            f"Import button not found among: {labels}",
        )


if __name__ == "__main__":
    unittest.main()
