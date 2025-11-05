# ============================================================================
# src/py2flamingo/services/workflow_execution_service.py
"""
Service for executing workflows on the microscope.

This service handles workflow validation, execution, and result retrieval,
coordinating between the connection service, queue manager, and event manager.
"""

import logging
import time
from typing import Optional, Any, Dict
from queue import Empty

from py2flamingo.core.events import EventManager
from py2flamingo.core.queue_manager import QueueManager


class WorkflowExecutionService:
    """
    Service for executing microscope workflows.

    This service coordinates workflow execution by:
    - Validating workflows before sending
    - Sending workflows to the microscope
    - Waiting for system idle state
    - Retrieving image results

    Attributes:
        connection_service: Service for microscope communication
        queue_manager: Manager for data queues
        event_manager: Manager for synchronization events
        workflow_service: Service for workflow operations
        logger: Logger instance
    """

    # Command codes from CommandCodes.h (verified 2025-11-05)
    COMMAND_CODES_CAMERA_CHECK_STACK = 12331  # Fixed: was 12335
    COMMAND_CODES_CAMERA_WORK_FLOW_START = 12292
    COMMAND_CODES_SYSTEM_STATE_GET = 40967
    COMMAND_CODES_SYSTEM_STATE_IDLE = 40962

    def __init__(self,
                 connection_service: 'ConnectionService',
                 queue_manager: QueueManager,
                 event_manager: EventManager,
                 workflow_service: 'WorkflowService'):
        """
        Initialize workflow execution service with dependency injection.

        Args:
            connection_service: ConnectionService instance for sending commands
            queue_manager: QueueManager instance for data flow
            event_manager: EventManager instance for synchronization
            workflow_service: WorkflowService instance for validation
        """
        self.connection_service = connection_service
        self.queue_manager = queue_manager
        self.event_manager = event_manager
        self.workflow_service = workflow_service
        self.logger = logging.getLogger(__name__)

    def check_workflow(self, workflow_dict: Dict[str, Any]) -> bool:
        """
        Validate workflow before sending to microscope.

        This method checks if the workflow will hit any hard limits on the
        microscope and logs any warnings. Based on lines 80-92 of old code.

        Args:
            workflow_dict: Workflow configuration dictionary

        Returns:
            True if workflow is valid and safe to execute

        Raises:
            RuntimeError: If not connected to microscope
        """
        if not self.connection_service.is_connected():
            raise RuntimeError("Not connected to microscope")

        try:
            # First validate the workflow structure
            self.workflow_service.validate_workflow(workflow_dict)

            # Clear any previous data in the queue
            self.queue_manager.clear_queue('other_data')

            # Send check stack command to microscope
            self.queue_manager.put_nowait('command', self.COMMAND_CODES_CAMERA_CHECK_STACK)
            self.event_manager.set_event('send')

            # Wait for send event to clear (command sent)
            timeout = 5.0  # 5 second timeout
            start_time = time.time()
            while self.event_manager.is_set('send'):
                time.sleep(0.05)
                if time.time() - start_time > timeout:
                    self.logger.warning("Timeout waiting for check command to send")
                    break

            # Get response from microscope
            time.sleep(0.1)  # Brief delay for response
            try:
                text_bytes = self.queue_manager.get_nowait('other_data')

                if text_bytes and "hard limit" in str(text_bytes):
                    text_data = text_bytes.decode('utf-8')
                    self.logger.warning(f"Workflow validation warning: {text_data}")
                    print(text_data)
                    return False

            except Exception as e:
                self.logger.debug(f"No validation warnings received: {e}")

            self.logger.info("Workflow validation passed")
            return True

        except Exception as e:
            self.logger.error(f"Workflow validation failed: {e}")
            raise

    def send_workflow(self, workflow_dict: Dict[str, Any]) -> None:
        """
        Send workflow to microscope and wait for system to become idle.

        This method sends the workflow start command and monitors the system
        idle event to ensure the workflow begins execution. Based on lines
        94-112 of old code.

        Args:
            workflow_dict: Workflow configuration dictionary

        Raises:
            RuntimeError: If not connected to microscope
            ValueError: If workflow validation fails
        """
        if not self.connection_service.is_connected():
            raise RuntimeError("Not connected to microscope")

        # Validate workflow first
        if not self.check_workflow(workflow_dict):
            raise ValueError("Workflow validation failed - would hit hard limits")

        try:
            # Clear system idle event before starting workflow
            self.event_manager.clear_event('system_idle')

            # Send workflow start command
            self.queue_manager.put_nowait('command', self.COMMAND_CODES_CAMERA_WORK_FLOW_START)
            self.event_manager.set_event('send')

            self.logger.info("Workflow start command sent")

            # Wait for system to become idle (workflow complete)
            self.wait_for_system_idle()

            self.logger.info("Workflow execution completed")

        except Exception as e:
            self.logger.error(f"Failed to send workflow: {e}")
            raise

    def wait_for_system_idle(self, timeout: float = 300.0) -> None:
        """
        Wait for microscope system to become idle.

        This method monitors the system_idle event and periodically queries
        the system state to handle cases where idle events might be missed.
        This is a workaround for missed system idle messages (see TODO comment
        in original code lines 103-111).

        Args:
            timeout: Maximum time to wait in seconds (default: 300s = 5 minutes)

        Raises:
            TimeoutError: If system doesn't become idle within timeout
        """
        self.logger.info("Waiting for system to become idle...")

        start_time = time.time()
        last_query_time = time.time()
        query_interval = 5.0  # Query system state every 5 seconds

        while not self.event_manager.is_set('system_idle'):
            # Check for timeout
            elapsed = time.time() - start_time
            if elapsed > timeout:
                raise TimeoutError(f"System did not become idle within {timeout}s")

            # Periodically query system state in case we missed the idle event
            if time.time() - last_query_time > query_interval:
                self.logger.debug("Querying system state to check for missed idle event")
                self.queue_manager.put_nowait('command', self.COMMAND_CODES_SYSTEM_STATE_GET)
                self.event_manager.set_event('send')
                last_query_time = time.time()

            time.sleep(0.1)

        self.logger.info(f"System became idle after {elapsed:.1f}s")

    def resolve_workflow(self,
                        xyzr_init: list,
                        timeout: float = 60.0) -> Optional[Any]:
        """
        Wait for workflow completion and retrieve image data.

        This method should be called immediately after send_workflow. It sets
        up the stage location, triggers visualization, and waits for the image
        data to arrive in the queue. Based on lines 114-133 of old code.

        Args:
            xyzr_init: Initial stage position [x, y, z, r] in mm and degrees
            timeout: Maximum time to wait for image data in seconds

        Returns:
            Image data from the workflow, or None if terminated or timeout

        Raises:
            TimeoutError: If no image data received within timeout
        """
        try:
            # Put initial stage location in queue for visualization
            self.queue_manager.put_nowait('stage_location', xyzr_init)

            # Trigger visualization event
            self.event_manager.set_event('visualize')

            self.logger.info("Waiting for workflow image data...")

            # Wait for image data or terminate event
            start_time = time.time()
            while True:
                # Check for terminate event
                if self.event_manager.is_set('terminate'):
                    self.logger.warning("Workflow resolution terminated by user")
                    return None

                # Check for timeout
                elapsed = time.time() - start_time
                if elapsed > timeout:
                    raise TimeoutError(f"No image data received within {timeout}s")

                # Try to get image from queue
                try:
                    image_data = self.queue_manager.get_queue('image').get(timeout=1.0)
                    self.logger.info("Image data received from workflow")
                    return image_data

                except Empty:
                    # Queue empty, continue waiting
                    continue
                except Exception as e:
                    self.logger.error(f"Error retrieving image data: {e}")
                    raise

        except Exception as e:
            self.logger.error(f"Failed to resolve workflow: {e}")
            raise

    def execute_workflow(self,
                        workflow_dict: Dict[str, Any],
                        xyzr_init: list,
                        wait_timeout: float = 300.0,
                        resolve_timeout: float = 60.0) -> Optional[Any]:
        """
        Execute complete workflow and return results.

        This is a convenience method that combines check_workflow,
        send_workflow, and resolve_workflow into a single call.

        Args:
            workflow_dict: Workflow configuration dictionary
            xyzr_init: Initial stage position [x, y, z, r]
            wait_timeout: Maximum time to wait for workflow completion
            resolve_timeout: Maximum time to wait for image data

        Returns:
            Image data from the workflow, or None if terminated

        Raises:
            RuntimeError: If not connected
            ValueError: If workflow validation fails
            TimeoutError: If workflow doesn't complete or no image data
        """
        self.logger.info("Executing workflow...")

        # Send workflow (includes validation and idle wait)
        self.send_workflow(workflow_dict)

        # Retrieve results
        image_data = self.resolve_workflow(xyzr_init, timeout=resolve_timeout)

        if image_data is not None:
            self.logger.info("Workflow executed successfully")
        else:
            self.logger.warning("Workflow terminated or returned no data")

        return image_data
