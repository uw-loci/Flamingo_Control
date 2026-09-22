"""A real 704-plane stack where 540 frames were copies of the last plane.

CTLSM1, 2026-09. The server's own summary reported success throughout --
704 requested, 704 acquired, 704 processed, 704 saved, 0 errors -- while the
stage had finished its entire 0.704 mm path after about 163 frames and parked
there for the remaining 541. The stage position column in the acquisition log
stops changing at index 161 and holds 12.8970296 to the end.

The cause is in the workflow itself and needed no imaging to find:

    <Experiment Settings> Frame rate (f/s) = 40.213    <- non-light-sheet ceiling
    <Camera Settings>     Frame rate (f/s) =           <- blank, camera kept ASLM
    read out time (us)    = 90,857                     <- 11.006 fps ceiling

40.213 fps against a camera that can produce 11.006. These tests pin that
arithmetic against the real numbers so the guard cannot quietly stop working.
"""

import pytest

from py2flamingo.models.frame_delivery import check_sweep_feasible

# Every number below is from the acquisition log of that run.
READOUT_US = 90857.0
MEASURED_PERIOD_US = 107111.0  # "acquisition time mean (us)"
PLANES = 704
Z_VELOCITY = 0.040213  # mm/s, in both the workflow and the server summary
PLANE_SPACING_UM = 1.0
REQUESTED_FPS = 40.213
OBSERVED_PARK_INDEX = 161  # where the logged Z stops changing


def real_stack(**kwargs):
    params = dict(
        readout_us=READOUT_US,
        z_velocity_mm_s=Z_VELOCITY,
        plane_spacing_um=PLANE_SPACING_UM,
        planes=PLANES,
    )
    params.update(kwargs)
    return check_sweep_feasible(**params)


class TestTheRealStack:
    def test_the_requested_rate_is_recovered_from_the_stage_speed(self):
        """Derived from velocity/spacing, not read from a field that may lie."""
        assert real_stack().requested_fps == pytest.approx(REQUESTED_FPS, rel=1e-4)

    def test_readout_time_recovers_the_aslm_line_time(self):
        """90.857 ms over 2048 rows is the 44364 ns line time, independently."""
        assert READOUT_US / 2048 * 1000 == pytest.approx(44364, rel=1e-3)

    def test_the_camera_ceiling_is_eleven_fps(self):
        assert real_stack().max_fps == pytest.approx(11.006, abs=0.01)

    def test_the_request_is_not_feasible(self):
        assert not real_stack().feasible

    def test_overspeed_is_three_and_a_half_times(self):
        assert real_stack().overspeed_factor == pytest.approx(3.65, abs=0.02)

    def test_predicted_park_point_matches_the_logged_one(self):
        """With the measured period the prediction lands on the observed index.

        The readout floor alone predicts 193; the real period carries queueing
        and save overhead on top, which is why the floor is the optimistic
        bound and a configuration failing against it fails harder in practice.
        """
        predicted = real_stack(
            measured_frame_period_us=MEASURED_PERIOD_US
        ).frames_before_park
        assert predicted == pytest.approx(OBSERVED_PARK_INDEX, abs=5)

    def test_the_floor_is_the_optimistic_bound(self):
        floor = real_stack().frames_before_park
        real = real_stack(
            measured_frame_period_us=MEASURED_PERIOD_US
        ).frames_before_park
        assert floor > real  # the floor flatters the configuration

    def test_most_of_the_stack_was_duplicates(self):
        assert real_stack(
            measured_frame_period_us=MEASURED_PERIOD_US
        ).duplicate_frames == pytest.approx(541, abs=5)

    def test_the_real_plane_spacing_was_four_times_what_was_asked(self):
        actual = real_stack(
            measured_frame_period_us=MEASURED_PERIOD_US
        ).real_plane_spacing_um
        assert actual == pytest.approx(4.31, abs=0.05)


class TestTheGuard:
    def test_a_rate_at_the_ceiling_is_allowed(self):
        assert real_stack(z_velocity_mm_s=0.011006).feasible

    def test_a_rate_just_over_the_ceiling_is_not_silently_tolerated(self):
        assert not real_stack(z_velocity_mm_s=0.0115).feasible

    def test_a_feasible_sweep_predicts_no_duplicates(self):
        assert real_stack(z_velocity_mm_s=0.011).duplicate_frames == 0

    def test_a_stopped_stage_does_not_divide_by_zero(self):
        assert real_stack(z_velocity_mm_s=0.0).frames_before_park == PLANES

    def test_zero_spacing_does_not_divide_by_zero(self):
        assert real_stack(plane_spacing_um=0.0).requested_fps == 0.0

    def test_free_running_readout_leaves_forty_fps_feasible(self):
        """The same workflow is fine when the camera is NOT in light-sheet mode.

        2048 rows at the free-running 12.207 us/row is a 25 ms readout, so
        40.213 fps was a correct number for the mode the workflow was written
        for. It became wrong when the camera kept its ASLM timing.
        """
        assert real_stack(readout_us=25000.0).feasible


class TestValidatorIntegration:
    def _validate(self, **constraint_kwargs):
        from py2flamingo.models.data.workflow import StackSettings
        from py2flamingo.workflows.workflow_validator import (
            HardwareConstraints,
            WorkflowValidator,
        )

        class Result:
            def __init__(self):
                self.errors = []
                self.warnings = []

            def add_error(self, message):
                self.errors.append(message)

            def add_warning(self, message):
                self.warnings.append(message)

        validator = WorkflowValidator(HardwareConstraints(**constraint_kwargs))
        stack = StackSettings(
            num_planes=PLANES,
            z_step_um=PLANE_SPACING_UM,
            z_velocity_mm_s=Z_VELOCITY,
        )
        result = Result()
        validator._validate_sweep_speed(stack, result)
        return result

    def test_the_real_workflow_is_rejected(self):
        errors = self._validate(camera_readout_us=READOUT_US).errors
        assert len(errors) == 1
        assert "Stage outruns the camera" in errors[0]

    def test_the_error_says_what_it_would_cost(self):
        """An error naming only the rate does not convey losing 500 planes."""
        message = self._validate(camera_readout_us=READOUT_US).errors[0]
        assert "40.21 fps" in message
        assert "11.01 fps" in message
        assert "704" in message
        assert "duplicates" in message

    def test_an_unknown_readout_time_skips_the_check_rather_than_guessing(self):
        """In light-sheet mode readout is a knob; a stale guess is the disease.

        Checked with a sweep so fast it would fail against ANY plausible
        default readout, so this distinguishes "skipped" from "silently
        evaluated against an assumed value that happened to pass".
        """
        from py2flamingo.models.data.workflow import StackSettings
        from py2flamingo.workflows.workflow_validator import (
            HardwareConstraints,
            WorkflowValidator,
        )

        class Result:
            def __init__(self):
                self.errors = []
                self.warnings = []

            def add_error(self, message):
                self.errors.append(message)

            def add_warning(self, message):
                self.warnings.append(message)

        validator = WorkflowValidator(HardwareConstraints(camera_readout_us=None))
        # 0.2 mm/s at 1 um spacing implies 200 fps -- impossible at any readout.
        stack = StackSettings(num_planes=PLANES, z_step_um=1.0, z_velocity_mm_s=0.2)
        result = Result()
        validator._validate_sweep_speed(stack, result)
        assert result.errors == []

    def test_a_nonsense_readout_time_is_not_trusted(self):
        assert self._validate(camera_readout_us=0.0).errors == []


class TestTimingModelWarning:
    def _warnings(self, configured_fps):
        import py2flamingo.models.aslm_timing as aslm

        result = aslm.from_light_sheet_settings(
            line_time_ns=44364,
            exposure_lines=128,
            rows=2048,
            plane_spacing_um=PLANE_SPACING_UM,
            z_range_mm=0.704,
            pixel_size_um=0.253,
            configured_frame_rate_hz=configured_fps,
        )
        return result.warnings

    def test_asking_above_the_ceiling_warns(self):
        assert any("3.7x too fast" in w for w in self._warnings(40.213))

    def test_the_warning_names_the_parked_planes(self):
        warning = next(w for w in self._warnings(40.213) if "too fast" in w)
        assert "193 of 704" in warning
        assert "same plane repeated" in warning

    def test_asking_at_the_ceiling_does_not_warn_about_speed(self):
        assert not any("too fast" in w for w in self._warnings(11.006))

    def test_the_opposite_direction_still_warns_separately(self):
        """Configured BELOW the ceiling is the older, milder warning."""
        assert any("Do not raise" in w for w in self._warnings(5.0))


# A second stack from the same rig, and the one that settles what is actually
# pacing it. Same requested rate, same stage velocity, 3.5x different readout --
# and an identical delivered cadence.
STACK2_PLANES = 1382
STACK2_READOUT_US = 26000.0  # free-running, NOT the ASLM 90.857 ms
STACK2_CYCLE_US = 150_679_365 / STACK2_PLANES  # 109.03 ms
STACK2_OBSERVED_PARK = 317
STACK1_CYCLE_US = 76_839_838 / PLANES  # 109.15 ms


class TestReadoutIsNotWhatPacesTheRig:
    """The readout explanation fitted stack 1 and is refuted by stack 2.

    Stack 1's 90.857 ms ASLM readout looked like a complete account of its
    107 ms frames. Stack 2 ran the same workflow with readout 3.5x shorter and
    was delivered at the same 109 ms, so the cadence is set by something
    outside the camera's readout entirely -- an external trigger being the
    obvious candidate, checkable with TRIGGER_MODE_GET (0x300E).
    """

    def test_the_two_stacks_have_the_same_delivered_cadence(self):
        assert STACK1_CYCLE_US == pytest.approx(STACK2_CYCLE_US, rel=0.01)

    def test_their_readout_times_differ_by_more_than_three_times(self):
        assert READOUT_US / STACK2_READOUT_US > 3.0

    def test_stack_two_park_point_is_predicted_by_the_measured_cadence(self):
        predicted = check_sweep_feasible(
            readout_us=STACK2_READOUT_US,
            z_velocity_mm_s=Z_VELOCITY,
            plane_spacing_um=PLANE_SPACING_UM,
            planes=STACK2_PLANES,
            measured_frame_period_us=STACK2_CYCLE_US,
        ).frames_before_park
        assert predicted == pytest.approx(STACK2_OBSERVED_PARK, abs=5)

    def test_readout_alone_understates_stack_two_enormously(self):
        """4.6% over the readout ceiling, 77% of the stack duplicated.

        This is why a readout-only verdict may never be reported as an
        estimate: it predicts tens of duplicate frames where there were over a
        thousand.
        """
        weak = check_sweep_feasible(
            readout_us=STACK2_READOUT_US,
            z_velocity_mm_s=Z_VELOCITY,
            plane_spacing_um=PLANE_SPACING_UM,
            planes=STACK2_PLANES,
        )
        measured = check_sweep_feasible(
            readout_us=STACK2_READOUT_US,
            z_velocity_mm_s=Z_VELOCITY,
            plane_spacing_um=PLANE_SPACING_UM,
            planes=STACK2_PLANES,
            measured_frame_period_us=STACK2_CYCLE_US,
        )
        assert measured.duplicate_frames == pytest.approx(1065, abs=10)
        assert weak.duplicate_frames < measured.duplicate_frames / 10

    def test_a_readout_only_verdict_is_marked_as_weak(self):
        assert real_stack().bound_is_weak
        assert not real_stack(measured_frame_period_us=STACK1_CYCLE_US).bound_is_weak

    def test_the_weak_bound_is_disclosed_in_the_validator_message(self):
        from py2flamingo.models.data.workflow import StackSettings
        from py2flamingo.workflows.workflow_validator import (
            HardwareConstraints,
            WorkflowValidator,
        )

        class Result:
            def __init__(self):
                self.errors = []
                self.warnings = []

            def add_error(self, message):
                self.errors.append(message)

            def add_warning(self, message):
                self.warnings.append(message)

        stack = StackSettings(
            num_planes=STACK2_PLANES, z_step_um=1.0, z_velocity_mm_s=Z_VELOCITY
        )

        weak = Result()
        WorkflowValidator(
            HardwareConstraints(camera_readout_us=STACK2_READOUT_US)
        )._validate_sweep_speed(stack, weak)
        assert "floor on how wrong" in weak.errors[0]

        measured = Result()
        WorkflowValidator(
            HardwareConstraints(
                camera_readout_us=STACK2_READOUT_US,
                camera_frame_period_us=STACK2_CYCLE_US,
            )
        )._validate_sweep_speed(stack, measured)
        assert "floor on how wrong" not in measured.errors[0]
        # Names the cadence that actually applies, not the readout ceiling.
        assert "measured 109.0 ms cadence" in measured.errors[0]
        assert "9.17 fps" in measured.errors[0]
        assert "38.46" not in measured.errors[0]
        # Predicted duplicates land on the 1065 actually observed.
        assert "1067" in measured.errors[0]

    def test_the_measured_cadence_predicts_both_stacks(self):
        """One number, ~109 ms, accounts for both runs to within two frames."""
        for planes, park in (
            (PLANES, OBSERVED_PARK_INDEX),
            (STACK2_PLANES, STACK2_OBSERVED_PARK),
        ):
            predicted = check_sweep_feasible(
                readout_us=READOUT_US,
                z_velocity_mm_s=Z_VELOCITY,
                plane_spacing_um=PLANE_SPACING_UM,
                planes=planes,
                measured_frame_period_us=109_090.0,
            ).frames_before_park
            assert predicted == pytest.approx(park, abs=3)


class TestTheVelocityFloorDoesNotBlockTheFix:
    """The correct velocity for these stacks must not be rejected as too slow.

    `velocity = plane_spacing x frame_rate`, so a floor on velocity is a floor
    on frame rate. Ours was 0.01 mm/s, i.e. 10 fps at 1 um spacing -- above the
    9.17 fps this rig actually delivers. The 4.4x-too-fast velocity passed that
    check and the correct one would have been refused.
    """

    CORRECT_VELOCITY = 0.00917  # 1 um x 9.17 fps
    SERVER_FLOOR = 0.000001  # Teensy SystemLimits.h:13

    def test_the_correct_velocity_is_accepted(self):
        from py2flamingo.workflows.workflow_validator import HardwareConstraints

        assert self.CORRECT_VELOCITY >= HardwareConstraints().min_z_velocity_mm_s

    def test_our_floor_is_not_stricter_than_the_server(self):
        from py2flamingo.workflows.workflow_validator import HardwareConstraints

        assert HardwareConstraints().min_z_velocity_mm_s <= self.SERVER_FLOOR

    def test_the_gui_does_not_clamp_it_away(self):
        from py2flamingo.views.workflow_panels.zstack_panel import Z_VELOCITY_MIN_MM_S

        assert self.CORRECT_VELOCITY >= Z_VELOCITY_MIN_MM_S

    def test_a_genuinely_stopped_stage_is_still_rejected(self):
        """Lowering the floor must not let a zero velocity through.

        It does not: StackSettings validates on construction, well before the
        validator's floor is consulted.
        """
        import pytest as _pytest

        from py2flamingo.models.base import ValidationError
        from py2flamingo.models.data.workflow import StackSettings

        with _pytest.raises(ValidationError):
            StackSettings(num_planes=10, z_step_um=1.0, z_velocity_mm_s=0.0)


# A third stack, and the one that shows the cadence is QUANTISED to the clock.
#
# Different spacing (5 um), different requested rate (6.972 fps), different
# camera timing (152 ms) -- and a delivered cadence of 218.17 ms, which is
# 1.9999x the 109.09 ms of the first two. The camera needs 152 ms, the clock
# ticks every 109 ms, so it misses an edge and fires on every second one. A
# continuous bottleneck does not land on an integer multiple; a camera waiting
# for trigger edges does exactly this.
STACK3_PLANES = 276
STACK3_CYCLE_US = 60_214_108 / STACK3_PLANES  # 218.17 ms
STACK3_VELOCITY = 0.034860  # server-computed: 5 um x 6.972 fps
STACK3_SPACING_UM = 5.0
STACK3_OBSERVED_PARK = 183
CLOCK_PERIOD_US = 109_090.0


class TestTheCadenceIsQuantisedToTheClock:
    def test_the_third_stack_runs_at_exactly_twice_the_clock(self):
        assert STACK3_CYCLE_US / CLOCK_PERIOD_US == pytest.approx(2.0, abs=0.005)

    def test_the_first_two_stacks_run_at_exactly_one_clock_period(self):
        for cycle in (STACK1_CYCLE_US, STACK2_CYCLE_US):
            assert cycle / CLOCK_PERIOD_US == pytest.approx(1.0, abs=0.005)

    def test_the_park_point_is_predicted_at_the_doubled_cadence(self):
        predicted = check_sweep_feasible(
            readout_us=152_000.0,
            z_velocity_mm_s=STACK3_VELOCITY,
            plane_spacing_um=STACK3_SPACING_UM,
            planes=STACK3_PLANES,
            measured_frame_period_us=STACK3_CYCLE_US,
        ).frames_before_park
        assert predicted == pytest.approx(STACK3_OBSERVED_PARK, abs=3)

    def test_lowering_the_requested_rate_did_not_fix_it(self):
        """6.972 fps was already a big reduction from 40.213 and still failed.

        It was derived from the camera's readout (143 ms -> 6.99 fps), and the
        camera is not what sets the rate. Against the delivered 4.584 fps it is
        still 1.5x too fast, which is why a third of the stack is duplicates.
        """
        feasibility = check_sweep_feasible(
            readout_us=152_000.0,
            z_velocity_mm_s=STACK3_VELOCITY,
            plane_spacing_um=STACK3_SPACING_UM,
            planes=STACK3_PLANES,
            measured_frame_period_us=STACK3_CYCLE_US,
        )
        assert not feasibility.feasible
        assert feasibility.overspeed_factor == pytest.approx(1.52, abs=0.03)
        assert feasibility.duplicate_frames == pytest.approx(94, abs=3)

    def test_the_camera_readout_would_have_passed_this_one(self):
        """The readout bound calls a 34%-duplicate stack acceptable.

        152 ms readout implies 6.58 fps against 6.972 requested -- 6% over,
        inside no useful margin. Only the measured cadence catches it.
        """
        weak = check_sweep_feasible(
            readout_us=152_000.0,
            z_velocity_mm_s=STACK3_VELOCITY,
            plane_spacing_um=STACK3_SPACING_UM,
            planes=STACK3_PLANES,
        )
        assert weak.duplicate_frames < 20  # vs 93 actually observed

    def test_the_correct_velocity_for_this_stack(self):
        """spacing x delivered rate, and our floor must accept it."""
        from py2flamingo.workflows.workflow_validator import HardwareConstraints

        correct = STACK3_SPACING_UM / 1000.0 * (1e6 / STACK3_CYCLE_US)
        assert correct == pytest.approx(0.02292, abs=0.0002)
        assert correct >= HardwareConstraints().min_z_velocity_mm_s
        feasibility = check_sweep_feasible(
            readout_us=152_000.0,
            z_velocity_mm_s=correct,
            plane_spacing_um=STACK3_SPACING_UM,
            planes=STACK3_PLANES,
            measured_frame_period_us=STACK3_CYCLE_US,
        )
        assert feasibility.feasible
        assert feasibility.duplicate_frames == 0

    def test_all_three_stacks_predicted_by_their_own_cadence(self):
        cases = [
            (PLANES, Z_VELOCITY, 1.0, STACK1_CYCLE_US, OBSERVED_PARK_INDEX),
            (STACK2_PLANES, Z_VELOCITY, 1.0, STACK2_CYCLE_US, STACK2_OBSERVED_PARK),
            (
                STACK3_PLANES,
                STACK3_VELOCITY,
                STACK3_SPACING_UM,
                STACK3_CYCLE_US,
                STACK3_OBSERVED_PARK,
            ),
        ]
        for planes, velocity, spacing, cycle, observed in cases:
            predicted = check_sweep_feasible(
                readout_us=90857.0,
                z_velocity_mm_s=velocity,
                plane_spacing_um=spacing,
                planes=planes,
                measured_frame_period_us=cycle,
            ).frames_before_park
            assert predicted == pytest.approx(observed, abs=3)
