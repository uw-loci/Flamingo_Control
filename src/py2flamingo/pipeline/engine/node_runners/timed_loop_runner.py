"""
TimedLoopRunner — repeats body subgraph N times with configurable delay.

Config:
    iterations: int — number of iterations (0 = indefinite until cancelled)
    interval_seconds: float — delay between iterations
    timing_mode: str — "clock_aligned" or "sequential"
        clock_aligned: start each iteration at fixed intervals relative to
            start time.  If an iteration overruns the interval, the next
            iteration starts immediately and skipped slots are logged.
        sequential: each iteration starts when the previous one finishes,
            plus the delay.

Outputs:
    iteration — SCALAR (0-based index of current iteration)
    elapsed_seconds — SCALAR (seconds since loop start)
    completed — TRIGGER (after all iterations finish)

Body nodes are identified via ScopeResolver (same pattern as ForEach).
"""

import logging
import time

from py2flamingo.pipeline.engine.context import ExecutionContext
from py2flamingo.pipeline.engine.node_runners.base_runner import AbstractNodeRunner
from py2flamingo.pipeline.models.pipeline import Pipeline, PipelineNode
from py2flamingo.pipeline.models.port_types import PortType, PortValue

logger = logging.getLogger(__name__)


class TimedLoopRunner(AbstractNodeRunner):
    """Repeats body nodes on a timed schedule."""

    def __init__(self):
        self._scope_resolver = None
        self._executor = None

    def set_scope_resolver(self, resolver):
        self._scope_resolver = resolver

    def set_executor(self, executor):
        self._executor = executor

    def run(
        self, node: PipelineNode, pipeline: Pipeline, context: ExecutionContext
    ) -> None:
        if not self._scope_resolver or not self._executor:
            raise RuntimeError("TimedLoopRunner requires scope_resolver and executor")

        config = node.config
        max_iterations = int(config.get("iterations", 1))
        interval = float(config.get("interval_seconds", 60.0))
        timing_mode = config.get("timing_mode", "sequential")
        indefinite = max_iterations <= 0

        # Get body node IDs from scope resolver
        body_sorted = self._scope_resolver.get_body_sorted(node.id)
        if not body_sorted:
            logger.warning(f"TimedLoop '{node.name}' has no body nodes")
            self._set_output(node, context, "completed", PortType.TRIGGER, True)
            return

        # Port references for injecting iteration variables
        iteration_port = node.get_output("iteration")
        elapsed_port = node.get_output("elapsed_seconds")

        label = "indefinitely" if indefinite else f"{max_iterations} iterations"
        logger.info(
            f"TimedLoop '{node.name}': starting {label}, "
            f"interval={interval}s, mode={timing_mode}"
        )

        loop_start = time.monotonic()
        idx = 0

        while indefinite or idx < max_iterations:
            if context.check_cancelled():
                raise RuntimeError("Pipeline cancelled during TimedLoop")

            iteration_start = time.monotonic()
            elapsed = iteration_start - loop_start

            logger.info(
                f"TimedLoop '{node.name}': iteration {idx + 1}"
                + (f"/{max_iterations}" if not indefinite else "")
                + f"  (elapsed {elapsed:.1f}s)"
            )

            # Emit progress signal (reuse foreach_iteration)
            if hasattr(self._executor, "foreach_iteration"):
                total = max_iterations if not indefinite else 0
                self._executor.foreach_iteration.emit(node.id, idx + 1, total)

            # Create scoped context for this iteration
            iter_context = context.create_scoped_copy()

            if iteration_port:
                iter_context.set_port_value(
                    iteration_port.id,
                    PortValue(port_type=PortType.SCALAR, data=idx),
                )
            if elapsed_port:
                iter_context.set_port_value(
                    elapsed_port.id,
                    PortValue(port_type=PortType.SCALAR, data=elapsed),
                )

            # Execute body subgraph
            self._executor.execute_subgraph(body_sorted, iter_context)

            idx += 1

            # If done, break before waiting
            if not indefinite and idx >= max_iterations:
                break

            # --- Delay until next iteration ---
            if timing_mode == "clock_aligned":
                # Next iteration should start at loop_start + idx * interval
                next_scheduled = loop_start + idx * interval
                wait = next_scheduled - time.monotonic()
                if wait > 0:
                    self._cancellable_sleep(wait, context)
                else:
                    # Overrun — log how many slots were missed
                    missed = int(-wait / interval) if interval > 0 else 0
                    if missed > 0:
                        logger.warning(
                            f"TimedLoop '{node.name}': iteration overran by "
                            f"{-wait:.1f}s ({missed} interval(s) behind)"
                        )
            else:
                # Sequential: wait full interval after completion
                if interval > 0:
                    self._cancellable_sleep(interval, context)

        elapsed_total = time.monotonic() - loop_start
        self._set_output(node, context, "completed", PortType.TRIGGER, True)
        logger.info(
            f"TimedLoop '{node.name}': completed {idx} iterations "
            f"in {elapsed_total:.1f}s"
        )

    @staticmethod
    def _cancellable_sleep(seconds: float, context: ExecutionContext) -> None:
        """Sleep in small increments so cancellation is responsive."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if context.check_cancelled():
                raise RuntimeError("Pipeline cancelled during TimedLoop delay")
            remaining = end - time.monotonic()
            time.sleep(min(remaining, 0.5))
