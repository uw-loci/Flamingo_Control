"""
The client must know which command numbering the connected server uses.

The 2026-10-06 server build removed six COMMON commands and so renumbered
fourteen, while still reporting version 3.0.0. On that build our old
"list storage drives" code (0x1013) is SYSTEM_SLEEP and sets the inactivity
sleep to one minute; our old "load scope settings" code (0x1009) is a MEMS
save. These tests pin how the client finds out (the per-block code counts in
the version string) and what it then refuses to send.
"""

import struct
import unittest

from py2flamingo.core import server_layout as sl
from py2flamingo.core.metadata_document import legacy_scope_settings_text, parse
from py2flamingo.core.protocol_encoder import ProtocolEncoder as LegacyEncoder
from py2flamingo.core.tcp_protocol import ProtocolEncoder

POST_1006 = (
    "ControlSystem - Oct  6 2026 - 14:03:00 - 3.0.0 - 20.10.58.7.2.20.19.9.8.8.7.4"
)
SERVER_300 = (
    "ControlSystem - Sep 19 2026 - 09:00:00 - 3.0.0 - 26.10.58.7.2.20.19.9.8.8.7.5"
)


class LayoutTestCase(unittest.TestCase):
    def tearDown(self):
        sl.clear_active()


class TestReadingTheVersion(LayoutTestCase):
    def test_both_known_builds_are_told_apart_though_both_say_3_0_0(self):
        old = sl.layout_for(sl.parse_server_version(SERVER_300))
        new = sl.layout_for(sl.parse_server_version(POST_1006))
        self.assertIs(old, sl.REFERENCE)
        self.assertIs(new, sl.LAYOUT_2026_10_06)

    def test_a_date_with_two_spaces_survives_the_split(self):
        v = sl.parse_server_version(POST_1006)
        self.assertEqual(v.build_date, "Oct  6 2026")
        self.assertEqual(v.system_version, "3.0.0")

    def test_garbage_is_not_a_version(self):
        self.assertIsNone(sl.parse_server_version(""))
        self.assertIsNone(
            sl.parse_server_version("ControlSystem - x - y - 3.0.0 - 1.2")
        )

    def test_our_string_parses_the_way_the_server_splits_it(self):
        """The server splits on '-' into five fields; a '-' inside a field
        (a version like 0.6.2-dev) would shift every field after it."""
        mine = sl.client_version_string("0.6.2-dev")
        self.assertEqual(len(mine.split("-")), 5)
        self.assertEqual(sl.parse_server_version(mine).counts, sl.REFERENCE.counts)


class TestTheTablesMatchTheirCounts(LayoutTestCase):
    """Each table must hold exactly the codes its counts say exist."""

    def test_common_codes_are_contiguous_and_end_where_the_count_says(self):
        for layout in sl.KNOWN_LAYOUTS.values():
            common = sorted(
                v for k, v in layout.codes.items() if k.startswith("COMMON_")
            )
            n = layout.counts[sl.BLOCK_ORDER.index("COMMON")]
            self.assertEqual(common, list(range(0x1001, 0x1000 + n)), layout.name)

    def test_metadata_codes_are_contiguous(self):
        for layout in sl.KNOWN_LAYOUTS.values():
            meta = sorted(
                v for k, v in layout.codes.items() if k.startswith("META_DATA_")
            )
            n = layout.counts[sl.BLOCK_ORDER.index("META_DATA")]
            self.assertEqual(meta, list(range(0xC001, 0xC000 + n)), layout.name)

    def test_only_common_and_metadata_moved_on_2026_10_06(self):
        self.assertEqual(sl.LAYOUT_2026_10_06.changed_blocks, {"COMMON", "META_DATA"})


class TestTheGuard(LayoutTestCase):
    def setUp(self):
        sl.set_active(sl.LAYOUT_2026_10_06)

    def test_stale_constants_that_mean_something_else_now_are_refused(self):
        # 0x1013 STORAGE_PATH_SET -> SYSTEM_SLEEP; 0x1009 SCOPE_SETTINGS_LOAD ->
        # MEMS_SPEC_SETTINGS_SAVE; 0x1008 SCOPE_SETTINGS_SAVE -> MEMS_CAL_SETTINGS_LOAD
        for code in (0x1013, 0x1009, 0x1008, 0x1007, 0x1014, 0x1015):
            with self.assertRaises(sl.CommandUnavailable, msg=hex(code)):
                sl.check_sendable(code)

    def test_codes_that_mean_the_same_or_were_looked_up_pass(self):
        for code in (0x1001, 0x1002, 0x1006, sl.code("COMMON_STORAGE_PATH_SET")):
            sl.check_sendable(code)

    def test_document_commands_that_write_on_the_server_are_not_sendable(self):
        for name in ("META_DATA_DOCUMENT_LOAD", "META_DATA_DOCUMENT_SAVE"):
            with self.assertRaises(sl.CommandUnavailable):
                sl.check_sendable(sl.LAYOUT_2026_10_06.codes[name])

    def test_blocks_that_did_not_move_are_untouched(self):
        for code in (0x6003, 0x6005, 0x3004, 0x2001, 0xA007):
            sl.check_sendable(code)

    def test_both_encoders_refuse(self):
        with self.assertRaises(sl.CommandUnavailable):
            ProtocolEncoder().encode_command(code=0x1013)
        with self.assertRaises(sl.CommandUnavailable):
            LegacyEncoder().encode_command(code=0x1013)

    def test_the_lookup_gives_the_new_number(self):
        self.assertEqual(sl.code("COMMON_STORAGE_PATH_SET", fallback=0x1013), 0x100D)
        with self.assertRaises(sl.CommandUnavailable):
            sl.code("COMMON_SCOPE_SETTINGS_LOAD")

    def test_no_server_identified_means_no_check_and_the_3_0_0_numbers(self):
        sl.clear_active()
        sl.check_sendable(0x1013)
        self.assertEqual(sl.code("COMMON_STORAGE_PATH_SET", fallback=0x1013), 0x1013)

    def test_the_3_0_0_layout_refuses_nothing(self):
        sl.set_active(sl.REFERENCE)
        for code in (0x1013, 0x1009, 0x1008):
            sl.check_sendable(code)


class TestABuildThisClientDoesNotKnow(LayoutTestCase):
    """The next build: refuse what moved, keep what matches a known build."""

    def setUp(self):
        # One more CAMERA command than any known build; COMMON as 2026-10-06.
        version = sl.parse_server_version(
            "ControlSystem - Jan  1 2027 - 08:00:00 - 3.0.0 - "
            "20.10.59.7.2.20.19.9.8.8.7.4"
        )
        self.layout = sl.layout_for(version)
        sl.set_active(self.layout)

    def test_it_is_not_recognized_and_names_the_block(self):
        self.assertFalse(self.layout.recognized)
        self.assertEqual(self.layout.unknown_blocks, {"CAMERA"})
        self.assertIn("CAMERA", self.layout.describe())

    def test_every_camera_command_is_refused(self):
        with self.assertRaises(sl.CommandUnavailable) as caught:
            sl.check_sendable(0x3004)  # WORKFLOW_START in every known build
        self.assertIn("update", str(caught.exception))

    def test_blocks_matching_a_known_build_keep_working(self):
        self.assertEqual(sl.code("COMMON_STORAGE_PATH_SET"), 0x100D)
        self.assertTrue(self.layout.has_metadata_document)
        sl.check_sendable(0x6003)

    def test_the_override_lets_a_code_through(self):
        import os

        os.environ[sl.OVERRIDE_ENV] = "1"
        try:
            sl.check_sendable(0x3004)
        finally:
            del os.environ[sl.OVERRIDE_ENV]


class TestTheSettingsDocument(unittest.TestCase):
    DOC = (
        "<MetaData>\n  <Instrument>\n    <Microscope characteristics>\n"
        "      Name = n7\n      Type = T, X, L, V SPIM\n"
        "      <Address>\n        IP = 10.0.0.1\n        Port = 53717\n      </Address>\n"
        "    </Microscope characteristics>\n"
        "    <Electronic box hardware>\n      <Stage motors and controllers>\n"
        "        <Stage z>\n          Soft limit max = 25.000\n          Home =\n"
        "        </Stage z>\n"
        "      </Stage motors and controllers>\n    </Electronic box hardware>\n"
        "    <Microscope Hardware>\n      <Detection>\n        <Detection 1>\n"
        "          Tube lens focal length (mm) = 200\n"
        "          <Detection lens>\n            Magnification = 6.205\n"
        "          </Detection lens>\n        </Detection 1>\n"
        "      </Detection>\n    </Microscope Hardware>\n  </Instrument>\n</MetaData>\n"
    )

    def _sections(self):
        import tempfile
        from pathlib import Path

        from py2flamingo.utils.file_handlers import text_to_dict

        text, missing = legacy_scope_settings_text(parse(self.DOC))
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "ScopeSettings.txt"
            path.write_text(text)
            return text_to_dict(path), missing

    def test_the_keys_the_client_reads_come_out_in_the_old_layout(self):
        sections, _ = self._sections()
        self.assertEqual(sections["Stage limits"]["Soft limit max z-axis"], "25.000")
        self.assertEqual(sections["Type"]["Objective lens magnification"], "6.205")
        self.assertEqual(sections["Type"]["Tube lens design focal length (mm)"], "200")
        self.assertEqual(sections["Type"]["Microscope name"], "n7")
        self.assertEqual(sections["Type"]["Microscope address"], "10.0.0.1 53717")

    def test_an_empty_value_is_left_out_not_written_as_blank(self):
        """float("") would fail every reader; a missing key falls back."""
        sections, missing = self._sections()
        self.assertNotIn("Home z-axis", sections["Stage limits"])
        self.assertIn("Stage limits/Home z-axis", missing)

    def test_the_config_loader_finds_the_microscope_name(self):
        """It reads the name with a regex, not text_to_dict."""
        import re

        text, _ = legacy_scope_settings_text(parse(self.DOC))
        m = re.search(r"Microscope name[^\S\r\n]*=[^\S\r\n]*([^\r\n]+)", text)
        self.assertEqual(m.group(1).strip(), "n7")


class TestAgainstTheSyntheticScope(LayoutTestCase):
    """Over a real socket: identify, fetch settings, list drives."""

    def _connect(self, layout):
        import os
        import tempfile

        from py2flamingo.core.tcp_connection import TCPConnection
        from py2flamingo.models.connection import ConnectionConfig
        from py2flamingo.services.connection_service import MVCConnectionService
        from py2flamingo.testing.synthetic_microscope import SyntheticMicroscope

        scope = SyntheticMicroscope(command_port=0, live_port=0, layout=layout)
        scope.start()
        self.addCleanup(scope.stop)
        service = MVCConnectionService(TCPConnection(), ProtocolEncoder())
        # get_microscope_settings writes microscope_settings/ in the cwd.
        cwd = os.getcwd()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        os.chdir(tmp.name)
        self.addCleanup(os.chdir, cwd)
        service.connect(
            ConnectionConfig("127.0.0.1", scope.command_port, scope.live_port)
        )
        self.addCleanup(lambda: service.is_connected() and service.disconnect())
        return scope, service

    def test_a_2026_10_06_server_is_identified_and_read_from_its_document(self):
        from pathlib import Path

        scope, service = self._connect(sl.LAYOUT_2026_10_06)
        self.assertIs(sl.active(), sl.LAYOUT_2026_10_06)
        sent = scope.received(0x1002)[0]
        self.assertTrue(sent.additional_data.startswith(b"Flamingo_Control - "))

        _, settings = service.get_microscope_settings()
        self.assertEqual(settings["Type"]["Microscope name"], "synthetic")
        self.assertEqual(settings["Stage limits"]["Soft limit max x-axis"], "12.310")
        self.assertTrue(Path("microscope_settings/SystemMetaData.txt").exists())
        # Read with the read-only request, never the reload-and-save one.
        self.assertTrue(scope.received(0x1001))
        self.assertFalse(scope.received(0xC003))

        self.assertEqual(service.query_available_drives(), ["/media/synthetic/drive1"])
        self.assertTrue(scope.received(0x100D))
        self.assertIsNone(scope.sleep_minutes, "0x1013 would have set the sleep timer")

    def test_a_3_0_0_server_is_read_exactly_as_before(self):
        scope, service = self._connect(sl.REFERENCE)
        self.assertIs(sl.active(), sl.REFERENCE)
        _, settings = service.get_microscope_settings()
        self.assertEqual(settings["Type"]["Microscope name"], "synthetic")
        self.assertTrue(scope.received(0x1009))
        self.assertEqual(service.query_available_drives(), ["/media/synthetic/drive1"])
        self.assertTrue(scope.received(0x1013))

    def test_disconnecting_forgets_the_layout(self):
        _, service = self._connect(sl.LAYOUT_2026_10_06)
        service.disconnect()
        self.assertIsNone(sl.active())

    def test_frames_after_the_reply_are_left_for_the_reader(self):
        """The handshake reader stops at its reply; the next exchange still
        finds its own reply rather than a leftover frame."""
        scope, service = self._connect(sl.LAYOUT_2026_10_06)
        service.get_microscope_settings()  # leaves a STATE_GET frame behind
        header, _ = service._exchange_frames(0x1002, want=0x1002, additional=b"x")
        self.assertEqual(struct.unpack_from("<I", header, 4)[0], 0x1002)


if __name__ == "__main__":
    unittest.main()
