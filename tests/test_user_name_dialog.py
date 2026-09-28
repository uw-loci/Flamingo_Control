"""The startup prompt, at the widget level.

The model tests cover what a name turns into; these cover that the dialog
returns the right name at all -- a prompt that computes correctly and hands
back the wrong string would file every run under the wrong person.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \
        tests/test_user_name_dialog.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

pytest.importorskip("PyQt5")


@pytest.fixture(scope="module")
def app():
    from PyQt5.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _dialog(app, history=None, current=None):
    from py2flamingo.views.dialogs.user_name_dialog import UserNameDialog

    return UserNameDialog(history=history, current=current)


class TestNoneIsTheSafeDefault:
    def test_a_fresh_prompt_returns_no_user(self, app):
        # Nobody has been asked yet: Enter must not invent a folder.
        assert _dialog(app).selected_name() is None

    def test_the_none_entry_is_first(self, app):
        from py2flamingo.views.dialogs.user_name_dialog import NO_USER_LABEL

        assert _dialog(app, history=["Sam"]).combo_items()[0] == NO_USER_LABEL

    def test_selecting_none_after_a_name_returns_none(self, app):
        d = _dialog(app, history=["Sam"], current="Sam")
        assert d.selected_name() == "Sam"
        d._combo.setCurrentIndex(0)
        assert d.selected_name() is None

    def test_typing_the_none_label_is_still_none(self, app):
        from py2flamingo.views.dialogs.user_name_dialog import NO_USER_LABEL

        d = _dialog(app, history=["Sam"])
        d._combo.setCurrentText(NO_USER_LABEL)
        assert d.selected_name() is None

    def test_blank_text_is_none_not_an_empty_folder(self, app):
        d = _dialog(app, history=["Sam"])
        d._combo.setCurrentText("   ")
        assert d.selected_name() is None


class TestItRemembers:
    def test_previous_users_are_offered(self, app):
        items = _dialog(app, history=["Sam", "Joe"]).combo_items()
        assert items[1:] == ["Sam", "Joe"]

    def test_it_opens_on_last_sessions_choice(self, app):
        d = _dialog(app, history=["Sam", "Joe"], current="Joe")
        assert d.selected_name() == "Joe"

    def test_it_opens_on_none_when_that_was_last_chosen(self, app):
        d = _dialog(app, history=["Sam"], current=None)
        assert d.selected_name() is None

    def test_a_name_not_in_the_history_still_preselects(self, app):
        d = _dialog(app, history=["Sam"], current="Kurt")
        assert d.selected_name() == "Kurt"

    def test_a_new_name_can_be_typed(self, app):
        d = _dialog(app, history=["Sam"])
        d._combo.setCurrentText("Kurt")
        assert d.selected_name() == "Kurt"


class TestItExplainsItself:
    def test_it_shows_the_resulting_folder(self, app):
        d = _dialog(app)
        d._combo.setCurrentText("Sam Nelson")
        assert "Sam_Nelson/" in d._effect.text()

    def test_it_says_nothing_changes_for_none(self, app):
        assert "exactly as it was before" in _dialog(app)._effect.text()

    def test_the_docs_link_points_at_the_repo(self, app):
        from py2flamingo.views.dialogs.user_name_dialog import DOCS_URL

        assert DOCS_URL.startswith("https://github.com/uw-loci/Flamingo_Control/")
        assert DOCS_URL.endswith("docs/user_name.md")

    def test_the_linked_page_exists_in_this_repo(self):
        # A dead link in the one dialog everybody sees is worse than no link.
        assert (Path(__file__).resolve().parents[1] / "docs/user_name.md").is_file()
