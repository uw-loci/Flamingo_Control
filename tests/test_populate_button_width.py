"""The "Populate from Live" button must not clip its own label.

It rendered as "opulate from Liv" — a character lost off BOTH ends, with no
ellipsis, because QPushButton clips rather than elides. The cause is that Qt's
sizeHint under-counts stylesheet padding: with `padding: 8px 16px` and a bold
face, sizeHint came back 161px while the text alone measured 159px, leaving 2px
for 32px of padding. The layout then honoured that hint and squeezed the button.

Narrower padding and a 9pt face buy the room; an explicit minimum width
guarantees it. The floor is measured against the WIDEST label the button ever
holds, because it swaps to "Stop Populating" while running — sizing for the
initial text alone would clip the other state.

Deliberately does not construct SampleView: that pulls in napari and a live GL
canvas, which segfaults under pytest even offscreen. This pins the sizing rule
on a bare button carrying the same stylesheet.

Run: QT_QPA_PLATFORM=offscreen ./.venv/bin/python -m pytest \
        tests/test_populate_button_width.py -q
"""

import re
from pathlib import Path

import pytest

LABELS = ("Populate from Live", "Stop Populating")
SRC = Path(__file__).resolve().parents[1] / "src/py2flamingo/views/sample_view.py"


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PyQt5")
    from PyQt5.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class TestTheButtonCannotClipItsLabel:
    def _button(self, qapp):
        from PyQt5.QtWidgets import QPushButton

        b = QPushButton(LABELS[0])
        b.setStyleSheet(
            "QPushButton { background-color: #4CAF50; color: white;"
            " font-weight: bold; font-size: 9pt; padding: 8px 10px; }"
        )
        fm = b.fontMetrics()
        b.setMinimumWidth(max(fm.horizontalAdvance(t) for t in LABELS) + 28)
        return b

    def test_a_squeezed_button_still_fits_its_text(self, qapp):
        b = self._button(qapp)
        b.resize(10, 30)  # what the layout did
        widest = max(b.fontMetrics().horizontalAdvance(t) for t in LABELS)
        assert b.width() >= widest

    def test_the_running_label_fits_too(self, qapp):
        """ "Stop Populating" is the other state; both must fit the same floor."""
        b = self._button(qapp)
        b.setText(LABELS[1])
        b.resize(10, 30)
        assert b.width() >= b.fontMetrics().horizontalAdvance(LABELS[1])

    def test_the_floor_exceeds_the_bare_sizehint(self, qapp):
        """The regression: sizeHint alone left no room for the padding."""
        b = self._button(qapp)
        assert b.minimumWidth() > b.sizeHint().width()


class TestTheSourceStillAppliesTheFloor:
    def _populate_block(self):
        src = SRC.read_text(encoding="utf-8")
        i = src.index('QPushButton("Populate from Live")')
        return src[i : i + 1800]

    def test_a_minimum_width_is_set_on_the_button(self):
        assert "populate_btn.setMinimumWidth" in self._populate_block()

    def test_the_floor_is_measured_from_both_labels(self):
        block = self._populate_block()
        assert "Stop Populating" in block, (
            "the floor must account for the running label, which is wider than "
            "nothing and is what the button shows for most of its life"
        )

    def test_the_wide_padding_is_gone(self):
        assert not re.search(r"padding:\s*8px\s+16px", self._populate_block())
