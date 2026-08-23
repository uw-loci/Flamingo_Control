"""LED_SET carries the percentage itself, not a scaled 16-bit level.

The server used to take a raw level, and this package mapped 0-100% onto the
positive half of a signed 16-bit range (0% -> 32000, 100% -> 65534) to match
what the C++ GUI was observed to send. A server update replaced that with the
percentage: 27 means 27%.

Sending the old value against the new server asks for 32000%, so the LED sits
whereever the firmware clamps to — bright no matter where the slider is, which
reads as "the intensity control does nothing" rather than as a protocol
mismatch. That is why this is pinned rather than left to the naming: every
layer above already says "percent", so nothing but the wire value itself
distinguishes the two protocols.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \\
        tests/test_led_intensity_is_a_percentage.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from py2flamingo.services.laser_led_service import (  # noqa: E402
    LaserLEDCommandCode,
    LaserLEDService,
)


class _Service(LaserLEDService):
    """The real service with only the wire send replaced."""

    def __init__(self):
        self.sent = []
        self._led_available = True
        import logging

        self.logger = logging.getLogger("test")

    def _send_command(self, command_code, command_name, params=None, **kwargs):
        self.sent.append((command_code, list(params or [])))
        return {"success": True}

    def _handle_set_command_result(self, result, name):
        return bool(result.get("success"))


def _sent_value(service):
    """The intensity field of the last LED_SET."""
    code, params = service.sent[-1]
    assert code == LaserLEDCommandCode.LED_SET
    return params[4]


class TestThePercentageGoesOnTheWire:
    @pytest.mark.parametrize("percent", [0, 1, 27, 50, 99, 100])
    def test_the_value_sent_is_the_percentage(self, percent):
        service = _Service()
        assert service.set_led_intensity(0, float(percent))
        assert _sent_value(service) == percent

    def test_the_users_example(self):
        # 27% reaches the server as 27.
        service = _Service()
        service.set_led_intensity(1, 27.0)
        assert _sent_value(service) == 27

    def test_the_old_scaled_value_is_gone(self):
        # 0% used to go out as 32000, which the new server reads as 32000%.
        service = _Service()
        service.set_led_intensity(0, 0.0)
        assert _sent_value(service) == 0

    def test_full_brightness_is_100_not_65534(self):
        service = _Service()
        service.set_led_intensity(3, 100.0)
        assert _sent_value(service) == 100

    def test_nothing_sent_exceeds_a_percentage(self):
        # A value above 100 on this protocol is meaningless, and is exactly the
        # shape of the bug being fixed.
        service = _Service()
        for percent in (0.0, 33.3, 66.7, 100.0):
            service.set_led_intensity(0, percent)
            assert 0 <= _sent_value(service) <= 100


class TestFractionsAreRoundedNotTruncated:
    def test_a_fraction_rounds_to_the_nearer_percent(self):
        # The wire fields are int32 and the spin box allows decimals.
        service = _Service()
        service.set_led_intensity(0, 27.6)
        assert _sent_value(service) == 28

    def test_rounding_goes_down_when_nearer(self):
        service = _Service()
        service.set_led_intensity(0, 27.4)
        assert _sent_value(service) == 27

    def test_an_integer_percent_is_untouched(self):
        service = _Service()
        service.set_led_intensity(0, 42.0)
        assert _sent_value(service) == 42

    def test_the_value_is_an_int(self):
        # The params are int32 fields; a float here would be a protocol error
        # rather than a rounding question.
        service = _Service()
        service.set_led_intensity(0, 55.0)
        assert isinstance(_sent_value(service), int)


class TestTheColourStillTravelsSeparately:
    @pytest.mark.parametrize("colour", [0, 1, 2, 3])
    def test_the_colour_index_is_its_own_field(self, colour):
        service = _Service()
        service.set_led_intensity(colour, 27.0)
        _code, params = service.sent[-1]
        assert params[3] == colour
        assert params[4] == 27


class TestTheRangeGuardStillHolds:
    @pytest.mark.parametrize("percent", [-1.0, 100.1, 32000.0])
    def test_a_value_outside_0_100_is_refused(self, percent):
        # Including 32000 — the old wire value. If some caller still passes one,
        # it must be rejected rather than forwarded as a percentage.
        service = _Service()
        assert service.set_led_intensity(0, percent) is False
        assert service.sent == []

    def test_an_unavailable_led_sends_nothing(self):
        service = _Service()
        service._led_available = False
        assert service.set_led_intensity(0, 27.0) is False
        assert service.sent == []


class TestNothingElseStillScalesTheValue:
    """A surviving copy of the mapping would resurrect the bug on its path.

    Scanned with COMMENTS STRIPPED. The old constants are named in the comment
    that explains why they are gone, and that explanation is the most useful
    thing there for the next reader — a check that cannot tell prose from code
    would force it out.
    """

    @staticmethod
    def _code_only(module) -> str:
        import io
        import tokenize

        source = Path(module.__file__).read_text(encoding="utf-8")
        kept = []
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type not in (tokenize.COMMENT, tokenize.STRING):
                kept.append(token.string)
        return " ".join(kept)

    def test_no_scaling_constants_survive_in_code(self):
        import py2flamingo.services.laser_led_service as mod

        code = self._code_only(mod)
        for constant in ("LED_MIN", "LED_MAX", "65534", "32000"):
            assert constant not in code, f"{constant} still used in code"

    def test_the_history_is_still_written_down(self):
        # The other half: someone who finds `led_value = round(percent)` should
        # be able to learn why a scaled value was ever expected.
        import py2flamingo.services.laser_led_service as mod

        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert "32000" in source, "the reason the mapping existed was lost"
