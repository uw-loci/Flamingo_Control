"""Ask who is at the microscope, once, at startup.

A shared instrument writes into one storage root, and nothing the server puts
on disk records whose run a stack was. This is the one place that question gets
asked, so it has to be answerable in about two seconds by someone who came to
image a sample -- a dropdown of the people who have used this PC before, a way
to type a new name, and an explicit way to say "not this time".

All of the rules about what a name turns into live in
:mod:`py2flamingo.models.user_name`, because GUI tests in this repo skip
wherever PyQt5 is missing (which is everywhere but a workstation). This file
is deliberately only wiring and layout.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
)

from py2flamingo.models.user_name import (
    MAX_STORED_NAME_LENGTH,
    describe_folder_effect,
    normalize_stored_name,
)
from py2flamingo.views.colors import NEUTRAL_BG

logger = logging.getLogger(__name__)

#: What the "no user folder" entry reads as. Spelled out rather than a bare
#: "None" so it cannot be mistaken for someone's actual name in the list.
NO_USER_LABEL = "None - do not create a user folder"

#: Where the convention is written down. Kept as a link rather than prose in
#: the dialog so what the name affects can change without shipping a new build.
DOCS_URL = "https://github.com/uw-loci/Flamingo_Control/blob/main/docs/user_name.md"


class UserNameDialog(QDialog):
    """Startup prompt for the operator's name.

    Example:
        name = UserNameDialog.ask(history=["Sam Nelson"], current="Sam Nelson")
    """

    def __init__(
        self,
        history: Optional[List[str]] = None,
        current: Optional[str] = None,
        parent=None,
    ):
        """
        Args:
            history: Previously used names, most recent first.
            current: The name chosen last time, or None for "no user folder".
            parent: Optional Qt parent.
        """
        super().__init__(parent)
        self.setWindowTitle("Who is using the microscope?")
        self.setMinimumWidth(460)
        self._history = [n for n in (history or []) if normalize_stored_name(n)]
        self._setup_ui(current)

    # ------------------------------------------------------------------ #
    # UI
    # ------------------------------------------------------------------ #

    def _setup_ui(self, current: Optional[str]) -> None:
        layout = QVBoxLayout()

        intro = QLabel(
            "Your name becomes the top-level folder for data saved this "
            "session. Pick yourself from the list, type a new name, or choose "
            "None to save exactly as before."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._combo = QComboBox()
        self._combo.setEditable(True)
        self._combo.lineEdit().setMaxLength(MAX_STORED_NAME_LENGTH)
        self._combo.lineEdit().setPlaceholderText("Type a name...")
        # "None" is index 0 so that Enter on an untouched dialog is the
        # harmless answer, never a half-typed one.
        self._combo.addItem(NO_USER_LABEL)
        for name in self._history:
            self._combo.addItem(name)
        self._combo.currentTextChanged.connect(self._update_effect)
        layout.addWidget(self._combo)

        self._effect = QLabel()
        self._effect.setWordWrap(True)
        self._effect.setTextFormat(Qt.PlainText)
        self._effect.setStyleSheet(
            f"background-color: {NEUTRAL_BG}; padding: 8px; border-radius: 4px;"
        )
        layout.addWidget(self._effect)

        link = QLabel(f'<a href="{DOCS_URL}">What does the user name change?</a>')
        link.setOpenExternalLinks(True)
        link.setTextFormat(Qt.RichText)
        layout.addWidget(link)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self.setLayout(layout)
        self._preselect(current)
        self._update_effect()

    def _preselect(self, current: Optional[str]) -> None:
        """Start on last session's answer, including when that answer was None."""
        stored = normalize_stored_name(current)
        if stored is None:
            self._combo.setCurrentIndex(0)
            return
        idx = self._combo.findText(stored)
        if idx >= 0:
            self._combo.setCurrentIndex(idx)
        else:
            self._combo.setCurrentText(stored)

    def _update_effect(self, *_args) -> None:
        self._effect.setText(describe_folder_effect(self.selected_name()))

    # ------------------------------------------------------------------ #
    # Result
    # ------------------------------------------------------------------ #

    def combo_items(self) -> List[str]:
        """Every entry offered, in order. Exposed for tests and logging."""
        return [self._combo.itemText(i) for i in range(self._combo.count())]

    def selected_name(self) -> Optional[str]:
        """The chosen name, or None for "no user folder".

        Decided by the text, never by the index. On an editable combo the index
        stays at the None entry while someone types over it, so an index test
        reads a freshly typed name as "no user" -- which silently files the run
        with no folder at all.
        """
        text = self._combo.currentText()
        if text.strip() == NO_USER_LABEL:
            return None
        return normalize_stored_name(text)

    @classmethod
    def ask(
        cls,
        history: Optional[List[str]] = None,
        current: Optional[str] = None,
        parent=None,
    ) -> Optional[str]:
        """Show the dialog and return the chosen name, or None.

        Dismissing the dialog returns None, which is the same as choosing the
        None entry: startup must never be blocked by this question, and the
        only safe reading of "went away" is "do not invent a folder".
        """
        dialog = cls(history=history, current=current, parent=parent)
        if dialog.exec_() != QDialog.Accepted:
            logger.info("User name prompt dismissed - no user folder this session")
            return None
        return dialog.selected_name()
