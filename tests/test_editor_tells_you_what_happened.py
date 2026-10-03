"""The editor has to say what went wrong, and not lose work silently.

Four faults, all of which left the operator with no way to find out what the
editor had decided:

* A refused wire vanished with no message. The four good explanations existed
  in the model and went to `logger.info`, where a biologist never looks.
* Validation named connections by `uuid4`, a string that appears nowhere on the
  canvas.
* There was no unsaved-changes flag at all, so New or a window close discarded
  an hour of wiring without asking.
* A failed run left a green "Ready" in the toolbar, byte-identical to success,
  and raised no modal although open/save/validate failures all did.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest \
        tests/test_editor_tells_you_what_happened.py -q
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


@pytest.fixture
def editor(app):
    from py2flamingo.pipeline.ui.pipeline_editor_dialog import PipelineEditorDialog

    d = PipelineEditorDialog()
    yield d
    d.deleteLater()


def _two_thresholds(scene):
    from py2flamingo.pipeline.models.pipeline import NodeType

    scene.add_node(NodeType.THRESHOLD, x=0, y=0)
    scene.add_node(NodeType.THRESHOLD, x=300, y=0)
    from py2flamingo.pipeline.ui.port_item import PortItem

    return [i for i in scene.items() if isinstance(i, PortItem)]


class TestARefusedWireExplainsItself:
    def test_a_type_mismatch_names_both_ends_in_words(self, editor):
        ports = _two_thresholds(editor._scene)
        src = next(p for p in ports if p.port.name == "objects" and p.is_output)
        tgt = [p for p in ports if p.port.name == "volume" and p.is_input][1]
        reason = editor._scene._describe_refusal(src, tgt)
        assert "objects" in reason and "volume" in reason
        assert "list of detected objects" in reason
        assert "3-D image stack" in reason

    def test_it_does_not_leak_the_enum_identifier(self, editor):
        ports = _two_thresholds(editor._scene)
        src = next(p for p in ports if p.port.name == "objects" and p.is_output)
        tgt = [p for p in ports if p.port.name == "volume" and p.is_input][1]
        assert "OBJECT_LIST" not in editor._scene._describe_refusal(src, tgt)

    def test_wiring_a_node_to_itself_says_so(self, editor):
        ports = _two_thresholds(editor._scene)
        src = next(p for p in ports if p.port.name == "objects" and p.is_output)
        same = next(p for p in ports if p.is_input and p.node_item is src.node_item)
        assert "itself" in editor._scene._describe_refusal(src, same)

    def test_an_output_target_explains_the_direction(self, editor):
        ports = _two_thresholds(editor._scene)
        src = next(p for p in ports if p.port.name == "objects" and p.is_output)
        out = [p for p in ports if p.port.name == "mask" and p.is_output][1]
        assert "output" in editor._scene._describe_refusal(src, out)

    def test_the_reason_reaches_the_log_pane(self, editor):
        editor._scene.connection_rejected.emit("Cannot connect A.x to B.y")
        assert "Cannot connect A.x to B.y" in editor._log_text.toPlainText()


class TestValidationNamesWhatIsWrong:
    @staticmethod
    def _pipeline_with_bad_wire():
        from py2flamingo.pipeline.models.pipeline import (
            Connection,
            NodeType,
            Pipeline,
            create_node,
        )

        p = Pipeline(name="t")
        a = create_node(NodeType.THRESHOLD, name="Detect beads")
        b = create_node(NodeType.THRESHOLD, name="Second pass")
        p.add_node(a)
        p.add_node(b)
        p.connections["c"] = Connection(
            id="c",
            source_node_id=a.id,
            source_port_id=a.get_output("objects").id,
            target_node_id=b.id,
            target_port_id=b.get_input("volume").id,
        )
        return p

    def test_a_type_mismatch_names_nodes_not_uuids(self):
        errors = self._pipeline_with_bad_wire().validate()
        assert len(errors) == 1
        assert "Detect beads.objects" in errors[0]
        assert "Second pass.volume" in errors[0]
        assert "c" != errors[0]  # not the bare connection id

    def test_a_cycle_names_the_loop(self):
        from py2flamingo.pipeline.models.pipeline import (
            Connection,
            NodeType,
            Pipeline,
            create_node,
        )

        p = Pipeline(name="cyc")
        nodes = [create_node(NodeType.THRESHOLD, name=f"Stage {i}") for i in range(3)]
        for n in nodes:
            p.add_node(n)
        for i in range(3):
            s, t = nodes[i], nodes[(i + 1) % 3]
            p.connections[f"k{i}"] = Connection(
                id=f"k{i}",
                source_node_id=s.id,
                source_port_id=s.get_output("mask").id,
                target_node_id=t.id,
                target_port_id=t.get_input("volume").id,
            )
        message = " ".join(p.validate())
        assert "Stage 0" in message and "Stage 1" in message and "Stage 2" in message

    def test_a_dangling_wire_is_reported_not_crashed(self):
        """validate() used to raise KeyError before it could report this."""
        from py2flamingo.pipeline.models.pipeline import (
            Connection,
            NodeType,
            Pipeline,
            create_node,
        )

        p = Pipeline(name="orphan")
        a = create_node(NodeType.THRESHOLD, name="Alone")
        p.add_node(a)
        p.connections["x"] = Connection(
            id="x",
            source_node_id=a.id,
            source_port_id=a.get_output("mask").id,
            target_node_id="deleted",
            target_port_id="deleted",
        )
        errors = p.validate()  # must not raise
        assert any("Alone" in e and "no longer exists" in e for e in errors)

    def test_topological_sort_tolerates_a_dangling_wire(self):
        from py2flamingo.pipeline.models.pipeline import (
            Connection,
            NodeType,
            Pipeline,
            create_node,
        )

        p = Pipeline(name="orphan")
        a = create_node(NodeType.THRESHOLD, name="Alone")
        p.add_node(a)
        p.connections["x"] = Connection(
            id="x",
            source_node_id=a.id,
            source_port_id="p",
            target_node_id="deleted",
            target_port_id="q",
        )
        assert p.topological_sort() == [a.id]


class TestUnsavedWorkIsNotLostSilently:
    def test_a_fresh_editor_is_clean(self, editor):
        assert editor._dirty is False
        assert "*" not in editor.windowTitle()

    def test_adding_a_node_marks_it_dirty(self, editor):
        from py2flamingo.pipeline.models.pipeline import NodeType

        editor._scene.add_node(NodeType.THRESHOLD, x=0, y=0)
        assert editor._dirty is True
        assert editor.windowTitle().endswith("*")

    def test_editing_a_setting_marks_it_dirty(self, editor):
        # An hour of retuning thresholds used to count as no change at all.
        editor._property_panel.node_edited.emit()
        assert editor._dirty is True

    def test_saving_clears_it(self, editor):
        from py2flamingo.pipeline.models.pipeline import NodeType

        editor._scene.add_node(NodeType.THRESHOLD, x=0, y=0)
        editor._mark_clean()
        assert editor._dirty is False
        assert "*" not in editor.windowTitle()

    def test_a_clean_pipeline_needs_no_prompt(self, editor):
        assert editor._confirm_discard("Close") is True

    def test_the_editor_has_a_close_handler(self, editor):
        # There was none, so the window close could not be intercepted.
        assert type(editor).closeEvent is not None
        source = Path(
            Path(__file__).resolve().parents[1]
            / "src/py2flamingo/pipeline/ui/pipeline_editor_dialog.py"
        ).read_text()
        assert "def closeEvent" in source
        # PersistentDialog needs the super call for geometry persistence.
        assert "super().closeEvent(event)" in source


class TestTheStatusWordReportsTheOutcome:
    def test_a_finished_run_does_not_say_ready(self, editor):
        editor.on_pipeline_completed()
        assert editor._status_label.text() == "Completed"

    def test_a_failed_run_says_so_in_red(self, editor, monkeypatch):
        from PyQt5.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
        editor.on_pipeline_error("Node 'Threshold' failed: boom")
        assert "Failed" in editor._status_label.text()
        assert "ef5350" in editor._status_label.styleSheet()

    def test_a_failed_run_raises_a_modal(self, editor, monkeypatch):
        from PyQt5.QtWidgets import QMessageBox

        shown = []
        monkeypatch.setattr(
            QMessageBox, "warning", lambda *a, **k: shown.append(a[1:3])
        )
        editor.on_pipeline_error("boom")
        assert shown, "a failed run must not be reported by the log alone"

    def test_the_modal_names_the_node_that_failed(self, editor, monkeypatch):
        from PyQt5.QtWidgets import QMessageBox

        from py2flamingo.pipeline.models.pipeline import NodeType

        node_id = editor._scene.add_node(NodeType.THRESHOLD, x=0, y=0)
        editor._pipeline.get_node(node_id).name = "Detect beads"
        editor.on_node_error(node_id, "boom")

        shown = []
        monkeypatch.setattr(
            QMessageBox, "warning", lambda *a, **k: shown.append(str(a[2]))
        )
        editor.on_pipeline_error("boom")
        assert shown and "Detect beads" in shown[0]

    def test_a_user_stop_is_not_reported_as_a_failure(self, editor, monkeypatch):
        from PyQt5.QtWidgets import QMessageBox

        shown = []
        monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a))
        editor._on_stop()
        editor.on_pipeline_error("Pipeline cancelled by user")
        assert editor._status_label.text() == "Stopped"
        assert not shown, "a deliberate stop must not raise an alarm"

    def test_idle_still_reads_ready(self, editor):
        editor._set_running(False)
        assert editor._status_label.text() == "Ready"
