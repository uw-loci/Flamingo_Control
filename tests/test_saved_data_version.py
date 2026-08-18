"""A file from a different build must announce itself, not be guessed at.

Every persisted format here carries a hand-written ``version`` and almost
nothing reads it; nothing records which *software* wrote a file. So a session
that will not load, or loads into something subtly wrong, is indistinguishable
from a corrupt one.

The direction that matters is refusing a file from the FUTURE. This package's
own history is the argument: ``position_presets.json`` was rebuilt in one
comprehension, so one key a newer version had added raised TypeError, hit a
blanket except, emptied the set — and the next save wrote that empty set back
over the file. Reading optimistically and hoping is how that happens.

Run: python3 -m pytest tests/test_saved_data_version.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from py2flamingo.utils.saved_data_version import (  # noqa: E402
    PROVENANCE_KEY,
    check,
    read_provenance,
    stamp,
)

FORMAT = "LED 2D Overview session"


def _checked(payload, current=2, oldest=1):
    return check(
        payload,
        format_name=FORMAT,
        current_format_version=current,
        oldest_readable_version=oldest,
    )


class TestStamping:
    def test_a_stamped_payload_names_the_build_and_the_format(self):
        out = stamp({}, format_name="thing", format_version=3, app_version="0.6.2")
        block = out[PROVENANCE_KEY]
        assert block["app_version"] == "0.6.2"
        assert block["format_version"] == 3

    def test_stamping_keeps_the_payload_it_was_given(self):
        payload = {"config": {"led": "red"}, "version": "1.0"}
        out = stamp(payload, format_name="thing", format_version=1)
        assert out["config"] == {"led": "red"}
        assert out["version"] == "1.0"

    def test_the_block_does_not_squat_on_the_formats_own_version_key(self):
        # Every existing format already uses `version` for its own number.
        # Overwriting it would change the meaning of the files this is supposed
        # to make legible.
        out = stamp({"version": "1.0"}, format_name="thing", format_version=7)
        assert out["version"] == "1.0"
        assert out[PROVENANCE_KEY]["format_version"] == 7

    def test_the_app_version_is_filled_in_when_not_given(self):
        block = stamp({}, format_name="t", format_version=1)[PROVENANCE_KEY]
        assert block["app_version"]

    def test_a_stamped_payload_round_trips(self):
        payload = stamp({}, format_name="t", format_version=4, app_version="9.9.9")
        prov = read_provenance(payload)
        assert prov.app_version == "9.9.9"
        assert prov.format_version == 4
        assert prov.stamped


class TestReadingWhatIsAlreadyOnDisk:
    """The formats in this package write three shapes for the same idea."""

    @pytest.mark.parametrize("written", ["1.0", 1, "1", 1.0, "1.2.3"])
    def test_every_existing_version_shape_is_understood(self, written):
        assert read_provenance({"version": written}).format_version == 1

    def test_an_unstamped_file_still_reports_its_format_version(self):
        # These are the majority of files on disk today. Reporting them as
        # unknown would make every one of them look suspicious.
        prov = read_provenance({"version": "2.0"})
        assert prov.format_version == 2
        assert not prov.stamped

    def test_a_file_with_nothing_reports_nothing_rather_than_guessing(self):
        prov = read_provenance({})
        assert prov.format_version is None
        assert not prov.stamped

    def test_garbage_is_not_a_version(self):
        assert read_provenance({"version": "banana"}).format_version is None

    def test_a_non_dict_payload_is_not_an_exception(self):
        assert read_provenance(None).format_version is None
        assert read_provenance([1, 2, 3]).format_version is None

    def test_true_is_not_version_one(self):
        # bool is an int in Python, and this would otherwise read as v1.
        assert read_provenance({"version": True}).format_version is None


class TestRefusingAFileFromTheFuture:
    def test_a_newer_format_is_not_read(self):
        result = _checked(stamp({}, format_name=FORMAT, format_version=5), current=2)
        assert not result.readable
        assert result.from_the_future

    def test_it_says_what_wrote_the_file(self):
        payload = stamp({}, format_name=FORMAT, format_version=5, app_version="9.1.0")
        assert "9.1.0" in _checked(payload, current=2).reason

    def test_it_says_what_to_do(self):
        result = _checked(stamp({}, format_name=FORMAT, format_version=5), current=2)
        assert "Update Py2Flamingo" in result.reason

    def test_the_current_format_is_read(self):
        assert _checked(
            stamp({}, format_name=FORMAT, format_version=2), current=2
        ).readable


class TestOlderFormats:
    def test_a_supported_older_format_is_read_and_said_to_be_old(self):
        result = _checked({"version": 1}, current=3, oldest=1)
        assert result.readable
        assert "older format" in result.reason

    def test_a_format_too_old_to_read_is_refused(self):
        result = _checked({"version": 1}, current=5, oldest=3)
        assert not result.readable
        assert not result.from_the_future

    def test_an_unversioned_file_is_read_as_the_oldest_format(self):
        # Everything written before stamping existed is exactly this. Refusing
        # it would break every file already on disk.
        result = _checked({}, current=2, oldest=1)
        assert result.readable
        assert "predates version stamping" in result.reason

    def test_an_unversioned_file_is_refused_when_the_oldest_moved_on(self):
        result = _checked({}, current=5, oldest=3)
        assert not result.readable


class TestTheAppVersionIsProvenanceNotAGate:
    """Gating on it would refuse good files every time the package bumped."""

    def test_a_much_newer_app_with_the_same_format_still_reads(self):
        payload = stamp({}, format_name=FORMAT, format_version=2, app_version="99.0.0")
        assert _checked(payload, current=2).readable

    def test_a_much_older_app_with_the_same_format_still_reads(self):
        payload = stamp({}, format_name=FORMAT, format_version=2, app_version="0.0.1")
        assert _checked(payload, current=2).readable

    def test_but_it_is_reported(self):
        payload = stamp({}, format_name=FORMAT, format_version=2, app_version="0.0.1")
        assert "0.0.1" in _checked(payload, current=2).reason


class TestEveryVerdictCanBeShownToSomebody:
    @pytest.mark.parametrize(
        "payload,current,oldest",
        [
            ({}, 2, 1),
            ({"version": 1}, 3, 1),
            ({"version": 9}, 2, 1),
            ({"version": 1}, 5, 3),
            ({"version": "banana"}, 2, 1),
        ],
    )
    def test_the_reason_is_never_empty_and_names_the_format(
        self, payload, current, oldest
    ):
        # This string is the whole point: it goes in front of a user who has to
        # decide whether to trust what just opened.
        result = _checked(payload, current=current, oldest=oldest)
        assert result.reason.strip()
        assert FORMAT in result.reason


class TestTheFormatRegistry:
    """The writer and the reader take the number from the same object.

    Two copies of one number that drift apart is the failure this package keeps
    hitting: the tile step had five, and fixing two of them shipped the same
    bug three times.
    """

    def test_a_spec_stamps_what_it_checks(self):
        from py2flamingo.utils.saved_data_version import LED_2D_SESSION

        assert LED_2D_SESSION.check(LED_2D_SESSION.stamp({})).readable

    def test_every_shipped_spec_can_read_its_own_output(self):
        import py2flamingo.utils.saved_data_version as mod
        from py2flamingo.utils.saved_data_version import FormatSpec

        specs = [v for v in vars(mod).values() if isinstance(v, FormatSpec)]
        assert specs
        for spec in specs:
            result = spec.check(spec.stamp({}))
            assert result.readable, f"{spec.name} cannot read its own output"

    def test_every_shipped_spec_still_reads_unstamped_files(self):
        # Everything already on disk. A registry entry whose `oldest` outran
        # reality would lock users out of their own sessions.
        import py2flamingo.utils.saved_data_version as mod
        from py2flamingo.utils.saved_data_version import FormatSpec

        for spec in [v for v in vars(mod).values() if isinstance(v, FormatSpec)]:
            assert spec.check({}).readable, f"{spec.name} rejects pre-stamp files"

    def test_a_spec_refuses_its_own_future(self):
        from py2flamingo.utils.saved_data_version import THRESHOLD_PRESET

        future = stamp(
            {},
            format_name=THRESHOLD_PRESET.name,
            format_version=THRESHOLD_PRESET.current + 1,
        )
        assert not THRESHOLD_PRESET.check(future).readable
