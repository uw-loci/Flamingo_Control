"""The post-update verification tool, run end to end against the synthetic scope.

The synthetic microscope answers the way the server source says (which code
replies, which field carries what, HALT ignoring axis 0, the ROI revert, the
exposure labels a workflow is read with). So the run here is the run the rig
will see, apart from the hardware itself, and two checks FAIL by design: they
measure suspected client defects (``t1.roi``: the client sends no right/bottom
ROI edge; ``t4.workflow``: the server does not read the "Exposure time (us)"
label the client writes). When those are fixed these expectations flip.

Fault injection below proves that a check can fail: a verifier that only
ever says PASS has not been tested.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from py2flamingo.core import server_layout as sl
from py2flamingo.core.tcp_connection import TCPConnection
from py2flamingo.core.tcp_protocol import ProtocolEncoder
from py2flamingo.models.connection import ConnectionConfig
from py2flamingo.services.connection_service import MVCConnectionService
from py2flamingo.testing import synthetic_microscope as synth
from py2flamingo.verification import report, session
from py2flamingo.verification.context import RunOptions
from py2flamingo.verification.model import Outcome, Tier
from py2flamingo.verification.runner import RunCallbacks

REPO = Path(__file__).resolve().parents[1]

# Checks that fail on purpose against a faithful server (see module docstring).
# t4.acquisition_files: the app's illumination reader knows only the pre-10-06
# keys ("Left path = ON 1", not "Left path recording = true"). The stitcher's
# name and objective readers handle SystemMetaData.txt from flamingo-stitcher
# 0.14.1 on.
KNOWN_CLIENT_DEFECTS = {"t1.roi", "t4.workflow", "t4.acquisition_files"}


def _adjacent_ports():
    """A free port p with p + 1 also free (the app opens live at port + 1)."""
    for _ in range(50):
        with socket.socket() as a:
            a.bind(("127.0.0.1", 0))
            p = a.getsockname()[1]
        try:
            with socket.socket() as b, socket.socket() as c:
                b.bind(("127.0.0.1", p))
                c.bind(("127.0.0.1", p + 1))
            return p
        except OSError:
            continue
    raise RuntimeError("no adjacent free ports")


class VerificationTestCase(unittest.TestCase):
    layout = sl.REFERENCE

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self.addCleanup(os.chdir, cwd)
        self.addCleanup(sl.clear_active)

        settings = Path("microscope_settings")
        settings.mkdir()
        limits = {
            a: {"min": lo, "max": hi} for a, (lo, hi) in synth.STAGE_LIMITS.items()
        }
        (settings / "synthetic_settings.json").write_text(
            json.dumps(
                {
                    "microscope_name": "synthetic",
                    "stage_limits": limits,
                    "verification_targets": [
                        {
                            "name": "synthetic sample",
                            "x": 6.0,
                            "y": 12.0,
                            "z": 19.0,
                            "r": 0.0,
                        }
                    ],
                }
            )
        )
        lasers = "".join(
            f"  Laser {i} wave length (nm) = {w}\n  Laser {i} max power (mw) = 20\n"
            for i, w in synth.SYNTHETIC_LASERS.items()
        )
        attached = "".join(
            f"  Attached laser line {i} = 1\n" for i in synth.SYNTHETIC_LASERS
        )
        attached += "  Attached LED = 1\n"
        (settings / "ControlSettings.txt").write_text(
            "<Laser parameters>\n" + lasers + "  Laser lines = 4\n</Laser parameters>\n"
            "<Attached devices>\n" + attached + "</Attached devices>\n"
        )
        Path("workflows").mkdir()
        shutil.copy(REPO / "workflows" / "ZStack.txt", "workflows/ZStack.txt")

        port = _adjacent_ports()
        # The server's save drive, as this computer sees it.
        self.data_root = Path(self.tmp.name) / "server_drive"
        self.data_root.mkdir()
        self.scope = synth.SyntheticMicroscope(
            command_port=port,
            live_port=port + 1,
            layout=self.layout,
            data_root=str(self.data_root),
        )
        self.scope.start()
        self.addCleanup(self.scope.stop)
        self.connection = MVCConnectionService(TCPConnection(), ProtocolEncoder())
        self.connection.connect(ConnectionConfig("127.0.0.1", port, port + 1))
        self.addCleanup(
            lambda: self.connection.is_connected() and self.connection.disconnect()
        )

    def run_tiers(
        self, top=Tier.ACQUISITION, abort=None, callbacks=None, area=None, **opts
    ):
        prepared = session.prepare(self.connection, base_path=Path.cwd())
        target = session.target_for(prepared, None, self.scope.stage_position())
        opts.setdefault("local_data_root", str(self.data_root))
        options = RunOptions(sample="synthetic sample", live_frames=8, **opts)
        summary, folder = session.execute(
            prepared,
            [top],
            options,
            target=target,
            report_root=Path("verification"),
            confirmations=["test"],
            abort=abort,
            callbacks=callbacks,
            area=area,
        )
        return summary, folder, {r.check_id: r for r in summary.results}


class TestFullRun(VerificationTestCase):
    layout = sl.LAYOUT_2026_11_13

    def test_every_check_runs_and_only_the_known_defects_fail(self):
        summary, folder, by_id = self.run_tiers()
        problems = {cid for cid, r in by_id.items() if r.outcome.is_problem}
        self.assertEqual(
            problems,
            KNOWN_CLIENT_DEFECTS,
            "\n".join(
                f"{r.check_id}: {r.outcome.value} {r.detail}" for r in summary.results
            ),
        )
        self.assertEqual(by_id["t3.illumination_paths"].outcome, Outcome.ACK_ONLY)
        self.assertEqual(by_id["t0.binning"].outcome, Outcome.PASS)
        self.assertIn("status 2", by_id["t1.roi"].detail)
        self.assertIn("exposure", by_id["t4.workflow"].detail)
        files = by_id["t4.acquisition_files"].detail
        self.assertIn("illumination as left=False", files)
        # flamingo-stitcher 0.14.1 reads SystemMetaData.txt: name and objective found.
        self.assertNotIn("no microscope name", files)
        self.assertNotIn("no objective", files)
        for cid in (
            "t1.binning",
            "t1.exposure_set",
            "t3.filter_wheel",
            "t0.server_version",
        ):
            self.assertEqual(
                by_id[cid].outcome, Outcome.PASS, f"{cid}: {by_id[cid].detail}"
            )
        self.assertEqual(by_id["t9.board_notices"].outcome, Outcome.PASS)
        # Settings go back after their own check, not at the end of the run.
        ids = [r.check_id for r in summary.results]
        self.assertLess(
            ids.index("restore.binning 1x1 and ROI"), ids.index("t1.laser_level")
        )
        # Restores all ran and passed; the stage is back where it started.
        restores = [r for r in summary.results if r.check_id.startswith("restore.")]
        self.assertTrue(restores)
        self.assertTrue(all(r.outcome == Outcome.PASS for r in restores), restores)
        pos = self.scope.stage_position()
        self.assertAlmostEqual(pos["x"], 6.0, places=3)
        self.assertAlmostEqual(pos["r"], 0.0, places=3)
        # The report folder is complete.
        for name in ("report.txt", "report.json", "exchange.log"):
            self.assertTrue((folder / name).exists(), name)
        text = (folder / "report.txt").read_text()
        self.assertIn("PROBLEMS", text)
        self.assertIn("2026-11-13", text)
        self.assertTrue(list(folder.glob("target_*")))

    def test_the_emergency_halt_names_all_axes(self):
        self.scope.clear_received()
        _, _, by_id = self.run_tiers(top=Tier.MOTION)
        self.assertEqual(
            by_id["t2.halt"].outcome, Outcome.PASS, by_id["t2.halt"].detail
        )
        halts = self.scope.received(0x6003)
        self.assertTrue(halts and all(h.axis == 0xFF for h in halts))


class TestOlderBuilds(VerificationTestCase):
    def test_3_0_0_runs_read_only_and_skips_what_it_lacks(self):
        _, _, by_id = self.run_tiers(top=Tier.READ_ONLY)
        self.assertEqual(by_id["t0.guard"].outcome, Outcome.SKIPPED)
        self.assertEqual(by_id["t0.binning"].outcome, Outcome.SKIPPED)
        for cid in (
            "t0.connect",
            "t0.settings",
            "t0.state",
            "t0.positions",
            "t0.camera_size",
        ):
            self.assertEqual(
                by_id[cid].outcome, Outcome.PASS, f"{cid}: {by_id[cid].detail}"
            )


class TestThe2026_10_06Build(VerificationTestCase):
    layout = sl.LAYOUT_2026_10_06

    def test_the_guard_refuses_and_settings_come_from_the_document(self):
        _, _, by_id = self.run_tiers(top=Tier.READ_ONLY)
        self.assertEqual(by_id["t0.guard"].outcome, Outcome.PASS)
        self.assertEqual(by_id["t0.settings"].outcome, Outcome.PASS)
        self.assertEqual(
            by_id["t0.lasers"].outcome, Outcome.PASS, by_id["t0.lasers"].detail
        )


class TestFaultsAreCaught(VerificationTestCase):
    """Each fault must turn the matching check into a FAIL."""

    layout = sl.LAYOUT_2026_11_13

    def test_a_halt_that_does_nothing(self):
        original = self.scope._handlers

        def handlers():
            table = original()
            table[0x6003] = lambda client, code, params, value, additional: None
            return table

        self.scope._handlers = handlers
        _, _, by_id = self.run_tiers(top=Tier.MOTION)
        self.assertEqual(by_id["t2.halt"].outcome, Outcome.FAIL)

    def test_a_stage_that_stops_short(self):
        original = self.scope.stage.start_move

        def short(axis, target):
            original(axis, target - 0.05 if axis == "y" else target)

        self.scope.stage.start_move = short
        _, _, by_id = self.run_tiers(top=Tier.MOTION)
        self.assertEqual(by_id["t2.move_y"].outcome, Outcome.FAIL)
        self.assertEqual(by_id["t2.move_x"].outcome, Outcome.PASS)

    def test_an_led_that_does_not_light(self):
        self.scope._on_led_set = (
            lambda client, code, params, value, additional: self.scope._reply(
                client, code
            )
        )
        original = self.scope._handlers

        def handlers():
            table = original()
            table[0x4001] = self.scope._on_led_set
            return table

        self.scope._handlers = handlers
        _, _, by_id = self.run_tiers()
        self.assertEqual(by_id["t4.led_brightness"].outcome, Outcome.FAIL)

    def test_an_led_that_stays_dark_below_25_percent(self):
        # The default test level sits above the threshold; 10% would not light.
        self.scope.led_min_percent = 25.0
        _, _, by_id = self.run_tiers()
        self.assertEqual(by_id["t4.led_brightness"].outcome, Outcome.PASS)
        self.assertIn("mean_30pct", by_id["t4.led_brightness"].values)
        _, _, by_id = self.run_tiers(led_test_percent=10.0)
        self.assertEqual(by_id["t4.led_brightness"].outcome, Outcome.FAIL)
        self.assertIn("below about 25%", by_id["t4.led_brightness"].detail)

    def test_an_led_that_lights_only_at_twice_the_test_level(self):
        # CTLSM1, 2026-11-20: off 99.4, 30% 99.5, 60% 3146.
        self.scope.led_min_percent = 40.0
        self.scope.clear_received()
        _, _, by_id = self.run_tiers()
        result = by_id["t4.led_brightness"]
        self.assertEqual(result.outcome, Outcome.PASS, result.detail)
        self.assertIn("lights at 60%", result.detail)
        self.assertEqual(by_id["t4.target_image"].outcome, Outcome.PASS)
        # The target image was taken at the level that lit.
        levels = [c.params[4] for c in self.scope.received(0x4001)]
        self.assertEqual(levels[-1], 60, levels)

    def test_a_settings_json_for_another_stage_stops_the_motion_checks(self):
        # CTLSM1, 2026-11-20: the stage sat at z 18.73 while the json (shared
        # by two rigs called 'princess') said z 8..15.
        path = Path("microscope_settings/synthetic_settings.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["stage_limits"]["z"] = {"min": 8.0, "max": 15.0}
        data["verification_targets"] = []
        path.write_text(json.dumps(data), encoding="utf-8")
        self.scope.clear_received()
        _, _, by_id = self.run_tiers(top=Tier.MOTION)
        self.assertEqual(by_id["t0.positions"].outcome, Outcome.FAIL)
        self.assertIn("does not match this stage", by_id["t0.positions"].detail)
        self.assertEqual(by_id["t2.move_x"].outcome, Outcome.BLOCKED)
        moves = [0x6004, 0x6005, 0x6006]
        self.assertEqual([c for c in self.scope.received() if c.code in moves], [])

    def test_the_server_soft_limits_narrow_a_wider_settings_json(self):
        # CTLSM1, 2026-11-20: the json allowed y from 1 and z from 8, the
        # server's soft limits start at y 5 and z 11.
        path = Path("microscope_settings/synthetic_settings.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["stage_limits"]["y"] = {"min": 1.0, "max": 30.0}
        path.write_text(json.dumps(data), encoding="utf-8")
        prepared = session.prepare(self.connection, base_path=Path.cwd())
        lo, hi = synth.STAGE_LIMITS["y"]
        self.assertEqual(prepared.rig.envelope.limits["y"], (lo, hi))
        self.assertTrue(
            any("y min 1 " in n and "soft limits" in n for n in prepared.notes),
            prepared.notes,
        )
        self.assertIn("soft limits", prepared.rig.envelope.describe())
        self.assertTrue(prepared.rig.envelope.problems("y", lo - 0.5, {"y": lo + 1.0}))

    def test_a_stage_near_its_maximum_steps_the_other_way(self):
        # z 19.0 with max 19.2: +0.1 would pass the 0.2 margin, -0.1 does not.
        path = Path("microscope_settings/synthetic_settings.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["stage_limits"]["z"] = {"min": 12.5, "max": 19.2}
        data["verification_targets"] = []
        path.write_text(json.dumps(data), encoding="utf-8")
        self.scope.clear_received()
        _, _, by_id = self.run_tiers(top=Tier.MOTION)
        self.assertEqual(
            by_id["t2.move_z"].outcome, Outcome.PASS, by_id["t2.move_z"].detail
        )
        z_moves = [c.value for c in self.scope.received(0x6004) if c.params[3] == 3]
        self.assertTrue(z_moves and min(z_moves) < 19.0 - 0.05, z_moves)
        self.assertTrue(all(v <= 19.0 + 1e-6 for v in z_moves), z_moves)

    def test_a_server_that_is_not_idle_blocks_the_changes(self):
        self.scope.live_view = True
        _, _, by_id = self.run_tiers(top=Tier.SETTINGS)
        self.assertEqual(by_id["t0.state"].outcome, Outcome.FAIL)
        self.assertEqual(by_id["t1.trigger_mode"].outcome, Outcome.BLOCKED)

    def test_abort_sends_the_safe_state_and_does_not_move_back(self):
        abort = threading.Event()

        def started(i, n, chk):
            if chk.check_id == "t2.move_z":
                abort.set()

        self.scope.clear_received()
        summary, _, by_id = self.run_tiers(
            top=Tier.ACQUISITION,
            abort=abort,
            callbacks=RunCallbacks(check_started=started),
        )
        self.assertTrue(summary.aborted)
        self.assertEqual(by_id["t4.workflow"].outcome, Outcome.SKIPPED)
        halts = [h for h in self.scope.received(0x6003) if h.axis == 0xFF]
        self.assertTrue(halts, "the safe state must halt every axis")
        # Restores of checks done before the abort ran then; any stage restore
        # after the abort is reported and not run.
        ids = [r.check_id for r in summary.results]
        after_abort = summary.results[ids.index("t2.move_z") :]
        moved_back = [
            r
            for r in after_abort
            if r.check_id.startswith("restore.") and "back" in r.check_id
        ]
        self.assertTrue(moved_back, "the run-end stage return is still reported")
        self.assertTrue(
            all(r.outcome == Outcome.SKIPPED for r in moved_back), moved_back
        )


class TestCompare(unittest.TestCase):
    def test_a_new_failure_and_a_changed_value_are_reported(self):
        before = {
            "started": "a",
            "command_layout": "x",
            "results": [
                {
                    "id": "t0.a",
                    "outcome": "PASS",
                    "detail": "",
                    "values": {"v": 10.0},
                    "duration_s": 1,
                },
                {
                    "id": "t0.b",
                    "outcome": "PASS",
                    "detail": "",
                    "values": {},
                    "duration_s": 1,
                },
            ],
        }
        after = {
            "started": "b",
            "command_layout": "y",
            "results": [
                {
                    "id": "t0.a",
                    "outcome": "PASS",
                    "detail": "",
                    "values": {"v": 20.0},
                    "duration_s": 1,
                },
                {
                    "id": "t0.b",
                    "outcome": "FAIL",
                    "detail": "broke",
                    "values": {},
                    "duration_s": 1,
                },
            ],
        }
        text = report.compare(before, after)
        self.assertIn("WORSE", text)
        self.assertIn("t0.b", text)
        self.assertIn("t0.a.v: 10.0 -> 20.0", text)
        self.assertIn("Command layout changed", text)


if __name__ == "__main__":
    unittest.main()


class TestTheDialog(VerificationTestCase):
    """Opened with the app's own services, run to the end, results shown."""

    layout = sl.LAYOUT_2026_11_13

    def test_a_settings_run_fills_the_table_and_the_progress_bar(self):
        import time
        from types import SimpleNamespace

        from PyQt5.QtWidgets import QApplication

        from py2flamingo.services.camera_service import CameraService
        from py2flamingo.services.configuration_service import ConfigurationService
        from py2flamingo.services.laser_led_service import LaserLEDService
        from py2flamingo.views.dialogs.verification_dialog import VerificationDialog

        qt = QApplication.instance() or QApplication([])
        # The app wrote ScopeSettings.txt at connect; the dialog reads that copy.
        self.connection.get_microscope_settings()
        app = SimpleNamespace(
            connection_service=self.connection,
            camera_service=CameraService(self.connection),
            laser_led_service=LaserLEDService(
                self.connection, ConfigurationService(base_path=Path.cwd())
            ),
        )
        dialog = VerificationDialog(app)
        dialog.folder_edit.setText(str(Path.cwd() / "reports"))
        dialog.tier_boxes[Tier.SETTINGS].setChecked(True)
        self.assertIn("synthetic", dialog.server_label.text())
        # Edit > Microscope Setup saves new limits while the window is open
        # (CTLSM1, 2026-11-20): the run must use what is saved now.
        path = Path("microscope_settings/synthetic_settings.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["stage_limits"]["z"] = {"min": 12.5, "max": 24.0}
        path.write_text(json.dumps(data), encoding="utf-8")
        dialog._run()
        self.assertEqual(dialog._prepared.rig.envelope.limits["z"], (12.5, 24.0))
        self.assertIn("z 12.5..24", dialog.server_label.text())
        deadline = time.monotonic() + 60
        while dialog._thread.isRunning() and time.monotonic() < deadline:
            qt.processEvents()
            time.sleep(0.02)
        qt.processEvents()
        self.assertFalse(dialog._thread.isRunning())
        self.assertEqual(dialog.progress.value(), dialog.progress.maximum())
        outcomes = {
            dialog.table.item(r, 2).text(): dialog.table.item(r, 0).text()
            for r in range(dialog.table.rowCount())
        }
        self.assertEqual(outcomes["Trigger mode set, read back, restored"], "PASS")
        self.assertTrue(dialog.open_button.isEnabled())
        self.assertTrue(list((Path.cwd() / "reports").glob("synthetic_*/report.txt")))
        dialog.close()


class TestImageMeasures(unittest.TestCase):
    def test_a_known_shift_is_measured(self):
        import numpy as np

        from py2flamingo.verification import images

        ref = synth.render_frame(
            *synth.SAMPLE_CENTER_MM, synth.SAMPLE_FOCUS_Z_MM, 1.0, (256, 256)
        )
        moved = np.roll(np.roll(ref, 5, axis=0), -3, axis=1)
        dy, dx, peak = images.shift_against(ref, moved)
        self.assertAlmostEqual(dy, 5, delta=0.5)
        self.assertAlmostEqual(dx, -3, delta=0.5)
        self.assertGreater(peak, 0.2)
        m = images.metrics(ref)
        self.assertGreater(
            m["focus"], images.metrics(np.full_like(ref, m["mean"]))["focus"]
        )


class TestAreas(VerificationTestCase):
    """Each menu entry runs one area with only the prerequisites it needs."""

    layout = sl.LAYOUT_2026_11_13

    def test_the_stage_area_runs_alone(self):
        from py2flamingo.verification.session import top_tier_of

        _, _, by_id = self.run_tiers(top=top_tier_of("stage"), area="stage")
        self.assertIn("t2.halt", by_id)
        self.assertNotIn("t0.camera_settings", by_id)
        self.assertNotIn("t3.led", by_id)
        problems = {c: r.detail for c, r in by_id.items() if r.outcome.is_problem}
        self.assertEqual(problems, {})

    def test_the_data_area_brings_its_workflow_even_though_it_fails(self):
        _, _, by_id = self.run_tiers(area="data")
        self.assertEqual(by_id["t4.workflow"].outcome, Outcome.FAIL)  # exposure label
        self.assertEqual(by_id["t4.acquisition_files"].outcome, Outcome.FAIL)
        self.assertNotIn("t3.led", by_id)

    def test_a_lost_board_during_the_run_fails_the_notice_check(self):
        import time as _time

        self.connection.device_notices.append(
            (
                _time.time() + 1,
                "COMMON_DEVICE_LOST",
                "Server reports a board lost: subsystem 7",
            )
        )
        _, _, by_id = self.run_tiers(top=Tier.READ_ONLY, area="server")
        self.assertEqual(by_id["t9.board_notices"].outcome, Outcome.FAIL)


class TestOldDataFormat(VerificationTestCase):
    """3.0.0 writes ScopeSettings.txt and the old keys; the readers know those."""

    def test_the_data_check_passes_on_3_0_0_output(self):
        _, _, by_id = self.run_tiers(area="data")
        self.assertEqual(
            by_id["t4.acquisition_files"].outcome,
            Outcome.PASS,
            by_id["t4.acquisition_files"].detail,
        )


class TestCommandLine(unittest.TestCase):
    def test_led_percent_outside_the_range_is_refused(self):
        from py2flamingo.verification.__main__ import main

        for bad in ("0", "60"):
            with self.assertRaises(SystemExit) as caught:
                main(["--dry-run", "--led-percent", bad])
            self.assertEqual(caught.exception.code, 2)
        self.assertEqual(main(["--dry-run", "--led-percent", "40"]), 0)
