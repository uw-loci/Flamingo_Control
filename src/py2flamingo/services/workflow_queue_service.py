# ============================================================================
# src/py2flamingo/services/workflow_queue_service.py
"""
Workflow Queue Service for sequential workflow execution.

This service manages a queue of workflow files and executes them sequentially,
waiting for each workflow to complete before starting the next. This is critical
for tile collection where multiple Z-stack workflows must run one after another.

Workflow completion is detected by:
1. Polling system state (SYSTEM_STATE_GET) until IDLE
2. Optional timeout based on estimated workflow duration

Architecture:
    WorkflowQueueService
        └── Accepts list of workflow files
        └── Executes sequentially via WorkflowController
        └── Polls system state for completion detection
        └── Emits signals for progress updates
        └── Handles cancellation gracefully
"""

import logging
import threading
import time
from pathlib import Path
from typing import List, Optional, Dict, Any, Callable, TYPE_CHECKING
from dataclasses import dataclass, field

from PyQt5.QtCore import QObject, pyqtSignal

if TYPE_CHECKING:
    from ..controllers.workflow_controller import WorkflowController
    from ..services import MVCConnectionService


logger = logging.getLogger(__name__)


@dataclass
class WorkflowQueueItem:
    """Item in the workflow queue."""
    file_path: Path
    metadata: Dict[str, Any] = field(default_factory=dict)  # Position info, etc.
    started: bool = False
    completed: bool = False
    error: Optional[str] = None


class WorkflowQueueService(QObject):
    """
    Service for sequential workflow execution.

    Manages a queue of workflow files and executes them one at a time,
    waiting for each to complete before starting the next.

    Signals:
        queue_started: Emitted when queue execution begins
        workflow_started: Emitted when individual workflow starts (index, total, path)
        workflow_completed: Emitted when individual workflow completes (index, total, path)
        queue_completed: Emitted when all workflows complete
        queue_cancelled: Emitted if queue is cancelled
        progress_updated: Emitted with progress info (current, total, message)
        error_occurred: Emitted on error (message)
    """

    queue_started = pyqtSignal()
    workflow_started = pyqtSignal(int, int, str)  # index, total, path
    workflow_completed = pyqtSignal(int, int, str)  # index, total, path
    queue_completed = pyqtSignal()
    queue_cancelled = pyqtSignal()
    progress_updated = pyqtSignal(int, int, str)  # current, total, message
    error_occurred = pyqtSignal(str)

    # Poll interval for checking system state (seconds)
    STATE_POLL_INTERVAL = 2.0

    # Maximum time to wait for a workflow (seconds) - 30 minutes default
    MAX_WORKFLOW_TIMEOUT = 1800

    # Minimum wait between workflows (seconds) - ensures system settles
    MIN_INTER_WORKFLOW_DELAY = 1.0

    def __init__(self,
                 workflow_controller: 'WorkflowController',
                 connection_service: Optional['MVCConnectionService'] = None,
                 status_indicator_service=None):
        """
        Initialize workflow queue service.

        Args:
            workflow_controller: Controller for executing workflows
            connection_service: Service for querying system state
            status_indicator_service: Service for workflow status events
        """
        super().__init__()

        self._workflow_controller = workflow_controller
        self._connection_service = connection_service
        self._status_indicator_service = status_indicator_service

        # Queue state
        self._queue: List[WorkflowQueueItem] = []
        self._current_index = 0
        self._is_running = False
        self._cancel_requested = False

        # Execution thread
        self._execution_thread: Optional[threading.Thread] = None

        # Callbacks for tile position (for Sample View integration)
        self._on_workflow_start_callback: Optional[Callable[[Path, Dict], None]] = None

        logger.info("WorkflowQueueService initialized")

    def set_workflow_start_callback(self, callback: Callable[[Path, Dict], None]) -> None:
        """
        Set callback for when individual workflow starts.

        Used for Sample View integration to set tile position metadata.

        Args:
            callback: Function(file_path, metadata) called before each workflow
        """
        self._on_workflow_start_callback = callback

    def enqueue(self, workflow_files: List[Path],
                metadata_list: Optional[List[Dict[str, Any]]] = None) -> None:
        """
        Add workflow files to the queue.

        Args:
            workflow_files: List of workflow file paths
            metadata_list: Optional list of metadata dicts for each workflow
        """
        if self._is_running:
            logger.warning("Cannot enqueue while queue is running")
            return

        self._queue.clear()

        for i, file_path in enumerate(workflow_files):
            metadata = metadata_list[i] if metadata_list and i < len(metadata_list) else {}
            item = WorkflowQueueItem(file_path=file_path, metadata=metadata)
            self._queue.append(item)

        self._current_index = 0
        logger.info(f"Enqueued {len(self._queue)} workflows")

    def start(self) -> bool:
        """
        Start executing the queue.

        Returns:
            True if started successfully, False if already running or queue empty
        """
        if self._is_running:
            logger.warning("Queue is already running")
            return False

        if not self._queue:
            logger.warning("Queue is empty")
            return False

        self._is_running = True
        self._cancel_requested = False

        # Start execution in background thread
        self._execution_thread = threading.Thread(
            target=self._execute_queue,
            name="WorkflowQueueExecution",
            daemon=True
        )
        self._execution_thread.start()

        return True

    def cancel(self) -> None:
        """Request cancellation of the queue execution."""
        if not self._is_running:
            return

        logger.info("Queue cancellation requested")
        self._cancel_requested = True

        # Also stop the currently running workflow
        try:
            self._workflow_controller.stop_workflow()
        except Exception as e:
            logger.warning(f"Error stopping current workflow: {e}")

    @property
    def is_running(self) -> bool:
        """Check if queue is currently executing."""
        return self._is_running

    @property
    def queue_length(self) -> int:
        """Get total number of workflows in queue."""
        return len(self._queue)

    @property
    def current_index(self) -> int:
        """Get index of currently executing workflow (0-based)."""
        return self._current_index

    def _execute_queue(self) -> None:
        """Main queue execution loop (runs in background thread)."""
        try:
            # Emit queue started on main thread
            self.queue_started.emit()

            # Notify status indicator that workflow(s) are starting
            if self._status_indicator_service:
                # Call directly - the status indicator service handles this gracefully
                try:
                    self._status_indicator_service.on_workflow_started()
                except Exception as e:
                    logger.warning(f"Could not notify status indicator of workflow start: {e}")

            total = len(self._queue)

            for i, item in enumerate(self._queue):
                if self._cancel_requested:
                    logger.info("Queue execution cancelled")
                    self.queue_cancelled.emit()
                    break

                self._current_index = i
                item.started = True

                # Emit progress
                self.progress_updated.emit(
                    i + 1, total, f"Starting workflow {i + 1}/{total}..."
                )
                self.workflow_started.emit(i, total, str(item.file_path))

                # Call start callback for Sample View integration
                if self._on_workflow_start_callback:
                    try:
                        self._on_workflow_start_callback(item.file_path, item.metadata)
                    except Exception as e:
                        logger.warning(f"Workflow start callback error: {e}")

                # Execute the workflow
                success, error = self._execute_single_workflow(item)

                if not success:
                    item.error = error
                    logger.error(f"Workflow {i + 1}/{total} failed: {error}")
                    self.error_occurred.emit(f"Workflow {item.file_path.name}: {error}")
                    # Continue with next workflow instead of aborting entire queue
                    continue

                item.completed = True
                self.workflow_completed.emit(i, total, str(item.file_path))

                # Brief delay between workflows
                if i < total - 1 and not self._cancel_requested:
                    time.sleep(self.MIN_INTER_WORKFLOW_DELAY)

            # Queue finished
            if not self._cancel_requested:
                self.queue_completed.emit()
                logger.info(f"Queue execution completed: {total} workflows")

        except Exception as e:
            logger.exception("Error during queue execution")
            self.error_occurred.emit(f"Queue execution error: {e}")

        finally:
            self._is_running = False

            # Notify status indicator that workflow(s) are done
            if self._status_indicator_service:
                try:
                    self._status_indicator_service.on_workflow_stopped()
                except Exception as e:
                    logger.warning(f"Could not notify status indicator of workflow stop: {e}")

    def _execute_single_workflow(self, item: WorkflowQueueItem) -> tuple:
        """
        Execute a single workflow and wait for completion.

        Args:
            item: Workflow queue item

        Returns:
            Tuple of (success: bool, error_message: Optional[str])
        """
        file_path = item.file_path

        # Load the workflow
        success, msg = self._workflow_controller.load_workflow(str(file_path))
        if not success:
            return (False, f"Load failed: {msg}")

        # Start the workflow
        success, msg = self._workflow_controller.start_workflow()
        if not success:
            return (False, f"Start failed: {msg}")

        logger.info(f"Started workflow: {file_path.name}")

        # Wait for workflow completion by polling system state
        success, error = self._wait_for_completion(item)

        if success:
            # Notify workflow controller that workflow completed
            # This records timing data and clears tile position
            self._workflow_controller.on_workflow_completed()

        return (success, error)

    def _wait_for_completion(self, item: WorkflowQueueItem) -> tuple:
        """
        Wait for workflow to complete by polling system state.

        Args:
            item: Current workflow queue item

        Returns:
            Tuple of (success: bool, error_message: Optional[str])
        """
        start_time = time.time()
        logger.info(f"Waiting for workflow completion: {item.file_path.name}")

        # Initial delay to let workflow start
        logger.debug("Initial 2s delay for workflow startup...")
        time.sleep(2.0)

        while True:
            # Check for cancellation
            if self._cancel_requested:
                return (False, "Cancelled")

            # Check timeout
            elapsed = time.time() - start_time
            if elapsed > self.MAX_WORKFLOW_TIMEOUT:
                logger.error(f"Workflow timeout after {elapsed:.1f}s")
                return (False, f"Timeout after {elapsed:.1f}s")

            # Poll system state
            if self._is_system_idle():
                logger.info(f"Workflow completed after {elapsed:.1f}s")
                return (True, None)

            # Update progress
            self.progress_updated.emit(
                self._current_index + 1,
                len(self._queue),
                f"Workflow running... ({elapsed:.0f}s)"
            )

            time.sleep(self.STATE_POLL_INTERVAL)

    def _is_system_idle(self) -> bool:
        """
        Check if system is idle (workflow complete).

        Uses SYSTEM_STATE_GET command to query microscope state.

        Returns:
            True if system is idle, False otherwise
        """
        if not self._connection_service:
            # Fall back to workflow controller's executing flag
            is_idle = not self._workflow_controller.is_executing
            logger.debug(f"System idle check (fallback): is_executing={self._workflow_controller.is_executing}, is_idle={is_idle}")
            return is_idle

        try:
            # Query system state via connection service
            # The system returns STATE_VALUE_IDLE (0) when ready
            from ..core.command_codes import SystemCommands

            response = self._connection_service.query_system_state()
            if response is None:
                logger.warning("System state query returned None - assuming busy")
                return False

            # Check if state is IDLE (0)
            state = response.get('state', -1)
            is_idle = state == 0  # SystemCommands.STATE_VALUE_IDLE
            logger.debug(f"System idle check: state={state}, is_idle={is_idle}")
            return is_idle

        except Exception as e:
            logger.warning(f"Error checking system state: {e}")
            # Fall back to workflow controller
            is_idle = not self._workflow_controller.is_executing
            logger.debug(f"System idle check (exception fallback): is_idle={is_idle}")
            return is_idle
