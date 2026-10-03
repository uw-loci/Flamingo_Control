"""The Python function node: what a body may do, and what it may not.

These are the load-bearing tests for the feature. The guard is explicitly not a
security boundary, so what is pinned here is the *mistake* surface -- the things
an ordinary author does by accident that would otherwise reach the filesystem,
the running application, or a pipeline file someone else opens.

Run: .venv/bin/python -m pytest tests/test_python_function_node.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from py2flamingo.pipeline.models.pipeline import (  # noqa: E402
    NODE_COLORS,
    NodeType,
    create_default_ports,
)
from py2flamingo.pipeline.models.python_function import (  # noqa: E402
    ALLOWED_LIBRARIES,
    DEFAULT_CODE,
    RESULT_KEYS,
    NodeCodeError,
    run_code,
    validate_code,
)


@pytest.fixture
def volume():
    v = np.zeros((8, 8, 8), dtype=np.float32)
    v[2:5, 2:5, 2:5] = 10.0
    return v


class TestItRunsOrdinaryCode:
    def test_the_shipped_default_runs_as_is(self, volume):
        # A new node must be wireable before it is written.
        assert validate_code(DEFAULT_CODE) == []
        assert run_code(DEFAULT_CODE, volume=volume).mask.sum() == 27

    def test_a_bare_return_becomes_the_result(self, volume):
        assert run_code("return volume.mean()", volume=volume).result == pytest.approx(
            volume.mean()
        )

    def test_a_dict_maps_onto_the_named_outputs(self, volume):
        r = run_code(
            "return {'value': 1.5, 'boolean': True, 'mask': volume > 5}",
            volume=volume,
        )
        assert (r.value, r.boolean) == (1.5, True)
        assert r.mask.sum() == 27

    def test_every_advertised_library_is_actually_there(self, volume):
        # The help text promises these by name; a missing one is a broken promise.
        for name in ALLOWED_LIBRARIES:
            assert run_code(f"return {name} is not None", volume=volume).boolean

    def test_log_output_is_captured(self, volume):
        r = run_code("log('hello', 42)\nreturn 1", volume=volume)
        assert r.log_lines == ["hello 42"]

    def test_runaway_logging_is_capped(self, volume):
        r = run_code(
            "for i in range(10000):\n    log(i)\nreturn 1",
            volume=volume,
            max_log_lines=10,
        )
        assert len(r.log_lines) == 11 and "truncated" in r.log_lines[-1]

    def test_inputs_that_are_not_connected_are_none(self):
        assert run_code("return volume is None and value is None").boolean


class TestNumpyBooleansPickABranch:
    """The natural way to write a true/false step returns numpy, not bool."""

    def test_a_numpy_comparison_sets_the_boolean_output(self, volume):
        assert run_code("return volume.max() > 5", volume=volume).boolean is True
        assert run_code("return volume.max() > 500", volume=volume).boolean is False

    def test_it_is_a_real_python_bool(self, volume):
        # Anything downstream may branch on this; numpy.bool_ leaks oddly.
        assert type(run_code("return volume.max() > 5", volume=volume).boolean) is bool

    def test_an_array_is_not_a_branch_decision(self, volume):
        # numpy raises on the truthiness of a multi-element array; guessing
        # would silently choose a branch.
        assert run_code("return volume > 5", volume=volume).boolean is None

    def test_a_non_boolean_leaves_the_branch_unset(self, volume):
        # An analysis step is not a decision and must not look like one.
        assert run_code("return volume.mean()", volume=volume).boolean is None


class TestItRejectsMistakes:
    @pytest.mark.parametrize(
        "code",
        [
            "import os",
            "from os import path",
            "from pathlib import Path",
            "import numpy",
        ],
    )
    def test_imports_are_refused(self, code):
        assert any("import is not allowed" in str(e) for e in validate_code(code))

    @pytest.mark.parametrize(
        "code",
        [
            "return open('/etc/passwd').read()",
            "return eval('1')",
            "return exec('x=1')",
            "return __import__('os')",
            "return globals()",
            "return getattr(volume, 'shape')",
        ],
    )
    def test_the_ways_out_are_refused(self, code):
        assert validate_code(code), f"not rejected: {code}"

    def test_dunder_attributes_are_refused(self):
        # []. __class__.__bases__ is the standard namespace escape.
        assert validate_code("return [].__class__")
        assert validate_code("return volume.__dict__")

    def test_global_and_nonlocal_are_refused(self):
        assert validate_code("global x")
        assert validate_code("def f():\n    nonlocal y")

    def test_an_empty_node_is_an_error_not_a_silent_pass(self):
        assert validate_code("") and validate_code("   \n  ")

    def test_a_misspelled_output_key_is_caught(self, volume):
        # Silently dropping it would produce a node that outputs nothing.
        with pytest.raises(NodeCodeError) as exc:
            run_code("return {'valeu': 1}", volume=volume)
        assert "valeu" in str(exc.value)
        assert all(k in str(exc.value) for k in ("value", "boolean"))

    def test_validation_happens_before_anything_runs(self, tmp_path):
        # The rejected line must never execute, not merely fail afterwards.
        victim = tmp_path / "victim.txt"
        victim.write_text("intact")
        with pytest.raises(NodeCodeError):
            run_code(f"open({str(victim)!r}, 'w').write('destroyed')")
        assert victim.read_text() == "intact"


class TestErrorsPointAtTheRightLine:
    def test_a_syntax_error_reports_the_users_line(self):
        errors = validate_code("x = 1\ny = (\n")
        assert errors and errors[0].line is not None

    def test_a_runtime_error_reports_the_users_line(self, volume):
        with pytest.raises(NodeCodeError) as exc:
            run_code("a = 1\nb = 2\nreturn 1 / 0", volume=volume)
        # Line 3 of what the operator typed, not line 4 of the wrapped form.
        assert exc.value.line == 3

    def test_a_rejected_line_is_numbered_from_the_users_text(self):
        errors = validate_code("a = 1\nimport os")
        assert errors[0].line == 2

    def test_the_message_names_the_offending_thing(self):
        assert "open" in str(validate_code("return open('x')")[0])


class TestTheNodeIsWiredUp:
    def test_it_has_the_ports_its_runner_writes(self):
        _, outputs = create_default_ports(NodeType.PYTHON_FUNCTION)
        names = {p.name for p in outputs}
        assert set(RESULT_KEYS) <= names
        assert {"true_branch", "false_branch", "completed"} <= names

    def test_its_inputs_match_the_names_bodies_use(self):
        inputs, _ = create_default_ports(NodeType.PYTHON_FUNCTION)
        assert {p.name for p in inputs} == {"volume", "value", "objects", "trigger"}

    def test_it_has_its_own_colour(self):
        assert NodeType.PYTHON_FUNCTION in NODE_COLORS
        others = [
            c for t, c in NODE_COLORS.items() if t is not NodeType.PYTHON_FUNCTION
        ]
        assert NODE_COLORS[NodeType.PYTHON_FUNCTION] not in others

    @pytest.mark.parametrize(
        "module",
        [
            "py2flamingo.pipeline.controllers.pipeline_controller",
            "py2flamingo.pipeline.headless_services",
        ],
    )
    def test_both_runner_maps_know_about_it(self, module):
        # Registered in two places; missing one makes the node a no-op in
        # exactly one execution mode, which is a miserable thing to debug.
        source = Path(
            str(Path(__file__).resolve().parents[1] / "src")
            + "/"
            + module.replace(".", "/")
            + ".py"
        ).read_text()
        assert "NodeType.PYTHON_FUNCTION: PythonFunctionRunner()" in source

    def test_the_palette_offers_it(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "src/py2flamingo/pipeline/ui/node_palette.py"
        ).read_text()
        assert "NodeType.PYTHON_FUNCTION:" in source


class TestTheVolumePortCarriesEitherShape:
    """A VOLUME port may hold one array OR a {channel: array} dict.

    SAMPLE_VIEW_DATA emits the dict, a single-channel ``--input`` emits an
    array, and ThresholdRunner handles both (threshold_runner.py:85). Passing
    the raw value through made ``volume > 200`` raise
    ``TypeError: '>' not supported between 'dict' and 'int'`` for any body fed
    by a Sample View Data node -- which is the first thing anyone wires up.
    """

    @staticmethod
    def _normalize(raw):
        from py2flamingo.pipeline.engine.node_runners.python_function_runner import (
            _normalize_volume_input,
        )

        return _normalize_volume_input(raw)

    def test_a_bare_array_is_passed_through_and_also_offered_as_channel_zero(
        self, volume
    ):
        one, many = self._normalize(volume)
        assert one is volume
        assert many == {0: volume}

    def test_a_channel_dict_yields_an_array_for_volume(self, volume):
        one, many = self._normalize({0: volume, 1: volume * 2})
        assert one is volume
        assert set(many) == {0, 1}

    def test_the_chosen_channel_is_the_lowest_id_not_insertion_order(self, volume):
        # Same pipeline must pick the same channel on every run.
        hi, lo = volume * 2, volume
        one, _ = self._normalize({3: hi, 1: lo})
        assert one is lo

    def test_an_unconnected_port_is_none_and_an_empty_mapping(self):
        # So a body can test `volume is None` rather than catch a TypeError.
        assert self._normalize(None) == (None, {})
        assert self._normalize({}) == (None, {})

    def test_a_body_can_reach_the_other_channels(self, volume):
        from py2flamingo.pipeline.models.python_function import run_code

        r = run_code(
            "return {'value': float(len(volumes))}",
            volume=volume,
            volumes={0: volume, 1: volume},
        )
        assert r.value == 2.0

    def test_volumes_defaults_to_empty_rather_than_none(self, volume):
        from py2flamingo.pipeline.models.python_function import run_code

        # An unset `volumes` must still be iterable in a body.
        assert run_code("return float(len(volumes))", volume=volume).result == 0.0
