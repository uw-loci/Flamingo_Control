"""Unit coverage for the workflow/overview UX fixes.

* Item 2: PositionPresetService emits ``presets_changed`` on save/delete so the
  Workflow tab can refresh live.
* Item 4A: a direct run is finished exactly once (poll monitor vs. IDLE
  broadcast), emitting workflow_finished on success / workflow_failed on error.
* Item 5: the Check-Stack result renders to in-tab HTML (estimates + warnings).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest  # noqa: E402

pytest.importorskip("PyQt5")
from PyQt5.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# ---------------------------------------------------------------------------
# Item 2 — preset change signal
# ---------------------------------------------------------------------------


def test_preset_service_emits_on_save_and_delete(qapp, tmp_path):
    from py2flamingo.models.microscope import Position
    from py2flamingo.services.position_preset_service import PositionPresetService

    svc = PositionPresetService(presets_file=str(tmp_path / "presets.json"))
    fired = []
    svc.presets_changed.connect(lambda: fired.append(True))

    svc.save_preset("A", Position(x=1.0, y=2.0, z=3.0, r=0.0))
    assert len(fired) == 1

    svc.save_preset("B", Position(x=4.0, y=5.0, z=6.0, r=0.0))
    assert len(fired) == 2

    assert svc.delete_preset("A") is True
    assert len(fired) == 3

    # Deleting a missing preset does not emit.
    assert svc.delete_preset("nope") is False
    assert len(fired) == 3


# ---------------------------------------------------------------------------
# Item 4A — direct-run completion/failure fires exactly once
# ---------------------------------------------------------------------------


def _queue_service(qapp):
    from py2flamingo.services.workflow_queue_service import WorkflowQueueService

    return WorkflowQueueService(
        workflow_controller=MagicMock(), connection_service=None
    )


def test_direct_run_finishes_once_success(qapp):
    svc = _queue_service(qapp)
    finished, failed = [], []
    svc.workflow_finished.connect(lambda: finished.append(True))
    svc.workflow_failed.connect(lambda m: failed.append(m))

    svc._direct_run_active = True
    svc._workflow_running = True

    svc._finish_direct_run(success=True)
    svc._finish_direct_run(success=True)  # second path is a no-op (guard)

    assert finished == [True]
    assert failed == []
    assert svc._direct_run_active is False


def test_direct_run_failure_emits_failed(qapp):
    svc = _queue_service(qapp)
    finished, failed = [], []
    svc.workflow_finished.connect(lambda: finished.append(True))
    svc.workflow_failed.connect(lambda m: failed.append(m))

    svc._direct_run_active = True
    svc._finish_direct_run(success=False, message="never started")

    assert finished == []
    assert failed == ["never started"]
    assert svc._direct_run_active is False


def test_finish_no_op_when_not_active(qapp):
    svc = _queue_service(qapp)
    finished = []
    svc.workflow_finished.connect(lambda: finished.append(True))
    svc._direct_run_active = False
    svc._finish_direct_run(success=True)
    assert finished == []


# ---------------------------------------------------------------------------
# Item 5 — Check-Stack result renders to HTML
# ---------------------------------------------------------------------------


def test_format_validation_result_html_has_estimates_and_warnings(qapp):
    from py2flamingo.views.workflow_view import format_validation_result_html

    html = format_validation_result_html(
        {
            "valid": True,
            "errors": [],
            "warnings": ["Tile outside stage range — tile (0, 3): Y=25.3 mm > ..."],
            "estimates": {
                "acquisition_time": 119.0,
                "sample_count": 0,
                "data_size_gb": 15.28,
                "total_images": 1956,
                "num_tiles": 4,
                "num_channels": 1,
                "z_range_um": 4890.0,
                "num_planes": 489,
                "z_step_um": 10.0,
            },
            "hardware_validation": {"valid": True, "message": "Not connected"},
        }
    )
    assert "Estimates" in html
    assert "15.3 GB" in html
    assert "1,956" in html
    assert "Warnings" in html
    assert "outside stage range" in html
    # errors escaping is applied (no raw unescaped angle brackets from input)
    assert "<b" in html  # it is HTML


def test_format_tiling_comparison_html_flags_divergence(qapp):
    from py2flamingo.views.workflow_view import format_tiling_comparison_html

    # client floor+1 (=2) vs server ceil((range+FOV)/step) (=4) -> differ.
    html = format_tiling_comparison_html(
        x_min=5.707,
        x_max=9.5,
        y_min=21.398,
        y_max=25.0,
        fov_mm=2.1454,
        overlap_percent=10.0,
    )
    assert "App (client)" in html
    assert "Server (CheckStackTile)" in html
    assert "differ" in html  # the two methods disagree here


def test_format_tiling_comparison_html_agrees_when_equal(qapp):
    from py2flamingo.views.workflow_view import format_tiling_comparison_html

    # Zero span -> both methods yield 1x1.
    html = format_tiling_comparison_html(
        x_min=7.0,
        x_max=7.0,
        y_min=12.0,
        y_max=12.0,
        fov_mm=2.1454,
        overlap_percent=10.0,
    )
    assert "agree" in html


def test_format_validation_result_html_shows_errors(qapp):
    from py2flamingo.views.workflow_view import format_validation_result_html

    html = format_validation_result_html(
        {"valid": False, "errors": ["Z velocity must be positive"], "warnings": []}
    )
    assert "Invalid" in html
    assert "Errors" in html
    assert "Z velocity must be positive" in html
