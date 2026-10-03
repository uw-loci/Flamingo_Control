"""Run a short piece of operator-written Python as a pipeline step.

The pipeline's built-in nodes cover the analyzes we anticipated. This node
covers the ones we did not: a thresholding rule nobody asked for yet, a
"is this tile worth imaging" test, a scalar derived from a volume in whatever
way the science needs this week. Writing it here beats adding a node type for
every idea, and it beats the alternative of people editing the source of an
instrument control application.

**What this is not.** The checks below are a guard against mistakes, not a
security boundary. Python cannot be made safe against hostile code in-process,
and nothing here tries: a determined author can get out. The threat being
managed is the ordinary one -- a typo that deletes files, a stray ``import os``
that makes a pipeline unshareable, an attribute poke that corrupts the running
application. Two consequences worth stating plainly:

* **Treat a pipeline file like a script, not like data.** Opening one from
  someone else means running their Python. Read it first.
* **A loop here has no timeout.** ``while True`` hangs the acquisition, because
  the node runs in the calling thread. The fix for that is isolation, not more
  parsing.

**TODO: move this to an Appose-powered worker.** In-process execution was the
deliberate first step, but it has two ceilings that parsing cannot raise: a
runaway body cannot be interrupted, and every invocation inside a ForEach or
TimedLoop hands the worker a fresh copy of the volume. Appose
(https://github.com/apposed/appose) gives both a supervised worker process and
shared-memory array handoff, so a node inside a loop stops paying a copy per
iteration -- which is where this design will hurt first on real stacks. The
seam is already in place: :func:`run_code` is the only entry point, and
``PythonFunctionRunner`` calls nothing else, so the backend can change without
touching node definitions, ports, or saved pipeline files. The existing
subprocess pattern for Leonardo and flat-field (``isolated_service.py`` /
``isolated_worker.py``) is the nearest precedent in this repo.

The design decision that makes the rest simple: **the libraries are provided,
never imported.** Everything useful is already in the namespace, so ``import``
is rejected outright and the set of things this code can reach is exactly the
list in :data:`ALLOWED_LIBRARIES`. There is no "which version of what is
installed" question at the point of writing a node.
"""

from __future__ import annotations

import ast
import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: The function the user's body is wrapped in. Wrapping means ``return`` works
#: the way anyone would expect it to, instead of a magic "assign to result".
_WRAPPER_NAME = "_node_main"

#: How far every reported line number is shifted by that wrapper.
_WRAPPER_LINE_OFFSET = 1

#: Names that are never available, even though a plain exec would provide them.
#: Each one is either a way out of the namespace or a way to touch the disk.
_FORBIDDEN_NAMES = frozenset(
    {
        "__import__",
        "breakpoint",
        "compile",
        "delattr",
        "dir",
        "eval",
        "exec",
        "exit",
        "getattr",
        "globals",
        "help",
        "input",
        "locals",
        "memoryview",
        "object",
        "open",
        "quit",
        "setattr",
        "super",
        "type",
        "vars",
    }
)

#: Builtins the body may use. Deliberately short: this is for arithmetic and
#: array work, and anything absent can be done with numpy.
_SAFE_BUILTINS = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "divmod": divmod,
    "enumerate": enumerate,
    "filter": filter,
    "float": float,
    "int": int,
    "isinstance": isinstance,
    "len": len,
    "list": list,
    "map": map,
    "max": max,
    "min": min,
    "pow": pow,
    "range": range,
    "reversed": reversed,
    "round": round,
    "set": set,
    "slice": slice,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "zip": zip,
    # Raising a clear error is how a node says "these inputs are wrong".
    "ValueError": ValueError,
    "TypeError": TypeError,
    "RuntimeError": RuntimeError,
    "Exception": Exception,
    "True": True,
    "False": False,
    "None": None,
    # The real __import__, deliberately. numpy and scikit-image import
    # submodules lazily from inside their own calls, and a namespace without
    # this raises KeyError('__import__') from the middle of someone's
    # threshold. The body cannot reach it: the validator rejects the name and
    # every dunder attribute before a line of this ever runs, so the block is
    # at parse time and the runtime stays functional.
    "__import__": __import__,
}

#: What the body can use, and the name it uses it by. This list IS the contract
#: -- it is what the node's help text shows and what the docs promise.
ALLOWED_LIBRARIES: Dict[str, str] = {
    "np": "numpy",
    "ndi": "scipy.ndimage",
    "filters": "skimage.filters",
    "measure": "skimage.measure",
    "morphology": "skimage.morphology",
    "exposure": "skimage.exposure",
    "math": "math",
}

#: Keys a body may return. Anything else is a typo worth catching, because a
#: misspelled key would silently produce a node that outputs nothing.
RESULT_KEYS = ("value", "boolean", "mask", "objects", "result")


#: What a new node starts with. A working example beats an empty box: it shows
#: the signature, the available names and the return shape in one read, and it
#: runs as-is so a new node can be wired up before it is written.
DEFAULT_CODE = """\
# Inputs: volume (one 3-D array), volumes ({channel: array}), value,
# objects, params.  log() writes to the run log.
# Available: np, ndi, filters, measure, morphology, exposure, math

threshold = filters.threshold_otsu(volume)
mask = volume > threshold
log("threshold", threshold, "-> voxels", int(mask.sum()))

return {
    "mask": mask,
    "value": float(threshold),
    "boolean": bool(mask.sum() > 0),
}
"""


class NodeCodeError(Exception):
    """Raised when a node's code is rejected or fails while running.

    Attributes:
        line: 1-based line in the user's own text, or None when not known.
    """

    def __init__(self, message: str, line: Optional[int] = None):
        super().__init__(message)
        self.line = line

    def __str__(self) -> str:
        base = super().__str__()
        return f"line {self.line}: {base}" if self.line else base


@dataclass
class NodeResult:
    """What a body returned, normalized into the node's output ports.

    Attributes:
        value: Scalar output.
        boolean: True/False output, also used to pick a branch.
        mask: Volume output.
        objects: Object-list output.
        result: Untyped pass-through.
        log_lines: Whatever the body passed to ``log()``.
    """

    value: Any = None
    boolean: Optional[bool] = None
    mask: Any = None
    objects: Any = None
    result: Any = None
    log_lines: List[str] = field(default_factory=list)


def _library_namespace() -> Dict[str, Any]:
    """Import the allowed libraries once, tolerating a missing optional one.

    A node that only needs numpy must still work on a machine where an optional
    dependency failed to install, and the failure must name the library rather
    than surfacing as a mysterious NameError inside someone's code.
    """
    namespace: Dict[str, Any] = {"math": math}
    try:
        import numpy as np

        namespace["np"] = np
    except ImportError:  # pragma: no cover - numpy is a hard dependency
        logger.error("numpy unavailable - Python function nodes cannot run")
    try:
        import scipy.ndimage as ndi

        namespace["ndi"] = ndi
    except ImportError:  # pragma: no cover - optional at runtime
        logger.warning("scipy.ndimage unavailable to Python function nodes")
    try:
        from skimage import exposure, filters, measure, morphology

        namespace.update(
            filters=filters,
            measure=measure,
            morphology=morphology,
            exposure=exposure,
        )
    except ImportError:  # pragma: no cover - optional at runtime
        logger.warning("scikit-image unavailable to Python function nodes")
    return namespace


class _Validator(ast.NodeVisitor):
    """Reject the constructs that make a node unshareable or unsafe-by-accident.

    Runs before anything executes, so a bad node fails when it is saved rather
    than halfway through an acquisition.
    """

    def __init__(self) -> None:
        self.errors: List[NodeCodeError] = []

    def _reject(self, node: ast.AST, message: str) -> None:
        line = getattr(node, "lineno", None)
        if line is not None:
            line = max(1, line - _WRAPPER_LINE_OFFSET)
        self.errors.append(NodeCodeError(message, line))

    def visit_Import(self, node: ast.Import) -> None:
        self._reject(
            node,
            "import is not allowed - the libraries are already available "
            f"({', '.join(sorted(ALLOWED_LIBRARIES))})",
        )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.visit_Import(node)  # type: ignore[arg-type]

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # Dunder attributes are the standard way out of a restricted namespace
        # (``[].__class__.__bases__`` and friends), so they go regardless of
        # what they are attached to.
        if node.attr.startswith("__"):
            self._reject(node, f"attribute '{node.attr}' is not allowed")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in _FORBIDDEN_NAMES:
            self._reject(node, f"'{node.id}' is not available in a node")
        elif node.id.startswith("__"):
            self._reject(node, f"name '{node.id}' is not allowed")
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        self._reject(node, "global is not allowed in a node")

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self._reject(node, "nonlocal is not allowed in a node")


def validate_code(code: str) -> List[NodeCodeError]:
    """Check a node body without running it.

    Called when the node is edited and again before execution, so a mistake
    surfaces in the editor rather than mid-run.

    Args:
        code: The body the operator typed.

    Returns:
        Every problem found, empty when the body is acceptable.
    """
    if not code or not code.strip():
        return [NodeCodeError("the node has no code")]
    try:
        tree = ast.parse(_wrap(code), filename="<node>")
    except SyntaxError as e:
        line = max(1, (e.lineno or 1) - _WRAPPER_LINE_OFFSET)
        return [NodeCodeError(f"syntax error: {e.msg}", line)]

    validator = _Validator()
    validator.visit(tree)
    return validator.errors


def _wrap(code: str) -> str:
    """Put the body inside a function so ``return`` means what it looks like."""
    body = "\n".join("    " + line for line in code.splitlines())
    return (
        f"def {_WRAPPER_NAME}(volume, volumes, value, objects, params, log):\n{body}\n"
    )


def _as_bool(returned: Any) -> Optional[bool]:
    """True/False if this is a boolean, else None.

    ``volume.max() > 5`` is the obvious way to write a true/false step, and it
    produces a ``numpy.bool_`` -- which is not a subclass of ``bool``, so a
    plain isinstance check quietly leaves the branch output empty. Zero-length
    and multi-element arrays are deliberately NOT booleans here: numpy raises
    on their truthiness, and guessing would pick a branch at random.
    """
    if returned is None or isinstance(returned, bool):
        return returned
    try:
        import numpy as np
    except ImportError:  # pragma: no cover - numpy is a hard dependency
        return None
    if isinstance(returned, np.bool_):
        return bool(returned)
    if isinstance(returned, np.ndarray) and returned.dtype == bool:
        return bool(returned) if returned.size == 1 else None
    return None


def _normalize(returned: Any, log_lines: List[str]) -> NodeResult:
    """Turn what the body returned into typed outputs.

    A dict maps onto the named ports; anything else is the untyped ``result``,
    so the simplest possible body (``return volume.mean()``) still works.
    """
    if isinstance(returned, dict):
        unknown = set(returned) - set(RESULT_KEYS)
        if unknown:
            raise NodeCodeError(
                f"unknown output key(s): {', '.join(sorted(unknown))}. "
                f"Use one of: {', '.join(RESULT_KEYS)}"
            )
        boolean = _as_bool(returned.get("boolean"))
        return NodeResult(
            value=returned.get("value"),
            boolean=boolean,
            mask=returned.get("mask"),
            objects=returned.get("objects"),
            result=returned.get("result"),
            log_lines=log_lines,
        )
    bare_bool = _as_bool(returned)
    if bare_bool is not None:
        # A bare True/False is obviously meant as the branch decision.
        return NodeResult(boolean=bare_bool, result=returned, log_lines=log_lines)
    return NodeResult(result=returned, log_lines=log_lines)


def run_code(
    code: str,
    *,
    volume: Any = None,
    volumes: Optional[Dict[int, Any]] = None,
    value: Any = None,
    objects: Any = None,
    params: Optional[Dict[str, Any]] = None,
    max_log_lines: int = 200,
) -> NodeResult:
    """Validate and execute a node body.

    Args:
        code: The body the operator typed.
        volume: A single 3-D ``(Z, Y, X)`` array for the body to work on.
        volumes: Every channel as ``{channel_id: array}``. A VOLUME port may
            carry either one array or a channel dict (see
            ``threshold_runner.py:85``), so the runner normalizes both and the
            body gets one of each: ``volume`` for the common single-channel
            case, ``volumes`` when it needs the others.
        value: Value on the node's ``value`` input.
        objects: Value on the node's ``objects`` input.
        params: The node's own parameters, as a plain dict.
        max_log_lines: Cap on captured ``log()`` output, so a call inside a
            loop cannot exhaust memory.

    Returns:
        The normalized outputs.

    Raises:
        NodeCodeError: If the body is rejected, or raises while running.
    """
    errors = validate_code(code)
    if errors:
        raise errors[0]

    log_lines: List[str] = []

    def log(*parts: Any) -> None:
        """Record a message for the node's log output."""
        if len(log_lines) < max_log_lines:
            log_lines.append(" ".join(str(p) for p in parts))
        elif len(log_lines) == max_log_lines:
            log_lines.append(f"... log truncated at {max_log_lines} lines")

    namespace: Dict[str, Any] = _library_namespace()
    namespace["__builtins__"] = dict(_SAFE_BUILTINS)

    try:
        exec(compile(_wrap(code), "<node>", "exec"), namespace)  # noqa: S102
        returned = namespace[_WRAPPER_NAME](
            volume, dict(volumes or {}), value, objects, params or {}, log
        )
    except NodeCodeError:
        raise
    except Exception as e:
        raise NodeCodeError(f"{type(e).__name__}: {e}", _failing_line(e)) from e

    return _normalize(returned, log_lines)


def _failing_line(exc: BaseException) -> Optional[int]:
    """The line of the user's own text that raised, if it was their line.

    Walks to the innermost frame belonging to the node so that an error raised
    deep inside numpy still points at the line that called numpy.
    """
    tb = exc.__traceback__
    line = None
    while tb is not None:
        if tb.tb_frame.f_code.co_filename == "<node>":
            line = tb.tb_lineno
        tb = tb.tb_next
    return None if line is None else max(1, line - _WRAPPER_LINE_OFFSET)
