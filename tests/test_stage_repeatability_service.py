"""Stage repeatability orchestration + error math."""

import sys
from pathlib import Path

import numpy as np
import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from py2flamingo.services.stage_repeatability_service import (  # noqa: E402
    run_repeatability_test,
)


def test_orchestration_moves_capture_and_error_math():
    moves = []

    def move(axis, delta):
        moves.append((axis, round(delta, 6)))

    def grab():
        return np.ones((16, 16), dtype=np.uint16)

    def shift(ref, moved):
        return (2.0, 0.0, 0.9)  # 2 px in X, quality 0.9

    diffs = []
    fracs = []
    report = run_repeatability_test(
        ["x"],
        distance_mm=0.1,
        repetitions=3,
        pixel_size_um=0.5,
        move_relative=move,
        grab_frame=grab,
        settle=lambda: None,
        measure_shift=shift,
        progress=lambda _m, f: fracs.append(f),
        on_diff=lambda a, i, d: diffs.append((a, i)),
    )

    # 3 reps, each an out (+0.1) and back (-0.1).
    assert moves == [("x", 0.1), ("x", -0.1)] * 3
    axis = report.axes[0]
    assert len(axis.reps) == 3
    # 2 px * 0.5 µm/px = 1.0 µm return error.
    assert axis.reps[0].error_um == pytest.approx(1.0)
    assert axis.mean_error_um == pytest.approx(1.0)
    assert axis.max_error_um == pytest.approx(1.0)
    assert len(diffs) == 3  # one difference image per return
    assert fracs[-1] == pytest.approx(1.0)  # progress reaches 100%


def test_cancel_stops_early():
    calls = {"n": 0}

    def grab():
        return np.zeros((8, 8), dtype=np.uint16)

    def cancel():
        calls["n"] += 1
        return calls["n"] > 3  # cancel after a few checks

    report = run_repeatability_test(
        ["x", "y"],
        distance_mm=0.05,
        repetitions=10,
        pixel_size_um=1.0,
        move_relative=lambda a, d: None,
        grab_frame=grab,
        settle=lambda: None,
        measure_shift=lambda r, m: (0.0, 0.0, 1.0),
        should_cancel=cancel,
    )
    total = sum(len(a.reps) for a in report.axes)
    assert total < 20  # did not run all 2×10 reps


def test_real_shift_measurement_detects_return_offset():
    """With the real cross-correlation, a shifted return frame reads a shift."""
    rng = np.random.default_rng(0)
    ref = rng.integers(0, 4000, size=(128, 128)).astype(np.uint16)
    moved = np.roll(ref, shift=3, axis=1)  # 3 px shift along image X

    frames = iter([ref, moved])
    report = run_repeatability_test(
        ["x"],
        distance_mm=0.1,
        repetitions=1,
        pixel_size_um=0.25,
        move_relative=lambda a, d: None,
        grab_frame=lambda: next(frames),
        settle=lambda: None,
    )
    rep = report.axes[0].reps[0]
    # |shift| ≈ 3 px → ~0.75 µm; allow slack for sub-pixel estimation.
    assert rep.error_um == pytest.approx(0.75, abs=0.2)
    assert rep.quality > 0.5
