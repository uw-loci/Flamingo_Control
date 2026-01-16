# ============================================================================
# src/py2flamingo/services/workflow_queue_service.py
"""
Workflow Queue Service for sequential workflow execution.

This service manages a queue of workflow files and executes them sequentially,
waiting for each workflow to complete before starting the next. This is critical
for tile collection where multiple Z-stack workflows must run one after another.

Workflow completion is detected by:
1. Listening for CAMERA_STACK_COMPLETE (0x3011) callback from server
2. Fallback: Polling system state (SYSTEM_STATE_GET) if no callback received
3. Optional timeout based on estimated workflow duration

Progress tracking via:
- UI_SET_GAUGE_VALUE (0x9004) callbacks for image acquisition progress
- UI_IMAGES_SAVED_TO_STORAGE (0x9008) for disk write progress

Architecture:
    WorkflowQueueService
        └── Accepts list of workflow files
        └── Executes sequentially via WorkflowController
        └── Listens for STACK_COMPLETE callback for completion
        └── Emits signals for progress updates
        └── Handles cancellation gracefully

TODO: Use progress callbacks to update Sample View with stage position during
      workflow execution. The progress callbacks contain position data that
      could be used to show where the microscope is currently acquiring.
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


# Command codes for workflow progress (from command_codes.py)
SYSTEM_STATE_IDLE = 0xa002     # 40962 - System became idle (PRIMARY completion signal)
CAMERA_STACK_COMPLETE = 0x3011  # 12305 - Stack acquisition complete (backup)
UI_SET_GAUGE_VALUE = 0x9004    # 36868 - Progress bar update
UI_IMAGES_SAVED = 0x9008       # 36872 - Images written to storage


@dataclass
class WorkflowQueueItem:
    """Item in the workflow queue."""
    file_path: Path
    metadata: Dict[str, Any] = field(default_factory=dict)  # Position info, etc.
    started: bool = False
    completed: bool = False
    error: Optional[str] = None
    images_acquired: int = 0
    images_expected: int = 0


class WorkflowQueueService(QObject):
    """
    Service for sequential workflow execution.

    Manages a queue of workflow files and executes them one at a time,
    waiting for each to complete before starting the next.

    Signals:
        queue_started: Emitted when queue execution begins
        workflow_started: Emitted when individual workflow starts (index, total, path)
        workflow_completed: Emitted when individual workflow completes (index, total, path)
        workflow_progress: Emitted with image progress (acquired, expected)
        queue_completed: Emitted when all workflows complete
        queue_cancelled: Emitted if queue is cancelled
        progress_updated: Emitted with progress info (current, total, message)
        error_occurred: Emitted on error (message)
    """

    queue_started = pyqtSignal()
    workflow_started = pyqtSignal(int, int, str)  # index, total, path
    workflow_completed = pyqtSignal(int, int, str)  # index, total, path
    workflow_progress = pyqtSignal(int, int)  # images_acquired, images_expected
    queue_completed = pyqtSignal()
    queue_cancelled = pyqtSignal()
    progress_updated = pyqtSignal(int, int, str)  # current, total, message
    error_occurred = pyqtSignal(str)

    # Fallback poll interval if callbacks not received (seconds)
    STATE_POLL_INTERVAL = 10.0

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
            connection_service: Service for querying system state and callback registration
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

        # Completion detection
        self._completion_event = threading.Event()
        self._completion_data: Optional[Dict] = None

        # Execution thread
        self._execution_thread: Optional[threading.Thread] = None

        # Callbacks for tile position (for Sample View integration)
        self._on_workflow_start_callback: Optional[Callable[[Path, Dict], None]] = None

        logger.info("WorkflowQueueService initialized (callback-based completion detection)")

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

        # Signal completion event to unblock waiting
        self._completion_event.set()

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

    # =========================================================================
    # Callback Handlers
    # =========================================================================

    def _on_system_idle(self, message) -> None:
        """
        Handle SYSTEM_STATE_IDLE callback from server.

        This is the PRIMARY signal that a workflow has completed.
        The server sends this when it transitions back to idle state.

        Args:
            message: ParsedMessage (may not contain useful data)
        """
        logger.info(f"[QUEUE] Received SYSTEM_STATE_IDLE callback - workflow complete!")

        # Signal completion - this is the definitive completion signal
        self._completion_event.set()

    def _on_stack_complete(self, message) -> None:
        """
        Handle CAMERA_STACK_COMPLETE callback from server.

        This contains acquisition statistics but is NOT the primary completion signal.
        We store the data for reporting but wait for SYSTEM_STATE_IDLE for completion.

        Args:
            message: ParsedMessage containing completion data
        """
        logger.info(f"[QUEUE] Received STACK_COMPLETE callback: "
                   f"acquired={message.int32_data0}, "
                   f"expected={message.int32_data1}, "
                   f"errors={message.int32_data2}, "
                   f"time={message.double_data:.1f}us")

        # Store completion data for reporting (but don't signal completion yet)
        self._completion_data = {
            'images_acquired': message.int32_data0,
            'images_expected': message.int32_data1,
            'error_count': message.int32_data2,
            'acquisition_time_us': message.double_data
        }

        # Note: Do NOT set _completion_event here - wait for SYSTEM_STATE_IDLE

    def _on_progress_update(self, message) -> None:
        """
        Handle UI_SET_GAUGE_VALUE callback for progress tracking.

        Args:
            message: ParsedMessage containing progress data

        TODO: Extract stage position from progress callbacks and update
              Sample View to show current acquisition location.
        """
        acquired = message.int32_data0
        expected = message.int32_data1

        logger.debug(f"Progress update: {acquired}/{expected} images")

        # Update current queue item
        if 0 <= self._current_index < len(self._queue):
            item = self._queue[self._current_index]
            item.images_acquired = acquired
            item.images_expected = expected

        # Emit progress signal
        self.workflow_progress.emit(acquired, expected)

        # Update progress message
        self.progress_updated.emit(
            self._current_index + 1,
            len(self._queue),
            f"Acquiring... {acquired}/{expected} images"
        )

    def _register_callbacks(self) -> None:
        """Register callback handlers with connection service."""
        if not self._connection_service:
            logger.warning("No connection service - using fallback polling")
            return

        try:
            # Primary completion signal - system returning to idle
            self._connection_service.register_callback(
                SYSTEM_STATE_IDLE, self._on_system_idle
            )
            # Acquisition stats (not completion trigger)
            self._connection_service.register_callback(
                CAMERA_STACK_COMPLETE, self._on_stack_complete
            )
            # Progress tracking
            self._connection_service.register_callback(
                UI_SET_GAUGE_VALUE, self._on_progress_update
            )
            logger.info("Registered callbacks for SYSTEM_STATE_IDLE, STACK_COMPLETE, and progress updates")
        except Exception as e:
            logger.warning(f"Failed to register callbacks: {e}")

    def _unregister_callbacks(self) -> None:
        """Unregister callback handlers."""
        if not self._connection_service:
            return

        try:
            self._connection_service.unregister_callback(
                SYSTEM_STATE_IDLE, self._on_system_idle
            )
            self._connection_service.unregister_callback(
                CAMERA_STACK_COMPLETE, self._on_stack_complete
            )
            self._connection_service.unregister_callback(
                UI_SET_GAUGE_VALUE, self._on_progress_update
            )
            logger.info("Unregistered workflow callbacks")
        except Exception as e:
            logger.warning(f"Failed to unregister callbacks: {e}")

    # =========================================================================
    # Queue Execution
    # =========================================================================

    def _execute_queue(self) -> None:
        """Main queue execution loop (runs in background thread)."""
        try:
            # Emit queue started on main thread
            self.queue_started.emit()
            logger.info("=== Queue execution started ===")

            # Register callbacks for completion detection
            self._register_callbacks()

            total = len(self._queue)
            logger.info(f"Queue contains {total} workflows to execute")

            for i, item in enumerate(self._queue):
                logger.info(f"--- Starting workflow {i + 1}/{total}: {item.file_path.name} ---")

                if self._cancel_requested:
                    logger.info("Queue execution cancelled by user")
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
                logger.info(f"Executing workflow {i + 1}/{total}...")
                success, error = self._execute_single_workflow(item)
                logger.info(f"Workflow {i + 1}/{total} execution returned: success={success}, error={error}")

                if not success:
                    item.error = error
                    logger.error(f"Workflow {i + 1}/{total} FAILED: {error}")
                    self.error_occurred.emit(f"Workflow {item.file_path.name}: {error}")
                    # Continue with next workflow instead of aborting entire queue
                    logger.info(f"Continuing to next workflow after failure...")
                    continue

                item.completed = True
                logger.info(f"Workflow {i + 1}/{total} completed successfully, emitting signal")
                self.workflow_completed.emit(i, total, str(item.file_path))

                # Brief delay between workflows
                if i < total - 1 and not self._cancel_requested:
                    logger.info(f"Waiting {self.MIN_INTER_WORKFLOW_DELAY}s before next workflow...")
                    time.sleep(self.MIN_INTER_WORKFLOW_DELAY)

            # Queue finished
            logger.info(f"=== Queue loop finished, cancel_requested={self._cancel_requested} ===")
            if not self._cancel_requested:
                self.queue_completed.emit()
                logger.info(f"Queue execution completed: {total} workflows")

        except Exception as e:
            logger.exception("Error during queue execution")
            self.error_occurred.emit(f"Queue execution error: {e}")

        finally:
            self._is_running = False
            self._unregister_callbacks()

    def _execute_single_workflow(self, item: WorkflowQueueItem) -> tuple:
        """
        Execute a single workflow and wait for completion.

        Args:
            item: Workflow queue item

        Returns:
            Tuple of (success: bool, error_message: Optional[str])
        """
        file_path = item.file_path
        logger.info(f"[QUEUE] Loading workflow: {file_path.name}")

        # Load the workflow
        success, msg = self._workflow_controller.load_workflow(str(file_path))
        if not success:
            logger.error(f"[QUEUE] Load failed: {msg}")
            return (False, f"Load failed: {msg}")
        logger.info(f"[QUEUE] Workflow loaded successfully")

        # Clear completion state BEFORE starting
        logger.info(f"[QUEUE] Clearing completion state...")
        self._completion_event.clear()
        self._completion_data = None

        # Start the workflow
        logger.info(f"[QUEUE] Starting workflow execution...")
        success, msg = self._workflow_controller.start_workflow()
        if not success:
            logger.error(f"[QUEUE] Start failed: {msg}")
            return (False, f"Start failed: {msg}")

        logger.info(f"[QUEUE] Started workflow: {file_path.name}, waiting for completion...")

        # Wait for workflow completion
        success, error = self._wait_for_completion(item)
        logger.info(f"[QUEUE] Wait completed: success={success}, error={error}")

        if success:
            # Notify workflow controller that workflow completed
            # This records timing data and clears tile position
            logger.info(f"[QUEUE] Notifying controller of completion...")
            self._workflow_controller.on_workflow_completed()

        return (success, error)

    def _wait_for_completion(self, item: WorkflowQueueItem) -> tuple:
        """
        Wait for workflow to complete via SYSTEM_STATE_IDLE callback.

        The primary completion signal is receiving SYSTEM_STATE_IDLE (0xa002)
        on the same message stream as progress updates. This indicates the
        server has finished all acquisition and processing.

        Falls back to polling if callback not received within timeout.

        Args:
            item: Current workflow queue item

        Returns:
            Tuple of (success: bool, error_message: Optional[str])
        """
        start_time = time.time()
        logger.info(f"[QUEUE] Waiting for SYSTEM_STATE_IDLE callback: {item.file_path.name}")

        # Wait for completion callback with timeout
        while True:
            # Check for cancellation
            if self._cancel_requested:
                return (False, "Cancelled")

            # Check timeout
            elapsed = time.time() - start_time
            if elapsed > self.MAX_WORKFLOW_TIMEOUT:
                logger.error(f"[QUEUE] Workflow timeout after {elapsed:.1f}s")
                return (False, f"Timeout after {elapsed:.1f}s")

            # Wait for SYSTEM_STATE_IDLE callback (primary completion signal)
            if self._completion_event.wait(timeout=self.STATE_POLL_INTERVAL):
                # SYSTEM_STATE_IDLE callback received
                if self._cancel_requested:
                    return (False, "Cancelled")

                # Log completion stats if we received STACK_COMPLETE data
                if self._completion_data:
                    acquired = self._completion_data.get('images_acquired', 0)
                    expected = self._completion_data.get('images_expected', 0)
                    errors = self._completion_data.get('error_count', 0)
                    acq_time = self._completion_data.get('acquisition_time_us', 0)

                    logger.info(f"[QUEUE] Workflow completed via SYSTEM_STATE_IDLE: "
                               f"{acquired}/{expected} images, "
                               f"{errors} errors, {acq_time/1e6:.1f}s")

                    if errors > 0:
                        logger.warning(f"[QUEUE] Workflow had {errors} acquisition errors")
                else:
                    logger.info(f"[QUEUE] Workflow completed via SYSTEM_STATE_IDLE after {elapsed:.1f}s")

                return (True, None)

            # Fallback: Poll system state if no callback received
            logger.debug(f"[QUEUE] No SYSTEM_STATE_IDLE callback after {self.STATE_POLL_INTERVAL}s, "
                        f"polling system state...")

            if self._is_system_idle():
                logger.info(f"[QUEUE] Workflow completed (via polling fallback) after {elapsed:.1f}s")
                return (True, None)

            # Update progress
            self.progress_updated.emit(
                self._current_index + 1,
                len(self._queue),
                f"Workflow running... ({elapsed:.0f}s)"
            )

    def _is_system_idle(self) -> bool:
        """
        Check if system is idle (workflow complete) - fallback method.

        Uses SYSTEM_STATE_GET command to query microscope state.

        Returns:
            True if system is idle, False otherwise
        """
        if not self._connection_service:
            # Fall back to workflow controller's executing flag
            is_idle = not self._workflow_controller.is_executing
            logger.debug(f"System idle check (controller flag): is_idle={is_idle}")
            return is_idle

        try:
            response = self._connection_service.query_system_state()
            if response is None:
                logger.warning("System state query returned None - assuming busy")
                return False

            # Check if state is IDLE (0)
            state = response.get('state', -1)
            is_idle = state == 0
            logger.debug(f"System idle check: state={state}, is_idle={is_idle}")
            return is_idle

        except Exception as e:
            logger.warning(f"Error checking system state: {e}")
            # Fall back to workflow controller
            is_idle = not self._workflow_controller.is_executing
            logger.debug(f"System idle check (fallback): is_idle={is_idle}")
            return is_idle
