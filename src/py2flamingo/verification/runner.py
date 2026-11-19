"""Runs the selected checks in order and puts the instrument back afterwards.

Order is the registration order. A check whose prerequisite did not pass is
BLOCKED with that reason and never run. An abort (button, Ctrl+C) or an
unexpected error in a check that moves, lights or acquires sends the safe
state at once: halt, lasers off, LED off, live view off, workflow stop. Then
every restore a check registered runs, newest first, and each is reported;
a restore that fails is a FAIL in its own right.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from py2flamingo.verification.context import RunContext, RunOptions, Tap, Target
from py2flamingo.verification.model import (
    Aborted,
    AckOnly,
    Check,
    Failed,
    Outcome,
    Result,
    Skipped,
    Tier,
)

logger = logging.getLogger(__name__)

_ACCEPTED = (Outcome.PASS, Outcome.ACK_ONLY)


@dataclass
class RunCallbacks:
    """Hooks for a GUI or CLI. All optional; called on the runner's thread."""

    check_started: Optional[Callable[[int, int, Check], None]] = None
    check_finished: Optional[Callable[[int, int, Result], None]] = None
    step: Optional[Callable[[str], None]] = None


@dataclass
class RunSummary:
    results: List[Result]
    started: float
    finished: float
    aborted: bool = False
    facts: Dict[str, object] = field(default_factory=dict)

    def count(self, outcome: Outcome) -> int:
        return sum(1 for r in self.results if r.outcome == outcome)

    @property
    def ok(self) -> bool:
        return not any(r.outcome.is_problem for r in self.results) and not self.aborted


def safe_state(ctx: RunContext, reason: str) -> List[str]:
    """Stop everything that moves, emits or streams. Never raises."""
    from py2flamingo.verification import codes

    done = []
    ctx.facts["safe_state_reason"] = reason
    # HALT must name the axes (0xFF = all; 0 is "no axis" and is dropped), and
    # the server turns a laser off only when int32Data0 is that laser's number.
    sends = [("STAGE_HALT", 0xFF), ("CAMERA_WORK_FLOW_STOP", 0)]
    sends += [("LASER_DISABLE", line) for line in range(1, 8)]
    sends += [("LED_PREVIEW_DISABLE", 0), ("CAMERA_LIVE_VIEW_STOP", 0)]
    sock = getattr(ctx.connection, "_command_socket", None)
    for name, int0 in sends:
        try:
            code = codes.resolve(name)
            packet = ctx.connection.encoder.encode_command(
                code=code,
                status=0,
                params=[0, 0, 0, int0, 0, 0, 0x80000000],
                value=0.0,
                data=b"",
            )
            if sock is not None:
                sock.sendall(packet)
                done.append(name if not int0 else f"{name}({int0})")
        except Exception as e:  # noqa: BLE001 - must keep going
            logger.warning(f"safe state: {name} not sent: {e}")
    logger.warning(f"Verification safe state ({reason}): sent {', '.join(done)}")
    return done


class Runner:
    def __init__(
        self,
        connection,
        services: Dict[str, object],
        checks: List[Check],
        options: Optional[RunOptions] = None,
        *,
        envelope=None,
        target: Optional[Target] = None,
        callbacks: Optional[RunCallbacks] = None,
        abort: Optional[threading.Event] = None,
    ) -> None:
        self.checks = checks
        self.callbacks = callbacks or RunCallbacks()
        self.abort = abort or threading.Event()
        self.tap = Tap()
        self.ctx = RunContext(
            connection=connection,
            services=services,
            tap=self.tap,
            options=options or RunOptions(),
            abort=self.abort,
            envelope=envelope,
            target=target,
            progress=self.callbacks.step,
        )

    # ------------------------------------------------------------------ run

    def run(self) -> RunSummary:
        started = time.time()
        self.ctx.facts["t0"] = started
        self.tap.install(self.ctx.connection)
        results: List[Result] = []
        by_id: Dict[str, Result] = {}
        aborted = False
        n = len(self.checks)
        try:
            for i, chk in enumerate(self.checks, 1):
                if self.callbacks.check_started:
                    self.callbacks.check_started(i, n, chk)
                if aborted:
                    result = Result(
                        chk.check_id,
                        chk.title,
                        chk.tier,
                        Outcome.SKIPPED,
                        "run aborted",
                    )
                else:
                    blocker = self._blocker(chk, by_id)
                    if blocker:
                        result = Result(
                            chk.check_id, chk.title, chk.tier, Outcome.BLOCKED, blocker
                        )
                    else:
                        result = self._run_one(chk)
                        if (
                            result.outcome == Outcome.SKIPPED
                            and "aborted" in result.detail
                        ):
                            aborted = True
                results.append(result)
                by_id[chk.check_id] = result
                if self.callbacks.check_finished:
                    self.callbacks.check_finished(i, n, result)
        finally:
            results.extend(self._run_restores(aborted or self.abort.is_set()))
            self.tap.remove()
        return RunSummary(results, started, time.time(), aborted, dict(self.ctx.facts))

    def _blocker(self, chk: Check, by_id: Dict[str, Result]) -> str:
        for dep in chk.after:
            prior = by_id.get(dep)
            if prior is None:
                return f"needs {dep}, which was not part of this run"
            if prior.outcome not in _ACCEPTED:
                return f"needs {dep} ({prior.outcome.value}: {prior.detail[:80]})"
        return ""

    def _run_one(self, chk: Check) -> Result:
        ctx = self.ctx
        ctx.notes = []
        ctx.values = {}
        mark = self.tap.mark()
        t = time.monotonic()
        outcome, detail, expected, actual = Outcome.PASS, "", None, None
        try:
            ctx.step(chk.title)
            chk.run(ctx)
        except Failed as e:
            outcome, detail, expected, actual = (
                Outcome.FAIL,
                str(e),
                e.expected,
                e.actual,
            )
        except AckOnly as e:
            outcome, detail = Outcome.ACK_ONLY, str(e)
        except Skipped as e:
            outcome, detail = Outcome.SKIPPED, str(e)
        except Aborted:
            outcome, detail = Outcome.SKIPPED, "run aborted"
            safe_state(ctx, "operator abort")
        except Exception as e:  # noqa: BLE001 - a broken check is a result too
            logger.exception(f"Verification check {chk.check_id} raised")
            outcome, detail = Outcome.ERROR, f"{type(e).__name__}: {e}"
            if chk.tier >= Tier.MOTION:
                safe_state(ctx, f"error in {chk.check_id}")
        notes = "; ".join(ctx.notes)
        if notes:
            detail = f"{detail}; {notes}" if detail else notes
        return Result(
            chk.check_id,
            chk.title,
            chk.tier,
            outcome,
            detail,
            expected,
            actual,
            time.monotonic() - t,
            dict(ctx.values),
            (mark, self.tap.mark()),
        )

    def _run_restores(self, aborted: bool) -> List[Result]:
        out = []
        # Settings go back even after an abort; the abort flag would stop
        # every exchange, so it is cleared for them and set again after.
        self.abort.clear()
        for label, fn, moves in self.ctx.restores():
            mark = self.tap.mark()
            t = time.monotonic()
            if aborted and moves:
                outcome, detail = Outcome.SKIPPED, "not moved back: the run was aborted"
            else:
                try:
                    if self.callbacks.step:
                        self.callbacks.step(f"Restoring {label}")
                    fn()
                    outcome, detail = Outcome.PASS, "restored"
                except Exception as e:  # noqa: BLE001
                    logger.error(f"Verification restore '{label}' failed: {e}")
                    outcome, detail = Outcome.FAIL, f"NOT restored: {e}"
            out.append(
                Result(
                    f"restore.{label}",
                    f"Restore {label}",
                    Tier.SETTINGS,
                    outcome,
                    detail,
                    duration_s=time.monotonic() - t,
                    exchange_span=(mark, self.tap.mark()),
                )
            )
        if aborted:
            self.abort.set()
        return out
