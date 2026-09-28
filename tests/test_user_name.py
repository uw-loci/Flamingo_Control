"""The user name decides where a run is filed, so its edges are pinned here.

Everything under test is Qt-free on purpose: GUI tests in this repo skip
wherever PyQt5 is absent, so any rule that matters lives in a plain module and
is tested like one.

Run: .venv/bin/python -m pytest tests/test_user_name.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from py2flamingo.models.user_name import (  # noqa: E402
    MAX_FOLDER_NAME_LENGTH,
    MAX_HISTORY_ENTRIES,
    describe_folder_effect,
    folder_name_for,
    merge_into_history,
    normalize_stored_name,
)
from py2flamingo.utils.tile_folder_organizer import acquisition_root  # noqa: E402


class TestNoUserIsARealAnswer:
    """ "None" must reproduce the old layout exactly, not a folder named None."""

    @pytest.mark.parametrize("empty", [None, "", "   ", "\t\n"])
    def test_nothing_in_means_no_folder(self, empty):
        assert folder_name_for(empty) is None
        assert normalize_stored_name(empty) is None

    def test_a_name_that_sanitises_away_is_also_no_folder(self):
        # Better no folder than one called "" or "." next to the real data.
        assert folder_name_for("...") is None
        assert folder_name_for("///") is None
        assert folder_name_for("???") is None

    def test_no_user_leaves_the_path_byte_identical(self):
        before = acquisition_root("D:/CTLSM1", "Test", "2026-09-28")
        assert acquisition_root("D:/CTLSM1", "Test", "2026-09-28", None) == before
        assert acquisition_root("D:/CTLSM1", "Test", "2026-09-28", "  ") == before

    def test_declining_is_not_remembered_as_a_person(self):
        assert merge_into_history(["Sam Nelson"], None) == ["Sam Nelson"]


class TestTheFolderNameIsSafeToWrite:
    def test_spaces_become_underscores(self):
        assert folder_name_for("Sam Nelson") == "Sam_Nelson"

    def test_repeated_whitespace_collapses_once(self):
        assert folder_name_for("Sam   \t Nelson") == "Sam_Nelson"

    def test_path_separators_cannot_smuggle_in_a_level(self):
        # The whole feature is one directory level; a name must not add more.
        assert "/" not in (folder_name_for("a/b") or "")
        assert "\\" not in (folder_name_for("a\\b") or "")
        assert "/" not in (folder_name_for("../../etc") or "")
        # Also must not come back as a relative-path token of its own.
        assert folder_name_for("../../etc") not in {"..", ".", "../.."}

    def test_accents_are_folded_because_the_server_strips_them(self):
        # SystemSupport::stripUnicode keeps only ASCII 32-126, so an unfolded
        # name would disagree with what actually appears on disk.
        assert folder_name_for("Müller") == "Muller"

    def test_windows_device_names_are_not_used_bare(self):
        assert folder_name_for("CON") == "CON_user"
        assert folder_name_for("lpt1") == "lpt1_user"

    def test_trailing_dots_and_spaces_go(self):
        # Windows strips these itself, so the name would not round-trip.
        assert folder_name_for("  Sam.  ") == "Sam"

    def test_length_is_capped(self):
        assert len(folder_name_for("x" * 300)) == MAX_FOLDER_NAME_LENGTH

    def test_a_truncated_name_does_not_end_in_a_separator(self):
        name = ("word " * 40).strip()
        folder = folder_name_for(name)
        assert not folder.endswith("_")


class TestTheStoredNameIsNotTheFolderName:
    def test_the_typed_spelling_survives_for_display(self):
        # Sanitising on the way in would rewrite someone's name in the UI.
        assert normalize_stored_name("Müller") == "Müller"
        assert folder_name_for("Müller") == "Muller"

    def test_the_effect_line_names_the_folder_when_they_differ(self):
        text = describe_folder_effect("Müller")
        assert "Muller/" in text and "Müller" in text

    def test_the_effect_line_says_nothing_changes_for_none(self):
        assert "exactly as it was before" in describe_folder_effect(None)


class TestHistory:
    def test_most_recent_goes_first(self):
        assert merge_into_history(["Joe", "Kurt"], "Sam") == ["Sam", "Joe", "Kurt"]

    def test_reusing_a_name_moves_it_up_without_duplicating(self):
        assert merge_into_history(["Joe", "Sam"], "Sam") == ["Sam", "Joe"]

    def test_case_and_spacing_do_not_create_a_second_entry(self):
        # "sam nelson" and "Sam Nelson" are one folder, so they are one entry.
        assert merge_into_history(["Sam Nelson"], "sam   nelson") == ["sam nelson"]

    def test_the_list_is_bounded(self):
        history = [f"user{i}" for i in range(50)]
        assert len(merge_into_history(history, "new")) == MAX_HISTORY_ENTRIES

    def test_junk_entries_are_dropped_on_read(self):
        assert merge_into_history(["", "   ", None, "Sam"], None) == ["Sam"]


class TestAcquisitionRoot:
    def test_the_user_folder_is_the_top_level(self):
        root = acquisition_root("D:/CTLSM1", "Test", "2026-09-28", "Sam Nelson")
        assert root.parts[-3:] == ("Sam_Nelson", "Test", "2026-09-28")

    def test_it_sits_under_the_drive_not_beside_it(self):
        root = acquisition_root("D:/CTLSM1", "Test", "2026-09-28", "Sam")
        assert str(root).replace("\\", "/").startswith("D:/CTLSM1/Sam/")


class TestItSurvivesARestart:
    """The name is remembered on disk, so the next launch starts on it."""

    @staticmethod
    def _service(tmp_path):
        from py2flamingo.services.configuration_service import ConfigurationService

        return ConfigurationService(base_path=tmp_path)

    def test_a_name_comes_back_on_the_next_launch(self, tmp_path):
        self._service(tmp_path).set_user_name("Sam Nelson")
        assert self._service(tmp_path).get_user_name() == "Sam Nelson"

    def test_choosing_none_comes_back_as_none(self, tmp_path):
        # Not "never asked": the dialog must reopen on None, not on the last
        # person who happened to type a name.
        service = self._service(tmp_path)
        service.set_user_name("Sam Nelson")
        service.set_user_name(None)
        assert self._service(tmp_path).get_user_name() is None

    def test_the_dropdown_remembers_everyone(self, tmp_path):
        service = self._service(tmp_path)
        for name in ("Joe", "Kurt", "Sam"):
            service.set_user_name(name)
        assert self._service(tmp_path).get_user_name_history() == ["Sam", "Kurt", "Joe"]

    def test_declining_does_not_wipe_the_dropdown(self, tmp_path):
        service = self._service(tmp_path)
        service.set_user_name("Sam")
        service.set_user_name(None)
        assert self._service(tmp_path).get_user_name_history() == ["Sam"]

    def test_nothing_stored_is_no_user_and_an_empty_list(self, tmp_path):
        service = self._service(tmp_path)
        assert service.get_user_name() is None
        assert service.get_user_name_history() == []
