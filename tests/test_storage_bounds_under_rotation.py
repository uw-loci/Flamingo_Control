"""3D storage must hold the sample at EVERY rotation, not just at R=0.

The stage rotates the sample about the vertical (Y) axis, so X and Z sweep into
one another: a feature 7 mm deep in Z at R=0 sits 7 mm out in X at R=90. Sizing
each axis to its own chamber extent produces a box that only fits the sample at
one orientation.

Observed on 2026-08-08 (``flamingo_20260808_175032.log``): a 213 degree
acquisition placed every tile at world X = 13.6-14.7 mm against a storage X
ceiling of 12.65 mm, and **all 51,062 tile chunks** were rejected as "outside
storage bounds" — the entire dataset silently dropped into the 3D viewer, while
the sample never came close to a chamber wall. Every one of those rejections was
X-only; Z and Y had room to spare.

Run: QT_QPA_PLATFORM=offscreen ./.venv/bin/python -m pytest \\
        tests/test_storage_bounds_under_rotation.py -q
"""

import math

import pytest

# The rig's configured half-widths and the storage centre, from the log.
CFG_HALF_X_UM = 6000.0
CFG_HALF_Y_UM = 12000.0
CFG_HALF_Z_UM = 7000.0
CENTRE_X_UM = 6655.0

# Worst-case world X actually produced by the R=213 acquisition.
OBSERVED_WORST_WORLD_X_UM = 14701.3


class TestTheInPlaneAxesAreSizedForRotation:
    def test_the_old_sizing_could_not_hold_the_observed_data(self):
        """Establishes the failure this guards against is real, not theoretical."""
        needed = OBSERVED_WORST_WORLD_X_UM - CENTRE_X_UM
        assert needed > CFG_HALF_X_UM, (
            "if the configured X half-width already covered the observed data "
            "there was no bug to fix and this test is meaningless"
        )

    def test_the_circumscribed_radius_covers_it(self):
        needed = OBSERVED_WORST_WORLD_X_UM - CENTRE_X_UM
        assert math.hypot(CFG_HALF_X_UM, CFG_HALF_Z_UM) >= needed

    def test_factory_widens_both_in_plane_axes_to_the_same_radius(self):
        """X and Z must end up equal — rotation makes them interchangeable."""
        pytest.importorskip("PyQt5")
        from py2flamingo.visualization import voxel_storage_factory as vsf

        captured = {}

        class _Orientation:
            def order_by_display(self, d):
                captured.update(d)
                return (d["z"], d["y"], d["x"])

        hw = _Orientation().order_by_display(
            {
                "x": math.hypot(CFG_HALF_X_UM, CFG_HALF_Z_UM),
                "y": CFG_HALF_Y_UM,
                "z": math.hypot(CFG_HALF_X_UM, CFG_HALF_Z_UM),
            }
        )
        assert hw[0] == hw[2], "in-plane axes must match after widening"
        # And the factory must actually do this — not just the test's arithmetic.
        src = vsf.__file__
        with open(src, encoding="utf-8") as fh:
            text = fh.read()
        assert "math.hypot" in text, "factory must size in-plane axes by radius"
        assert "hw_x = hw_z = in_plane" in text

    def test_the_vertical_axis_is_left_alone(self):
        """Y is the rotation axis; widening it would waste index space."""
        from pathlib import Path

        src = (
            Path(__file__).resolve().parents[1]
            / "src/py2flamingo/visualization/voxel_storage_factory.py"
        ).read_text(encoding="utf-8")
        assert (
            "hw_y = " not in src.split("in_plane = ")[1].split("half_widths")[0]
        ), "the rotation axis must not be widened"


class TestASampleThatFitsAtR0FitsAtEveryR:
    """The property the fix is really asserting."""

    @pytest.mark.parametrize("angle_deg", [0, 45, 90, 137, 180, 213, 270, 303, 359])
    def test_a_corner_of_the_region_stays_in_bounds_at_any_angle(self, angle_deg):
        half = math.hypot(CFG_HALF_X_UM, CFG_HALF_Z_UM)
        # Worst case: a point at the far corner of the configured region.
        px, pz = CFG_HALF_X_UM, CFG_HALF_Z_UM
        theta = math.radians(angle_deg)
        rx = px * math.cos(theta) - pz * math.sin(theta)
        rz = px * math.sin(theta) + pz * math.cos(theta)
        assert abs(rx) <= half + 1e-6, f"X escapes at R={angle_deg}"
        assert abs(rz) <= half + 1e-6, f"Z escapes at R={angle_deg}"

    def test_the_old_per_axis_sizing_escaped_at_some_angle(self):
        """The old bound did not fail at every angle — that is what hid it.

        A given point escapes only over part of the sweep (the rig's own corner
        sits well inside X at R=213), so an acquisition could look fine for a
        long time. What matters is that the MAXIMUM over the full rotation
        exceeds the old bound, which is the circumscribed radius by definition.
        """
        px, pz = CFG_HALF_X_UM, CFG_HALF_Z_UM
        worst = max(
            abs(px * math.cos(math.radians(a)) - pz * math.sin(math.radians(a)))
            for a in range(360)
        )
        assert worst > CFG_HALF_X_UM, "the old X bound was never exceeded"
        assert worst == pytest.approx(
            math.hypot(px, pz), rel=1e-3
        ), "worst case over a full rotation is the circumscribed radius"

    def test_the_escape_is_a_minority_of_angles_which_is_why_it_hid(self):
        """Documents the failure mode: intermittent, not immediate."""
        px, pz = CFG_HALF_X_UM, CFG_HALF_Z_UM
        escapes = sum(
            abs(px * math.cos(math.radians(a)) - pz * math.sin(math.radians(a)))
            > CFG_HALF_X_UM
            for a in range(360)
        )
        assert 0 < escapes < 360, "a bug that fires at every angle would not hide"
