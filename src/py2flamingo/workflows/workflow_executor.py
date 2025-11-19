"""Workflow Executor - Execution engine for running workflows on hardware.

This module handles the actual execution of workflows, interfacing with
the microscope hardware through the MicroscopeCommandService.
"""

import logging
import threading
import time
from typing import Optional, Dict, Any, Callable, List
from enum import Enum
from dataclasses import dataclass
from datetime import datetime
import queue

from ..models.data.workflow import (
    Workflow, WorkflowState, WorkflowStep, WorkflowType
)
from ..models.hardware.stage import Position
from ..services.microscope_command_service import MicroscopeCommandService
from ..services.connection_manager import ConnectionManager
from ..core.errors import FlamingoError
from ..core.command_codes import CommandCode


logger = logging.getLogger(__name__)


class ExecutionState(Enum):
    """Internal execution states."""
    IDLE = "idle"
    PREPARING = "preparing"
    EXECUTING = "executing"
    PAUSED = "paused"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass
class ExecutionContext:
    """Context for workflow execution."""
    workflow: Workflow
    dry_run: bool = False
    skip_hardware_init: bool = False
    image_callback: Optional[Callable] = None
    progress_callback: Optional[Callable] = None
    error_callback: Optional[Callable] = None
    pause_between_steps: float = 0.0
    timeout_per_step: float = 60.0
    retry_on_error: bool = False
    max_retries: int = 3


class WorkflowExecutionError(FlamingoError):
    """Raised when workflow execution fails."""
    pass


class WorkflowExecutor:
    """Executes workflows on microscope hardware.

    This is the only component that directly interfaces with hardware,
    consolidating all execution logic that was previously scattered
    across multiple services.
    """

    def __init__(self,
                command_service: Optional[MicroscopeCommandService] = None,
                connection_manager: Optional[ConnectionManager] = None):
        """Initialize executor.

        Args:
            command_service: Service for sending microscope commands
            connection_manager: Manager for microscope connections
        """
        self.command_service = command_service
        self.connection_manager = connection_manager

        # Execution state
        self._state = ExecutionState.IDLE
        self._current_context: Optional[ExecutionContext] = None
        self._execution_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()  # Not paused initially

        # Command queue for thread-safe operations
        self._command_queue = queue.Queue()

        # Statistics
        self._statistics = {
            'workflows_executed': 0,
            'workflows_completed': 0,
            'workflows_failed': 0,
            'total_steps_executed': 0,
            'total_execution_time': 0.0,
            'last_execution_time': None
        }

        # Ensure services are initialized
        self._initialize_services()

    def _initialize_services(self):
        """Initialize required services if not provided."""
        if not self.command_service:
            from ..services.microscope_command_service import MicroscopeCommandService
            self.command_service = MicroscopeCommandService.get_instance()
            logger.info("Initialized MicroscopeCommandService")

        if not self.connection_manager:
            from ..services.connection_manager import ConnectionManager
            self.connection_manager = ConnectionManager.get_instance()
            logger.info("Initialized ConnectionManager")

    # ==================== Execution Control ====================

    def start(self, workflow: Workflow, dry_run: bool = False, **kwargs) -> bool:
        """Start executing a workflow.

        Args:
            workflow: Workflow to execute
            dry_run: If True, simulate without hardware control
            **kwargs: Additional execution options

        Returns:
            True if execution started successfully

        Raises:
            WorkflowExecutionError: If execution cannot start
        """
        # Check if already executing
        if self._state in [ExecutionState.EXECUTING, ExecutionState.PREPARING]:
            raise WorkflowExecutionError("Another workflow is already executing")

        # Check connection
        if not dry_run and not self._check_connection():
            raise WorkflowExecutionError("No microscope connection")

        # Create execution context
        context = ExecutionContext(
            workflow=workflow,
            dry_run=dry_run,
            **kwargs
        )

        # Start execution thread
        self._current_context = context
        self._state = ExecutionState.PREPARING
        self._stop_event.clear()

        self._execution_thread = threading.Thread(
            target=self._execution_loop,
            args=(context,),
            daemon=True
        )
        self._execution_thread.start()

        logger.info(f"Started workflow execution: {workflow.name} (dry_run={dry_run})")
        self._statistics['workflows_executed'] += 1

        return True

    def stop(self) -> bool:
        """Stop the currently executing workflow.

        Returns:
            True if stop was initiated
        """
        if self._state not in [ExecutionState.EXECUTING, ExecutionState.PAUSED, ExecutionState.PREPARING]:
            logger.warning("No workflow to stop")
            return False

        logger.info("Stopping workflow execution")
        self._state = ExecutionState.STOPPING
        self._stop_event.set()
        self._pause_event.set()  # Unpause if paused

        # Send stop command to hardware if connected
        if not self._current_context.dry_run:
            try:
                self.command_service.send_command(
                    CommandCode.CMD_WORKFLOW_STOP
                )
            except Exception as e:
                logger.error(f"Failed to send stop command: {e}")

        # Wait for thread to finish (with timeout)
        if self._execution_thread and self._execution_thread.is_alive():
            self._execution_thread.join(timeout=5.0)

        self._state = ExecutionState.IDLE
        return True

    def pause(self) -> bool:
        """Pause the currently executing workflow.

        Returns:
            True if pause was initiated
        """
        if self._state != ExecutionState.EXECUTING:
            return False

        logger.info("Pausing workflow execution")
        self._state = ExecutionState.PAUSED
        self._pause_event.clear()

        return True

    def resume(self) -> bool:
        """Resume a paused workflow.

        Returns:
            True if resume was initiated
        """
        if self._state != ExecutionState.PAUSED:
            return False

        logger.info("Resuming workflow execution")
        self._state = ExecutionState.EXECUTING
        self._pause_event.set()

        return True

    # ==================== Execution Loop ====================

    def _execution_loop(self, context: ExecutionContext):
        """Main execution loop running in separate thread.

        Args:
            context: Execution context
        """
        start_time = time.time()

        try:
            # Prepare workflow
            self._prepare_workflow(context)

            # Execute workflow
            self._state = ExecutionState.EXECUTING
            self._execute_workflow(context)

            # Mark completion
            if not self._stop_event.is_set():
                context.workflow.mark_completed()
                self._statistics['workflows_completed'] += 1
                logger.info(f"Workflow completed: {context.workflow.name}")

        except Exception as e:
            # Handle execution error
            error_msg = f"Workflow execution failed: {e}"
            logger.error(error_msg)
            context.workflow.mark_error(str(e))
            self._statistics['workflows_failed'] += 1

            if context.error_callback:
                context.error_callback(context.workflow, e)

        finally:
            # Cleanup
            execution_time = time.time() - start_time
            self._statistics['total_execution_time'] += execution_time
            self._statistics['last_execution_time'] = execution_time

            self._state = ExecutionState.IDLE
            self._current_context = None
            logger.info(f"Workflow execution finished in {execution_time:.1f}s")

    def _prepare_workflow(self, context: ExecutionContext):
        """Prepare workflow for execution.

        Args:
            context: Execution context
        """
        workflow = context.workflow

        # Mark workflow as started
        workflow.start_execution()

        # Send workflow to microscope if not dry run
        if not context.dry_run:
            # Convert to legacy format for compatibility
            workflow_dict = workflow.to_workflow_dict()

            # Send workflow start command
            success = self.command_service.send_workflow(workflow_dict)
            if not success:
                raise WorkflowExecutionError("Failed to send workflow to microscope")

            # Send start command
            self.command_service.send_command(CommandCode.CMD_WORKFLOW_START)

        logger.info(f"Prepared workflow with {len(workflow.steps)} steps")

    def _execute_workflow(self, context: ExecutionContext):
        """Execute workflow steps.

        Args:
            context: Execution context
        """
        workflow = context.workflow

        for step_index, step in enumerate(workflow.steps):
            # Check for stop signal
            if self._stop_event.is_set():
                logger.info("Workflow stopped by user")
                break

            # Check for pause
            self._pause_event.wait()

            # Execute step
            self._execute_step(context, step, step_index)

            # Update progress
            workflow.current_step_index = step_index + 1
            workflow.images_acquired += 1

            if context.progress_callback:
                context.progress_callback(workflow, step)

            # Pause between steps if requested
            if context.pause_between_steps > 0:
                time.sleep(context.pause_between_steps)

        # Send workflow stop command if not dry run
        if not context.dry_run and not self._stop_event.is_set():
            self.command_service.send_command(CommandCode.CMD_WORKFLOW_STOP)

    def _execute_step(self, context: ExecutionContext,
                     step: WorkflowStep, step_index: int):
        """Execute a single workflow step.

        Args:
            context: Execution context
            step: Step to execute
            step_index: Index of the step
        """
        step.mark_started()
        retry_count = 0

        while retry_count <= context.max_retries:
            try:
                if context.dry_run:
                    # Simulate execution
                    self._simulate_step(step)
                else:
                    # Execute on hardware
                    self._execute_hardware_step(context, step)

                # Mark successful completion
                step.mark_completed()
                self._statistics['total_steps_executed'] += 1
                break

            except Exception as e:
                retry_count += 1

                if retry_count > context.max_retries or not context.retry_on_error:
                    error_msg = f"Step {step_index} failed: {e}"
                    step.mark_error(error_msg)
                    raise WorkflowExecutionError(error_msg)
                else:
                    logger.warning(f"Step {step_index} failed, retrying ({retry_count}/{context.max_retries})")
                    time.sleep(1.0)  # Brief delay before retry

    def _execute_hardware_step(self, context: ExecutionContext,
                              step: WorkflowStep):
        """Execute step on actual hardware.

        Args:
            context: Execution context
            step: Step to execute
        """
        # Move to position if needed
        if step.position:
            self._move_to_position(step.position)

        # Set Z position for z-stack
        if step.z_position is not None:
            self._move_to_z(step.z_position)

        # Configure channel if specified
        if step.channel:
            self._configure_channel(step.channel)

        # Trigger acquisition
        self._trigger_acquisition(context, step)

    def _move_to_position(self, position: Position):
        """Move stage to position.

        Args:
            position: Target position
        """
        # Send position command
        self.command_service.send_position(position)

        # Wait for movement to complete
        # This would ideally check stage status
        time.sleep(0.5)

    def _move_to_z(self, z_position: float):
        """Move to Z position.

        Args:
            z_position: Z position in mm
        """
        # Send Z movement command
        self.command_service.send_command(
            CommandCode.CMD_Z_STAGE_MOVE_TO,
            data={'z_mm': z_position}
        )

        # Wait for movement
        time.sleep(0.2)

    def _configure_channel(self, channel: str):
        """Configure acquisition channel.

        Args:
            channel: Channel name
        """
        # This would configure the appropriate laser/filter
        pass

    def _trigger_acquisition(self, context: ExecutionContext,
                           step: WorkflowStep):
        """Trigger image acquisition.

        Args:
            context: Execution context
            step: Current step
        """
        # Send acquisition command
        self.command_service.send_command(CommandCode.CMD_TAKE_IMAGE)

        # Wait for acquisition
        time.sleep(0.1)  # Would ideally wait for acquisition complete signal

        # Handle acquired image if callback provided
        if context.image_callback:
            # This would retrieve the actual image data
            image_data = None  # Placeholder
            context.image_callback(step, image_data)

    def _simulate_step(self, step: WorkflowStep):
        """Simulate step execution for dry run.

        Args:
            step: Step to simulate
        """
        # Simulate execution time
        time.sleep(0.01)

        logger.debug(f"Simulated step: {step.name}")

    # ==================== Connection Management ====================

    def _check_connection(self) -> bool:
        """Check if connected to microscope.

        Returns:
            True if connected
        """
        if not self.connection_manager:
            return False

        return self.connection_manager.is_connected()

    # ==================== State and Statistics ====================

    def get_state(self) -> ExecutionState:
        """Get current execution state.

        Returns:
            Current execution state
        """
        return self._state

    def is_executing(self) -> bool:
        """Check if currently executing.

        Returns:
            True if executing or preparing
        """
        return self._state in [ExecutionState.EXECUTING, ExecutionState.PREPARING]

    def get_current_workflow(self) -> Optional[Workflow]:
        """Get currently executing workflow.

        Returns:
            Current workflow or None
        """
        if self._current_context:
            return self._current_context.workflow
        return None

    def get_statistics(self) -> Dict[str, Any]:
        """Get execution statistics.

        Returns:
            Statistics dictionary
        """
        return self._statistics.copy()

    def reset_statistics(self):
        """Reset execution statistics."""
        self._statistics = {
            'workflows_executed': 0,
            'workflows_completed': 0,
            'workflows_failed': 0,
            'total_steps_executed': 0,
            'total_execution_time': 0.0,
            'last_execution_time': None
        }
        logger.info("Reset execution statistics")

    def reset(self):
        """Reset executor to initial state."""
        # Stop any running workflow
        if self.is_executing():
            self.stop()

        # Clear state
        self._state = ExecutionState.IDLE
        self._current_context = None
        self._stop_event.clear()
        self._pause_event.set()

        # Clear queue
        while not self._command_queue.empty():
            try:
                self._command_queue.get_nowait()
            except queue.Empty:
                break

        logger.info("Executor reset to initial state")

    # ==================== Advanced Features ====================

    def execute_single_step(self, step: WorkflowStep,
                           dry_run: bool = False) -> bool:
        """Execute a single workflow step.

        Args:
            step: Step to execute
            dry_run: If True, simulate execution

        Returns:
            True if step executed successfully
        """
        try:
            context = ExecutionContext(
                workflow=None,  # No parent workflow
                dry_run=dry_run
            )

            self._execute_step(context, step, 0)
            return True

        except Exception as e:
            logger.error(f"Failed to execute single step: {e}")
            return False

    def estimate_remaining_time(self) -> Optional[float]:
        """Estimate remaining execution time.

        Returns:
            Estimated seconds remaining or None
        """
        if not self._current_context:
            return None

        workflow = self._current_context.workflow

        if workflow.images_expected > 0 and workflow.images_acquired > 0:
            # Estimate based on current progress
            elapsed = time.time() - workflow.start_time.timestamp()
            rate = workflow.images_acquired / elapsed
            remaining_images = workflow.images_expected - workflow.images_acquired
            return remaining_images / rate if rate > 0 else None

        return None

    # ==================== Singleton Support ====================

    _instance = None

    @classmethod
    def get_instance(cls) -> 'WorkflowExecutor':
        """Get singleton instance.

        Returns:
            Singleton executor instance
        """
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance