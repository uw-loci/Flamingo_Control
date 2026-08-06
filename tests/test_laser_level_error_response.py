"""A scope that declines a level query is not a client parse failure.

The scope answers a level query it cannot satisfy with an error STRING in the
same 72-byte field a number would arrive in — e.g. ``b'getLevel error'``. The
old handler ran ``float()`` on it, caught the ValueError, and logged:

    Failed to parse laser power from response: buffer=b'getLevel error',
    error=could not convert string to float: 'getLevel error'

which reads like a bug in our decoding, at ERROR level, on a run that had
otherwise completed successfully — and sends people looking in the wrong place.

These tests drive the real ``query_laser_power`` with a stubbed transport, so
deleting the branch under test fails them.
"""

import logging
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from py2flamingo.services.laser_led_service import LaserLEDService  # noqa: E402


def _service(payload, *, success=True):
    """A real LaserLEDService with only the transport stubbed out."""
    svc = LaserLEDService.__new__(LaserLEDService)
    svc.logger = MagicMock(spec=logging.Logger)
    svc._lasers = [object()] * 4  # laser_index 1..4 valid
    svc._query_command = MagicMock(
        return_value={"success": success, "parsed": {"data": payload}}
    )
    return svc


class TestRealNumbersStillWork(unittest.TestCase):
    def test_a_plain_number_parses(self):
        svc = _service(b"42.5")

        self.assertAlmostEqual(svc.query_laser_power(1), 42.5)
        svc.logger.warning.assert_not_called()

    def test_a_null_terminated_number_parses(self):
        svc = _service(b"12.0\x00\x00garbage")

        self.assertAlmostEqual(svc.query_laser_power(1), 12.0)

    def test_zero_is_a_real_reading_not_a_failure(self):
        svc = _service(b"0.0")

        self.assertEqual(svc.query_laser_power(1), 0.0)
        svc.logger.warning.assert_not_called()


class TestScopeDeclinesTheQuery(unittest.TestCase):
    """The reported case: buffer=b'getLevel error'."""

    def test_it_returns_the_unknown_sentinel(self):
        svc = _service(b"getLevel error")

        self.assertEqual(svc.query_laser_power(1), -1.0)

    def test_it_warns_rather_than_errors(self):
        """The run completed fine; ERROR overstates it."""
        svc = _service(b"getLevel error")

        svc.query_laser_power(1)

        svc.logger.warning.assert_called_once()
        svc.logger.error.assert_not_called()

    def test_the_message_says_the_device_declined_and_why_it_might(self):
        svc = _service(b"getLevel error")

        svc.query_laser_power(1)

        msg = svc.logger.warning.call_args[0][0]
        self.assertIn("declining", msg)
        self.assertIn("off or not fitted", msg)
        self.assertIn("not a", msg)  # ...not a client-side parse failure

    def test_detection_is_case_insensitive(self):
        svc = _service(b"ERROR: no such laser")

        self.assertEqual(svc.query_laser_power(1), -1.0)
        svc.logger.warning.assert_called_once()


class TestGenuinelyUnreadableResponses(unittest.TestCase):
    """Junk that is neither a number nor a stated error is still an ERROR."""

    def test_unparseable_junk_still_logs_an_error(self):
        svc = _service(b"\x01\x02\x03")

        self.assertEqual(svc.query_laser_power(1), -1.0)
        svc.logger.error.assert_called()

    def test_an_empty_payload_is_reported_separately(self):
        svc = _service(b"")

        self.assertEqual(svc.query_laser_power(1), -1.0)
        svc.logger.error.assert_called()

    def test_a_failed_transport_is_reported_as_a_query_failure(self):
        svc = _service(b"42.0", success=False)

        self.assertEqual(svc.query_laser_power(1), -1.0)
        svc.logger.error.assert_called()

    def test_an_out_of_range_index_is_rejected_before_any_io(self):
        svc = _service(b"42.0")

        self.assertEqual(svc.query_laser_power(99), -1.0)
        svc._query_command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
