"""
PropertyPanel — right sidebar showing editable config for the selected node.

Dynamically generates form widgets based on the selected node's type
and current config dict. Changes are applied immediately to the node model.
"""

import logging
from typing import Any, Dict, Optional, Tuple

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from py2flamingo.pipeline.models.pipeline import NodeType, Pipeline, PipelineNode
from py2flamingo.pipeline.models.python_function import (
    ALLOWED_LIBRARIES,
    DEFAULT_CODE,
    RESULT_KEYS,
    validate_code,
)
from py2flamingo.views.colors import ERROR_COLOR

logger = logging.getLogger(__name__)


# Config keys that runners read but the property panel intentionally does NOT
# render in _CONFIG_SCHEMAS. Either the key is managed by a dedicated widget
# (e.g. THRESHOLD's channel_thresholds list), auto-derived from another source
# (e.g. THRESHOLD's voxel_size_um, taken from coordinate_config when available),
# or kept for backward compatibility with old saved pipelines.
#
# The schema-coverage test (tests/test_pipeline_property_panel_coverage.py)
# treats keys in this allowlist as covered. Add a key here only if the runner
# legitimately needs it but the schema must not expose it.
LEGACY_KEYS: Dict[NodeType, set] = {
    NodeType.THRESHOLD: {
        "channel_thresholds",  # managed by per-channel threshold widgets
        "enabled_channels",  # managed by per-channel threshold widgets
        "voxel_size_um",  # auto-derived from coordinate_config
    },
    NodeType.WORKFLOW: {
        "config_mode",  # legacy: only honored to warn-and-skip
    },
    NodeType.PYTHON_FUNCTION: {
        # Passed to the body as `params`. No widget yet: a node's own knobs are
        # only worth a form once someone wants to reuse one body with two
        # settings, and until then the values belong in the code.
        "params",
    },
}


# One line of help per setting, keyed by (node type, config key). Kept beside
# the schema rather than inside its tuples so the whole set can be read, and
# diffed against docs/pipeline_json_format.md, in one place. The coverage test
# in tests/test_config_field_help.py fails if a schema field has no entry.
#
# What each line is for: the label gives the name, this gives the UNIT, the
# direction of effect, and any coupling the label cannot show. Every float
# field accepts 0.00-99999.00 and every int 0-999999, so the widget itself
# offers no guidance at all.
_CONFIG_HELP: Dict[Tuple[NodeType, str], str] = {
    # --- WORKFLOW ---
    (NodeType.WORKFLOW, "template_file"): (
        "The .txt workflow template this node acquires with."
    ),
    (NodeType.WORKFLOW, "use_input_position"): (
        "Take the stage position from the 'position' input port instead of the "
        "template's. Has no effect while that port is unconnected, which is "
        "the state of a freshly dropped node."
    ),
    (NodeType.WORKFLOW, "auto_z_range"): (
        "Replace the template's Z range with the input object's Z extent plus "
        "the buffer below. Needs the 'z_range' port wired. The result is "
        "quantized to the display voxel size, 50 um by default."
    ),
    (NodeType.WORKFLOW, "auto_tiling"): (
        "Compute a tile grid from the object's XY extent. If it fits one field "
        "of view, tiling is removed; otherwise both start and end positions "
        "are rewritten around the object's centroid. Needs the 'z_range' port "
        "wired."
    ),
    (NodeType.WORKFLOW, "buffer_percent"): (
        "Percent of the object's extent added at EACH end of every axis, so 25 "
        "makes the extent 50% larger. Read only when Auto Z-Range or Auto "
        "Tiling is ticked. Silently clamped to the chamber limits."
    ),
    # --- THRESHOLD ---
    (NodeType.THRESHOLD, "gauss_sigma"): (
        "Blur applied before thresholding, in VOXELS (not micrometres). Raise "
        "for fewer, larger, smoother objects. It also lowers peaks, so it "
        "shifts the effective threshold down. Affects the mask only - "
        "intensity statistics are read from the unsmoothed data."
    ),
    (NodeType.THRESHOLD, "opening_enabled"): (
        "Turn on the morphological opening below."
    ),
    (NodeType.THRESHOLD, "opening_radius"): (
        "Radius in VOXELS. The element is a 6-connected cross dilated N times, "
        "not a sphere. Raise to remove thicker necks and shrink objects. Used "
        "only when Opening Enabled is ticked."
    ),
    (NodeType.THRESHOLD, "min_object_size"): (
        "Discard objects below this many VOXELS. The bound is inclusive, so an "
        "object of exactly this size is kept. 0 disables it. Counts across the "
        "merged mask, not per channel."
    ),
    (NodeType.THRESHOLD, "default_threshold"): (
        "Absolute grey level, NOT scaled for bit depth: 100 is about 0.4% of "
        "full scale on 8-bit and 0.15% on 16-bit. Used only when no "
        "per-channel threshold is set below."
    ),
    # --- PYTHON_FUNCTION ---
    (NodeType.PYTHON_FUNCTION, "code"): (
        "The function body. Available by name: np, ndi, filters, measure, "
        "morphology, exposure, math, and log(). print() does not exist and "
        "import is rejected. Return a value, or a dict with any of value, "
        "boolean, mask, objects, result. Returning a boolean picks a branch."
    ),
    # --- CONDITIONAL ---
    (NodeType.CONDITIONAL, "comparison_op"): (
        "How the 'value' input is compared with the threshold below."
    ),
    (NodeType.CONDITIONAL, "threshold_value"): (
        "Compared against the 'value' input. Overridden by the 'threshold' "
        "port when that is connected."
    ),
    # --- EXTERNAL_COMMAND ---
    (NodeType.EXTERNAL_COMMAND, "command_template"): (
        "Shell command to run. Placeholders are substituted before it is " "executed."
    ),
    (NodeType.EXTERNAL_COMMAND, "input_format"): (
        "How the input data is handed to the command."
    ),
    (NodeType.EXTERNAL_COMMAND, "output_format"): (
        "How the command's output is parsed back into a port value."
    ),
    (NodeType.EXTERNAL_COMMAND, "timeout_seconds"): (
        "Seconds to wait before giving up on the command."
    ),
    # --- TIMED_LOOP ---
    (NodeType.TIMED_LOOP, "iterations"): (
        "How many times to run the body. 0 or less runs until you press Stop. "
        "Note that a pipeline written by hand without this key runs ONCE: the "
        "runner's own default is 1, and 10 is only what a new node is seeded "
        "with here."
    ),
    (NodeType.TIMED_LOOP, "interval_seconds"): (
        "Seconds. Under Timing Mode 'sequential' this is the pause AFTER each "
        "pass finishes. Under 'clock_aligned' it is the spacing of pass "
        "STARTS, so a slow pass eats into the wait."
    ),
    (NodeType.TIMED_LOOP, "timing_mode"): (
        "'sequential' waits the interval after each pass. 'clock_aligned' "
        "keeps passes on a fixed wall-clock cadence even if one overruns."
    ),
    # --- POST_PROCESSING ---
    (NodeType.POST_PROCESSING, "acquisition_dir"): (
        "The raw acquisition folder to stitch. Overridden by the input port "
        "when that is connected. Required."
    ),
    (NodeType.POST_PROCESSING, "output_dir"): (
        "Where the stitched output is written. Defaults to a 'stitched' "
        "folder inside the acquisition directory. Note this down before you "
        "run: nothing reports it afterwards."
    ),
    (NodeType.POST_PROCESSING, "pixel_size_um"): (
        "XY voxel size in micrometres. Leave as prefilled to use this "
        "microscope's resolved pixel size; 0.406 is an old 16x value kept only "
        "as a fallback. Set it explicitly for a reproducible run."
    ),
    (NodeType.POST_PROCESSING, "z_step_um"): (
        "Z step in micrometres. Leave at 0 to read it from the acquisition's "
        "own metadata."
    ),
    (NodeType.POST_PROCESSING, "destripe"): (
        "Run PyStripe artifact correction before stitching. Adds time roughly "
        "proportional to the data size."
    ),
    (NodeType.POST_PROCESSING, "illumination_fusion"): (
        "How left and right illumination are combined. 'max' is the default; "
        "'leonardo' runs in a separate environment and needs it installed."
    ),
    (NodeType.POST_PROCESSING, "deconvolution_enabled"): (
        "Run deconvolution after fusion. Needs a GPU engine installed."
    ),
    (NodeType.POST_PROCESSING, "deconvolution_engine"): (
        "Which deconvolution backend to use. Read only when Deconvolution is " "ticked."
    ),
    (NodeType.POST_PROCESSING, "output_format"): (
        "Container for the stitched result. The sharded OME-Zarr default is "
        "the one the 3-D viewer and Fiji both read."
    ),
    (NodeType.POST_PROCESSING, "package_ozx"): (
        "Also write a single-file .ozx package alongside the output."
    ),
    (NodeType.POST_PROCESSING, "channels"): (
        "Comma-separated channel ids to process. Leave empty for all of them."
    ),
}

#: Channel checkboxes share one line of help: there are eight of them and the
#: label already names the wavelength and the illumination side.
for _ch in range(8):
    _CONFIG_HELP[(NodeType.SAMPLE_VIEW_DATA, f"channel_{_ch}")] = (
        "Include this channel's volume. Note that the runner treats an ABSENT "
        "key as enabled, so a pipeline written by hand that omits this "
        "channel still selects it; the editor writes the value explicitly."
    )

_OVERVIEW_HELP = {
    "method": (
        "Which measure decides whether a tile holds sample. This choice "
        "governs most of the settings below - the ones it does not read are "
        "ignored, whatever they say."
    ),
    "tiles_x": "Number of tile columns the overview is divided into.",
    "tiles_y": "Number of tile rows the overview is divided into.",
    "image_path": (
        "Load the overview from disk. Used only when the 'image' and "
        "'image_path' ports are both unconnected."
    ),
    "entropy_threshold": (
        "ENTROPY method. Shannon entropy in BITS, range 0 to 6. Raise to "
        "select fewer tiles. Each tile is normalized before measuring, so an "
        "empty tile holding only read noise still scores high - that is this "
        "method's main failure mode."
    ),
    "smoothing": (
        "ENTROPY method. Smooth the scores across the tile GRID (not the "
        "image) before thresholding, at a fixed 1.5 tiles."
    ),
    "gradient_threshold": (
        "GRADIENT method. Dimensionless 0 to 1, where 0 is isotropic sample "
        "texture and 1 a strong directional edge. This is the one setting "
        "where a LOWER value selects; above 1 selects every tile."
    ),
    "dog_threshold": (
        "DOG method. Variance of the difference-of-Gaussians image, in grey "
        "levels squared. The default of 0.0 selects EVERY tile."
    ),
    "dog_sigma1": (
        "DOG method. Inner blur radius in IMAGE PIXELS. Must be smaller than "
        "Sigma 2; nothing enforces that."
    ),
    "dog_sigma2": (
        "DOG method. Outer blur radius in IMAGE PIXELS. Must be larger than " "Sigma 1."
    ),
    "variance_threshold": (
        "VARIANCE and COMBINED methods. Grey levels SQUARED, so this must be "
        "re-tuned for a different bit depth: a value tuned on 8-bit data is "
        "out by about 65536x on 16-bit. Raise to select fewer tiles."
    ),
    "edge_threshold": (
        "EDGE and COMBINED methods. Variance of a Laplacian, in grey levels "
        "squared, so this is as much a FOCUS measure as a sample detector - it "
        "responds to defocus. Re-tune for a different bit depth."
    ),
    "intensity_min": (
        "INTENSITY method. Lowest mean grey level that counts as sample."
    ),
    "intensity_max": (
        "INTENSITY method. Highest mean grey level that counts as sample. The "
        "default of 255 ASSUMES 8-BIT data and will reject nearly every tile "
        "of a 16-bit overview."
    ),
    "bp_var_min": "BAND-PASS method. Lowest variance that passes, in grey levels squared.",
    "bp_var_max": "BAND-PASS method. Highest variance that passes, in grey levels squared.",
    "bp_entropy_min": "BAND-PASS method. Lowest entropy that passes, in bits (0 to 6).",
    "tube_interior_method": (
        "TUBE DETECT method. Which measure tests the tube interior once the "
        "walls have been found."
    ),
    "tube_interior_threshold": (
        "TUBE DETECT method. Threshold for the interior test above, so its "
        "units follow that choice: bits for entropy, grey levels squared for "
        "variance."
    ),
    "tube_edge_sensitivity": (
        "TUBE DETECT method. Dimensionless 0 to 1; raise to find edges more "
        "readily. Above about 1.25 the internal term goes negative. If fewer "
        "than two edges are found, EVERY tile is selected."
    ),
    "morphological_cleanup": (
        "Close then open the selected-tile mask, to fill single-tile holes and "
        "drop isolated tiles."
    ),
    "morphological_radius": (
        "Cleanup radius in TILES, not pixels. Read only when Morphological "
        "Cleanup is ticked."
    ),
    "invert": ("Select every tile that was NOT chosen. Applied after cleanup."),
}
for _key, _text in _OVERVIEW_HELP.items():
    _CONFIG_HELP[(NodeType.OVERVIEW_ANALYSIS, _key)] = _text


# Per-node-type config schema: list of (key, label, widget_type, default, options)
_CONFIG_SCHEMAS: Dict[NodeType, list] = {
    NodeType.WORKFLOW: [
        (
            "template_file",
            "Workflow Template",
            "file",
            "",
            {"filter": "Workflow files (*.txt);;All files (*)"},
        ),
        ("use_input_position", "Override Position from Input", "bool", True),
        ("auto_z_range", "Auto Z-Range from Object", "bool", False),
        ("auto_tiling", "Auto Tiling from Object", "bool", False),
        ("buffer_percent", "BBox Buffer (%)", "float", 25.0),
    ],
    NodeType.THRESHOLD: [
        ("gauss_sigma", "Gaussian Sigma", "float", 0.0),
        ("opening_enabled", "Opening Enabled", "bool", False),
        ("opening_radius", "Opening Radius", "int", 1),
        ("min_object_size", "Min Object Size (voxels)", "int", 0),
        ("default_threshold", "Default Threshold", "int", 100),
    ],
    NodeType.FOR_EACH: [],
    NodeType.PYTHON_FUNCTION: [
        ("code", "Code", "code", DEFAULT_CODE),
    ],
    NodeType.CONDITIONAL: [
        (
            "comparison_op",
            "Comparison",
            "combo",
            ">",
            [">", "<", "==", "!=", ">=", "<="],
        ),
        ("threshold_value", "Threshold Value", "float", 0.0),
    ],
    NodeType.EXTERNAL_COMMAND: [
        ("command_template", "Command Template", "str", ""),
        ("input_format", "Input Format", "combo", "numpy", ["numpy", "tiff", "json"]),
        ("output_format", "Output Format", "combo", "json", ["json", "csv", "numpy"]),
        ("timeout_seconds", "Timeout (s)", "int", 300),
    ],
    NodeType.SAMPLE_VIEW_DATA: [
        ("channel_0", "Channel 1 (405nm) L", "bool", True),
        ("channel_1", "Channel 2 (488nm) L", "bool", True),
        ("channel_2", "Channel 3 (561nm) L", "bool", True),
        ("channel_3", "Channel 4 (640nm) L", "bool", True),
        ("channel_4", "Channel 5 (405nm) R", "bool", False),
        ("channel_5", "Channel 6 (488nm) R", "bool", False),
        ("channel_6", "Channel 7 (561nm) R", "bool", False),
        ("channel_7", "Channel 8 (640nm) R", "bool", False),
    ],
    NodeType.OVERVIEW_ANALYSIS: [
        (
            "method",
            "Detection Method",
            "combo",
            "entropy",
            [
                "entropy",
                "bandpass",
                "gradient",
                "dog",
                "tube_detect",
                "variance",
                "edge",
                "intensity",
                "combined",
            ],
        ),
        ("tiles_x", "Tiles X", "int", 8),
        ("tiles_y", "Tiles Y", "int", 8),
        (
            "image_path",
            "Image Path",
            "file",
            "",
            {"filter": "Images (*.tif *.tiff *.png *.npy);;All files (*)"},
        ),
        ("_thresh_header", "Thresholds", "header", "Thresholds"),
        ("entropy_threshold", "Entropy Threshold", "float", 3.0),
        ("smoothing", "Entropy Smoothing", "bool", True),
        ("gradient_threshold", "Gradient Max Anisotropy", "float", 0.5),
        ("dog_threshold", "DoG Min Variance", "float", 0.0),
        ("dog_sigma1", "DoG Sigma 1", "float", 1.0),
        ("dog_sigma2", "DoG Sigma 2", "float", 4.0),
        ("variance_threshold", "Variance Threshold", "float", 100.0),
        ("edge_threshold", "Edge Threshold", "float", 500.0),
        ("intensity_min", "Intensity Min", "float", 20.0),
        ("intensity_max", "Intensity Max", "float", 255.0),
        ("bp_var_min", "Band-pass Var Min", "float", 0.0),
        ("bp_var_max", "Band-pass Var Max", "float", 1000.0),
        ("bp_entropy_min", "Band-pass Entropy Min", "float", 2.0),
        (
            "tube_interior_method",
            "Tube Interior Method",
            "combo",
            "entropy",
            ["entropy", "variance"],
        ),
        ("tube_interior_threshold", "Tube Interior Threshold", "float", 3.0),
        ("tube_edge_sensitivity", "Tube Edge Sensitivity", "float", 0.5),
        ("_post_header", "Post-processing", "header", "Post-processing"),
        ("morphological_cleanup", "Morphological Cleanup", "bool", False),
        ("morphological_radius", "Cleanup Radius", "int", 1),
        ("invert", "Invert Selection", "bool", False),
    ],
    NodeType.TIMED_LOOP: [
        ("iterations", "Iterations (0=indefinite)", "int", 10),
        ("interval_seconds", "Interval (seconds)", "float", 60.0),
        (
            "timing_mode",
            "Timing Mode",
            "combo",
            "sequential",
            ["sequential", "clock_aligned"],
        ),
    ],
    NodeType.POST_PROCESSING: [
        (
            "acquisition_dir",
            "Acquisition Directory",
            "folder",
            "",
        ),
        (
            "output_dir",
            "Output Directory",
            "folder",
            "",
        ),
        ("_voxel_header", "Voxel Geometry", "header", ""),
        ("pixel_size_um", "Pixel Size (\u00b5m)", "float", 0.406),
        ("z_step_um", "Z Step (\u00b5m, 0=auto)", "float", 0.0),
        ("_preprocess_header", "Preprocessing", "header", ""),
        ("destripe", "Destripe (PyStripe)", "bool", False),
        (
            "illumination_fusion",
            "Illum. Fusion",
            "combo",
            "max",
            ["max", "mean", "leonardo"],
        ),
        ("deconvolution_enabled", "Deconvolution", "bool", False),
        (
            "deconvolution_engine",
            "Deconv. Engine",
            "combo",
            "pycudadecon",
            ["pycudadecon", "redlionfish"],
        ),
        ("_output_header", "Output", "header", ""),
        (
            "output_format",
            "Output Format",
            "combo",
            "ome-zarr-sharded",
            ["ome-zarr-sharded", "ome-tiff", "both", "tiff"],
        ),
        ("package_ozx", "Package as .ozx", "bool", False),
        ("channels", "Channels (empty=all)", "str", ""),
    ],
}


class PropertyPanel(QWidget):
    """Dynamic property editor for the selected pipeline node."""

    #: A setting or a node name was changed. The editor uses this to mark the
    #: pipeline unsaved; without it, an hour of retuning thresholds counted as
    #: no change at all.
    node_edited = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pipeline: Optional[Pipeline] = None
        self._current_node: Optional[PipelineNode] = None
        self._widgets: Dict[str, QWidget] = {}
        self._app = None

        self._setup_ui()

    def set_app(self, app):
        """Set the application reference (needed for workflow config dialog)."""
        self._app = app

    def _setup_ui(self):
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(4, 4, 4, 4)

        self._header = QLabel("Properties")
        self._header.setStyleSheet("font-weight: bold; padding: 4px;")
        outer_layout.addWidget(self._header)

        # Empty state hint
        self._empty_hint = QLabel("Select a node on the canvas\nto edit its properties")
        self._empty_hint.setStyleSheet(
            "color: #777; font-size: 10px; padding: 12px 4px;"
        )
        self._empty_hint.setWordWrap(True)
        outer_layout.addWidget(self._empty_hint)

        # Name editor
        self._name_group = QGroupBox("Name")
        name_layout = QVBoxLayout(self._name_group)
        self._name_edit = QLineEdit()
        self._name_edit.textChanged.connect(self._on_name_changed)
        name_layout.addWidget(self._name_edit)
        outer_layout.addWidget(self._name_group)
        self._name_group.hide()

        # Scroll area for config
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self._config_container = QWidget()
        self._config_layout = QFormLayout(self._config_container)
        self._config_layout.setContentsMargins(4, 4, 4, 4)
        scroll.setWidget(self._config_container)
        outer_layout.addWidget(scroll)

        outer_layout.addStretch()

    def set_pipeline(self, pipeline: Pipeline):
        self._pipeline = pipeline

    def show_node(self, node_id: Optional[str]):
        """Display config for the given node (or clear if None)."""
        self._clear_config()

        if not node_id or not self._pipeline:
            self._header.setText("Properties")
            self._empty_hint.show()
            self._name_group.hide()
            self._current_node = None
            return

        node = self._pipeline.get_node(node_id)
        if not node:
            self._header.setText("Properties")
            self._empty_hint.show()
            self._name_group.hide()
            self._current_node = None
            return

        self._current_node = node
        self._empty_hint.hide()
        type_label = node.node_type.name.replace("_", " ").title()
        self._header.setText(f"{type_label} Node")

        # Name
        self._name_group.show()
        self._name_edit.blockSignals(True)
        self._name_edit.setText(node.name)
        self._name_edit.blockSignals(False)

        # Config fields
        schema = _CONFIG_SCHEMAS.get(node.node_type, [])
        for entry in schema:
            key = entry[0]
            label = entry[1]
            widget_type = entry[2]
            default = entry[3]
            options = entry[4] if len(entry) > 4 else None

            current_val = node.config.get(key, default)
            # A header has no config value -- its text is the label. Passing
            # current_val rendered the DEFAULT, so POST_PROCESSING's three
            # sections ("Voxel Geometry", "Preprocessing", "Output") drew as
            # unlabelled horizontal rules: their defaults are "".
            widget = self._create_widget(
                widget_type,
                label if widget_type == "header" else current_val,
                options,
                key,
            )
            if widget_type == "header":
                self._config_layout.addRow(widget)
            else:
                # The help goes on the label as well as the field. Hovering the
                # name is what people try first, and a spinbox is a small
                # target.
                help_text = _CONFIG_HELP.get((node.node_type, key))
                label_widget = QLabel(label)
                if help_text:
                    tip = f"{label}\n\n{help_text}"
                    widget.setToolTip(tip)
                    label_widget.setToolTip(tip)
                self._config_layout.addRow(label_widget, widget)
            self._widgets[key] = widget

        # Channel thresholds for Threshold node
        if node.node_type == NodeType.THRESHOLD:
            self._add_threshold_import_button(node)
            self._add_channel_threshold_widgets(node)

        # Per-NodeType import buttons (Phase 7a — simple flat forms).
        if node.node_type == NodeType.SAMPLE_VIEW_DATA:
            self._add_sample_view_import_button(node)
        elif node.node_type == NodeType.POST_PROCESSING:
            self._add_post_processing_import_button(node)
        elif node.node_type == NodeType.OVERVIEW_ANALYSIS:
            self._add_overview_analysis_import_button(node)

        # "Configure Workflow..." button for Workflow nodes
        if node.node_type == NodeType.WORKFLOW:
            self._add_workflow_configure_button(node)

    def _create_code_widget(self, value, key: str) -> QWidget:
        """A code box that says what is available and what is wrong, as you type.

        The validation label is the point. Without it the first sign of a typo
        is a pipeline failing partway through a run, which on this instrument
        can mean a stack that has already started.
        """
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        available = QLabel(
            "Available: " + ", ".join(sorted(ALLOWED_LIBRARIES)) + ", log()\n"
            "Inputs: volume (3-D array), volumes ({channel: array}), "
            "value, objects, params\n"
            "Return a value, or a dict of: " + ", ".join(RESULT_KEYS)
        )
        available.setWordWrap(True)
        available.setStyleSheet("color: gray; font-size: 8pt;")
        layout.addWidget(available)

        editor = QPlainTextEdit(str(value))
        editor.setTabChangesFocus(False)
        editor.setMinimumHeight(180)
        font = editor.font()
        font.setFamily("Courier New")
        font.setStyleHint(font.Monospace)
        editor.setFont(font)
        layout.addWidget(editor)

        errors = QLabel()
        errors.setWordWrap(True)
        layout.addWidget(errors)

        def on_changed():
            text = editor.toPlainText()
            self._on_config_changed(key, text)
            problems = validate_code(text)
            if problems:
                errors.setText("\n".join(str(e) for e in problems[:5]))
                errors.setStyleSheet(f"color: {ERROR_COLOR}; font-size: 9pt;")
            else:
                errors.setText("Code is valid.")
                errors.setStyleSheet("color: gray; font-size: 9pt;")

        editor.textChanged.connect(on_changed)
        on_changed()
        return container

    def _create_widget(self, widget_type: str, value, options, key: str) -> QWidget:
        """Create an appropriate input widget."""
        if widget_type == "str":
            w = QLineEdit(str(value))
            w.textChanged.connect(lambda v, k=key: self._on_config_changed(k, v))
            return w

        elif widget_type == "int":
            w = QSpinBox()
            w.setRange(0, 999999)
            w.setValue(int(value))
            w.valueChanged.connect(lambda v, k=key: self._on_config_changed(k, v))
            return w

        elif widget_type == "float":
            w = QDoubleSpinBox()
            w.setRange(0.0, 99999.0)
            w.setDecimals(2)
            w.setValue(float(value))
            w.valueChanged.connect(lambda v, k=key: self._on_config_changed(k, v))
            return w

        elif widget_type == "bool":
            w = QCheckBox()
            w.setChecked(bool(value))
            w.stateChanged.connect(
                lambda state, k=key: self._on_config_changed(k, state == Qt.Checked)
            )
            return w

        elif widget_type == "combo":
            w = QComboBox()
            if options:
                w.addItems([str(o) for o in options])
                idx = w.findText(str(value))
                if idx >= 0:
                    w.setCurrentIndex(idx)
            w.currentTextChanged.connect(lambda v, k=key: self._on_config_changed(k, v))
            return w

        elif widget_type == "code":
            return self._create_code_widget(value, key)

        elif widget_type == "file":
            container = QWidget()
            layout = QHBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            line_edit = QLineEdit(str(value))
            line_edit.textChanged.connect(
                lambda v, k=key: self._on_config_changed(k, v)
            )
            layout.addWidget(line_edit)
            btn = QPushButton("...")
            btn.setFixedWidth(30)
            file_filter = (
                options.get("filter", "All files (*)")
                if isinstance(options, dict)
                else "All files (*)"
            )
            btn.clicked.connect(
                lambda _, le=line_edit, ff=file_filter: self._browse_file(le, ff)
            )
            layout.addWidget(btn)
            return container

        elif widget_type == "folder":
            container = QWidget()
            layout = QHBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            line_edit = QLineEdit(str(value))
            line_edit.textChanged.connect(
                lambda v, k=key: self._on_config_changed(k, v)
            )
            layout.addWidget(line_edit)
            btn = QPushButton("...")
            btn.setFixedWidth(30)
            btn.clicked.connect(lambda _, le=line_edit: self._browse_folder(le))
            layout.addWidget(btn)
            return container

        elif widget_type == "header":
            w = QLabel(str(value))
            w.setStyleSheet(
                "font-weight: bold; border-bottom: 1px solid #555; padding: 6px 0 2px 0;"
            )
            return w

        # Fallback
        w = QLineEdit(str(value))
        w.textChanged.connect(lambda v, k=key: self._on_config_changed(k, v))
        return w

    def _browse_file(self, line_edit: QLineEdit, file_filter: str):
        """Open a file dialog and set the line edit text."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Select File", line_edit.text(), file_filter
        )
        if path:
            line_edit.setText(path)

    def _browse_folder(self, line_edit: QLineEdit):
        """Open a directory dialog and set the line edit text."""
        path = QFileDialog.getExistingDirectory(
            self, "Select Directory", line_edit.text()
        )
        if path:
            line_edit.setText(path)

    def _add_channel_threshold_widgets(self, node: PipelineNode):
        """Add per-channel enable checkbox + threshold spinbox for Threshold nodes."""
        group = QGroupBox("Channel Thresholds")
        group_layout = QFormLayout(group)

        thresholds = node.config.get("channel_thresholds", {})
        enabled = node.config.get("enabled_channels", [0, 1, 2, 3])

        channel_names = [
            "405nm (DAPI) L",
            "488nm (GFP) L",
            "561nm (RFP) L",
            "640nm (Far-Red) L",
            "405nm (DAPI) R",
            "488nm (GFP) R",
            "561nm (RFP) R",
            "640nm (Far-Red) R",
        ]
        for ch_id in range(8):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)

            cb = QCheckBox()
            cb.setChecked(ch_id in enabled)
            cb.stateChanged.connect(
                lambda state, c=ch_id: self._on_channel_enabled_changed(
                    c, state == Qt.Checked
                )
            )
            row_layout.addWidget(cb)

            spin = QSpinBox()
            spin.setRange(0, 65535)
            spin.setValue(thresholds.get(ch_id, thresholds.get(str(ch_id), 0)))
            spin.valueChanged.connect(
                lambda v, c=ch_id: self._on_threshold_changed(c, v)
            )
            row_layout.addWidget(spin)

            group_layout.addRow(f"Ch {ch_id + 1} ({channel_names[ch_id]})", row)

        self._config_layout.addRow(group)

    def _on_channel_enabled_changed(self, ch_id: int, enabled: bool):
        """Update enabled channels list in config."""
        if self._current_node:
            ch_list = list(
                self._current_node.config.get("enabled_channels", [0, 1, 2, 3])
            )
            if enabled and ch_id not in ch_list:
                ch_list.append(ch_id)
                ch_list.sort()
            elif not enabled and ch_id in ch_list:
                ch_list.remove(ch_id)
            self._current_node.config["enabled_channels"] = ch_list

    def _on_threshold_changed(self, ch_id: int, value: int):
        """Update channel threshold in config."""
        if self._current_node:
            if "channel_thresholds" not in self._current_node.config:
                self._current_node.config["channel_thresholds"] = {}
            self._current_node.config["channel_thresholds"][ch_id] = value

    def _add_threshold_import_button(self, node: PipelineNode):
        """Add an "Import from Workflow.txt…" button to the THRESHOLD panel.

        Reads the laser-channel-enabled flags from a Workflow.txt file and
        opens an :class:`AutofillPreviewDialog` so the user can review and
        tweak before applying.
        """
        btn = QPushButton("\U0001f4c4 Import from Workflow.txt…")
        btn.setToolTip(
            "Import enabled-channel flags from an existing Workflow.txt file"
        )
        btn.setStyleSheet("QPushButton { padding: 6px 12px; }")
        btn.clicked.connect(self._on_threshold_import_clicked)
        self._config_layout.insertRow(0, btn)
        self._widgets["_threshold_import_btn"] = btn

    def _on_threshold_import_clicked(self):
        if not self._current_node:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import settings from Workflow.txt",
            "",
            "Workflow files (*.txt);;All files (*)",
        )
        if not path:
            return

        from pathlib import Path

        from py2flamingo.pipeline.ui.autofill_preview_dialog import (
            AutofillPreviewDialog,
            FieldSpec,
        )
        from py2flamingo.utils.tile_workflow_parser import (
            read_laser_channels_from_workflow,
        )

        try:
            enabled = read_laser_channels_from_workflow(Path(path))
        except Exception as e:
            logger.error("Failed to parse %s: %s", path, e)
            return

        # Per-channel boolean rows. Keys are synthetic ``ch<N>`` — the
        # property-panel handler converts the checked subset back into the
        # canonical ``enabled_channels`` list. The leading "ch" is stripped
        # at parse time so the field-key format is decoupled from the schema.
        current_enabled = set(self._current_node.config.get("enabled_channels", []))
        parsed_enabled = set(enabled)
        labels = [
            "Channel 1 (405nm) L",
            "Channel 2 (488nm) L",
            "Channel 3 (561nm) L",
            "Channel 4 (640nm) L",
            "Channel 5 (405nm) R",
            "Channel 6 (488nm) R",
            "Channel 7 (561nm) R",
            "Channel 8 (640nm) R",
        ]
        specs = [
            FieldSpec(
                key=f"ch{ch}",
                label=labels[ch],
                widget_type="bool",
                current_value=ch in current_enabled,
                parsed_value=ch in parsed_enabled,
            )
            for ch in range(8)
        ]

        dialog = AutofillPreviewDialog(
            specs,
            parent=self,
            title="Import Threshold Settings",
            source_summary=f"Imported from: {path}",
        )
        if dialog.exec_() != dialog.Accepted:
            return
        applied = dialog.result_values()
        # Translate synthetic ``chN`` keys back to enabled_channels list.
        # Only checked rows appear in ``applied``; their boolean values
        # reflect the final checkbox state so the user can still override.
        new_enabled = sorted(
            int(k[2:]) for k, v in applied.items() if v and k.startswith("ch")
        )
        if applied:
            self._current_node.config["enabled_channels"] = new_enabled
        # Refresh so the channel-threshold widgets reflect the new state.
        self.show_node(self._current_node.id)

    # ------------------------------------------------------------------ #
    # SAMPLE_VIEW_DATA: Import enabled channels from Workflow.txt.
    # ------------------------------------------------------------------ #

    def _add_sample_view_import_button(self, node: PipelineNode):
        btn = QPushButton("\U0001f4c4 Import from Workflow.txt…")
        btn.setToolTip("Import enabled-channel flags from a Workflow.txt file")
        btn.setStyleSheet("QPushButton { padding: 6px 12px; }")
        btn.clicked.connect(self._on_sample_view_import_clicked)
        self._config_layout.insertRow(0, btn)
        self._widgets["_sample_view_import_btn"] = btn

    def _on_sample_view_import_clicked(self):
        if not self._current_node:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import from Workflow.txt",
            "",
            "Workflow files (*.txt);;All files (*)",
        )
        if not path:
            return
        from pathlib import Path

        from py2flamingo.pipeline.ui.autofill_preview_dialog import (
            AutofillPreviewDialog,
            FieldSpec,
        )
        from py2flamingo.utils.tile_workflow_parser import (
            read_laser_channels_from_workflow,
        )

        try:
            enabled = set(read_laser_channels_from_workflow(Path(path)))
        except Exception as e:
            logger.error("Failed to parse %s: %s", path, e)
            return

        labels = [
            "Channel 1 (405nm) L",
            "Channel 2 (488nm) L",
            "Channel 3 (561nm) L",
            "Channel 4 (640nm) L",
            "Channel 5 (405nm) R",
            "Channel 6 (488nm) R",
            "Channel 7 (561nm) R",
            "Channel 8 (640nm) R",
        ]
        specs = [
            FieldSpec(
                key=f"channel_{ch}",
                label=labels[ch],
                widget_type="bool",
                current_value=bool(
                    self._current_node.config.get(f"channel_{ch}", ch < 4)
                ),
                parsed_value=ch in enabled,
            )
            for ch in range(8)
        ]
        d = AutofillPreviewDialog(
            specs,
            parent=self,
            title="Import Sample View Data Settings",
            source_summary=f"Imported from: {path}",
        )
        if d.exec_() != d.Accepted:
            return
        applied = d.result_values()
        # Each chN key maps directly to channel_N config — schema-aligned.
        for k, v in applied.items():
            self._current_node.config[k] = bool(v)
        self.show_node(self._current_node.id)

    # ------------------------------------------------------------------ #
    # POST_PROCESSING: pixel_size / z_step / output_format from configs;
    # acquisition_dir from Workflow.txt save settings.
    # ------------------------------------------------------------------ #

    def _add_post_processing_import_button(self, node: PipelineNode):
        btn = QPushButton("\U0001f4c4 Import defaults / from Workflow.txt…")
        btn.setToolTip(
            "Pull pixel size + output format from configs/microscope_hardware.yaml "
            "and configs/stitching_config.yaml; pull acquisition_dir from a "
            "Workflow.txt's Save section."
        )
        btn.setStyleSheet("QPushButton { padding: 6px 12px; }")
        btn.clicked.connect(self._on_post_processing_import_clicked)
        self._config_layout.insertRow(0, btn)
        self._widgets["_post_processing_import_btn"] = btn

    def _on_post_processing_import_clicked(self):
        if not self._current_node:
            return
        from pathlib import Path

        from py2flamingo.configs.config_loader import (
            get_hardware_config,
            get_stitching_defaults,
        )
        from py2flamingo.pipeline.ui.autofill_preview_dialog import (
            AutofillPreviewDialog,
            FieldSpec,
        )

        try:
            hw = get_hardware_config()
            defaults = get_stitching_defaults()
        except Exception as e:
            logger.error("Failed to read hardware/stitching defaults: %s", e)
            hw = None
            defaults = {}

        # Optional: prompt for a Workflow.txt for the save dir.
        wf_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import save directory from Workflow.txt (optional — Cancel to skip)",
            "",
            "Workflow files (*.txt);;All files (*)",
        )
        save_dir_parsed = ""
        if wf_path:
            try:
                from py2flamingo.utils.tile_workflow_parser import (
                    read_save_directory_from_workflow,
                )

                drive, sub = read_save_directory_from_workflow(Path(wf_path))
                if drive or sub:
                    save_dir_parsed = str(Path(drive) / sub)
            except Exception as e:
                logger.warning("Could not parse save dir from %s: %s", wf_path, e)

        cfg = self._current_node.config
        specs = [
            FieldSpec(
                key="pixel_size_um",
                label="Pixel Size (µm)",
                widget_type="float",
                current_value=float(cfg.get("pixel_size_um", 0.406)),
                parsed_value=(
                    float(getattr(hw, "effective_pixel_size_um", 0.406))
                    if hw
                    else float(cfg.get("pixel_size_um", 0.406))
                ),
            ),
            FieldSpec(
                key="z_step_um",
                label="Z Step (µm, 0=auto)",
                widget_type="float",
                current_value=float(cfg.get("z_step_um", 0.0)),
                parsed_value=float(
                    defaults.get("z_step_um", cfg.get("z_step_um", 0.0))
                    if isinstance(defaults, dict)
                    else cfg.get("z_step_um", 0.0)
                ),
            ),
            FieldSpec(
                key="output_format",
                label="Output Format",
                widget_type="combo",
                current_value=cfg.get("output_format", "ome-zarr-sharded"),
                parsed_value=(
                    defaults.get(
                        "output_format", cfg.get("output_format", "ome-zarr-sharded")
                    )
                    if isinstance(defaults, dict)
                    else cfg.get("output_format", "ome-zarr-sharded")
                ),
                options=["ome-zarr-sharded", "ome-tiff", "both", "tiff"],
            ),
            FieldSpec(
                key="acquisition_dir",
                label="Acquisition Directory",
                widget_type="folder",
                current_value=cfg.get("acquisition_dir", ""),
                parsed_value=(
                    save_dir_parsed
                    if save_dir_parsed
                    else cfg.get("acquisition_dir", "")
                ),
            ),
        ]
        d = AutofillPreviewDialog(
            specs,
            parent=self,
            title="Import Post-Processing Settings",
            source_summary=(
                f"Defaults from configs/; save dir from {wf_path}"
                if wf_path
                else "Defaults from configs/microscope_hardware.yaml + stitching_config.yaml"
            ),
        )
        if d.exec_() != d.Accepted:
            return
        applied = d.result_values()
        for k, v in applied.items():
            self._current_node.config[k] = v
        self.show_node(self._current_node.id)

    # ------------------------------------------------------------------ #
    # OVERVIEW_ANALYSIS: tiles_x/tiles_y/image_path from stitch_metadata.json.
    # ------------------------------------------------------------------ #

    def _add_overview_analysis_import_button(self, node: PipelineNode):
        btn = QPushButton("\U0001f4c4 Import from stitch_metadata.json…")
        btn.setToolTip(
            "Pull tile grid dimensions and image path from a stitched dataset's "
            "stitch_metadata.json (v2 format)."
        )
        btn.setStyleSheet("QPushButton { padding: 6px 12px; }")
        btn.clicked.connect(self._on_overview_analysis_import_clicked)
        self._config_layout.insertRow(0, btn)
        self._widgets["_overview_analysis_import_btn"] = btn

    def _on_overview_analysis_import_clicked(self):
        if not self._current_node:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import from stitch_metadata.json",
            "",
            "Stitch metadata (stitch_metadata.json);;JSON files (*.json);;All files (*)",
        )
        if not path:
            return

        import json
        from pathlib import Path

        from py2flamingo.pipeline.ui.autofill_preview_dialog import (
            AutofillPreviewDialog,
            FieldSpec,
        )

        try:
            metadata = json.loads(Path(path).read_text())
        except Exception as e:
            logger.error("Failed to read %s: %s", path, e)
            return

        # Tile grid: v2 keeps it under metadata["tile_grid"] = {"x": .., "y": ..}
        # but tolerate a few common spellings.
        tile_grid = metadata.get("tile_grid") or metadata.get("tiles") or {}
        parsed_tiles_x = int(
            tile_grid.get("x") or tile_grid.get("nx") or metadata.get("tiles_x") or 8
        )
        parsed_tiles_y = int(
            tile_grid.get("y") or tile_grid.get("ny") or metadata.get("tiles_y") or 8
        )
        # Image path: point at the stitched store / metadata directory.
        parsed_image_path = (
            str(Path(path).parent / metadata.get("store_path", ""))
            if metadata.get("store_path")
            else str(Path(path).parent)
        )

        cfg = self._current_node.config
        specs = [
            FieldSpec(
                key="tiles_x",
                label="Tiles X",
                widget_type="int",
                current_value=int(cfg.get("tiles_x", 8)),
                parsed_value=parsed_tiles_x,
            ),
            FieldSpec(
                key="tiles_y",
                label="Tiles Y",
                widget_type="int",
                current_value=int(cfg.get("tiles_y", 8)),
                parsed_value=parsed_tiles_y,
            ),
            FieldSpec(
                key="image_path",
                label="Image Path",
                widget_type="file",
                current_value=cfg.get("image_path", ""),
                parsed_value=parsed_image_path,
                options="Images (*.tif *.tiff *.png *.npy);;All files (*)",
            ),
        ]
        d = AutofillPreviewDialog(
            specs,
            parent=self,
            title="Import Overview Analysis Settings",
            source_summary=f"Imported from: {path}",
        )
        if d.exec_() != d.Accepted:
            return
        applied = d.result_values()
        for k, v in applied.items():
            self._current_node.config[k] = v
        self.show_node(self._current_node.id)

    def _add_workflow_configure_button(self, node: PipelineNode):
        """Add a 'Configure Workflow...' button above the template file field."""
        btn = QPushButton("Configure Workflow...")
        btn.setToolTip("Open full workflow configuration dialog")
        btn.setStyleSheet("QPushButton { padding: 6px 12px; font-weight: bold; }")
        btn.clicked.connect(self._on_configure_workflow)
        self._config_layout.insertRow(0, btn)
        self._widgets["_configure_btn"] = btn

    def _on_configure_workflow(self):
        """Open the workflow config dialog and update node on accept."""
        if not self._current_node:
            return

        from py2flamingo.pipeline.ui.workflow_config_dialog import (
            PipelineWorkflowConfigDialog,
        )

        current_template = self._current_node.config.get("template_file", "")
        dialog = PipelineWorkflowConfigDialog(
            app=self._app,
            template_file=current_template,
            parent=self,
        )
        if dialog.exec_() == dialog.Accepted:
            result_path = dialog.get_result_path()
            if result_path:
                self._current_node.config["template_file"] = result_path
                # Update the file path widget display
                template_widget = self._widgets.get("template_file")
                if template_widget:
                    line_edit = template_widget.findChild(QLineEdit)
                    if line_edit:
                        line_edit.setText(result_path)

    def _on_config_changed(self, key: str, value):
        """Update a config value on the current node."""
        if self._current_node:
            self._current_node.config[key] = value
            self.node_edited.emit()

    def _on_name_changed(self, name: str):
        """Update the node's display name."""
        if self._current_node:
            self._current_node.name = name
            self.node_edited.emit()

    def _clear_config(self):
        """Remove all config widgets."""
        self._widgets.clear()
        while self._config_layout.count():
            item = self._config_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
