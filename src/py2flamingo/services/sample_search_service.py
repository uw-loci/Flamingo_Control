# ============================================================================
# src/py2flamingo/services/sample_search_service.py
"""
Service for sample boundary detection and focus optimization.

This service provides functionality for scanning the Y and Z axes to detect
sample boundaries using intensity analysis and peak detection. It replaces
the functionality from oldcodereference/microscope_interactions.py.
"""

import copy
import logging
import time
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any
from queue import Queue, Empty
from threading import Event

import numpy as np

from py2flamingo.models.microscope import Position
from py2flamingo.utils.calculations import (
    calculate_rolling_y_intensity,
    find_peak_bounds,
)
from py2flamingo.core.queue_manager import QueueManager
from py2flamingo.core.events import EventManager


class SampleSearchService:
    """
    Service for finding sample boundaries through Y-axis scanning and Z-axis focus optimization.

    This service handles:
    - Y-axis scanning with intensity analysis for sample boundary detection
    - Z-axis scanning for focus optimization using sub-stacks
    - Peak detection using rolling intensity calculations
    - MIP (Maximum Intensity Projection) handling
    - Coordinate tracking during scans

    Attributes:
        queue_manager: QueueManager for inter-thread communication
        event_manager: EventManager for synchronization events
        logger: Logger instance
    """

    def __init__(
        self,
        queue_manager: QueueManager,
        event_manager: EventManager,
    ):
        """
        Initialize the sample search service.

        Args:
            queue_manager: QueueManager instance for queue access
            event_manager: EventManager instance for event synchronization
        """
        self.queue_manager = queue_manager
        self.event_manager = event_manager
        self.logger = logging.getLogger(__name__)

    def scan_y_axis(
        self,
        sample_count: int,
        start_position: Position,
        search_params: Dict[str, Any],
    ) -> Tuple[Optional[List[Tuple[int, int]]], List[List], Position, int]:
        """
        Scan along Y-axis to find sample boundaries using intensity analysis.

        This method replicates y_axis_sample_boundary_search from the old code.
        It scans down the Y axis, collecting intensity data at each step, and uses
        peak detection to find sample boundaries.

        Args:
            sample_count: Expected number of samples to detect
            start_position: Starting position for the scan
            search_params: Dictionary containing:
                - y_max: Maximum Y position allowed (mm)
                - y_move: Step size for Y movement (typically FOV)
                - z_end: End position for Z-stack
                - workflow_dict: Workflow configuration dictionary
                - workflow_zstack_name: Name of workflow file
                - image_pixel_size_mm: Pixel size in mm

        Returns:
            Tuple containing:
                - bounds: List of (start, end) tuples for each sample peak, or None
                - coords: List of [position, intensity_map] pairs collected during scan
                - final_position: Final position after scan
                - iterations: Number of scan iterations performed

        Raises:
            ValueError: If required parameters are missing
            RuntimeError: If scan fails
        """
        # Extract required parameters
        y_max = search_params.get('y_max')
        y_move = search_params.get('y_move')
        z_end = search_params.get('z_end')
        workflow_dict = search_params.get('workflow_dict')
        workflow_zstack_name = search_params.get('workflow_zstack_name')
        image_pixel_size_mm = search_params.get('image_pixel_size_mm')

        if not all([y_max, y_move, z_end, workflow_dict, workflow_zstack_name]):
            raise ValueError("Missing required search parameters")

        self.logger.info(
            f"Starting Y-axis scan from {start_position.y:.3f}mm, "
            f"expecting {sample_count} samples"
        )

        coords = []
        current_position = copy.deepcopy(start_position)
        i = 0
        bounds = None

        # Get terminate event
        terminate_event = self.event_manager.get_event('terminate')

        while not terminate_event.is_set() and (start_position.y + y_move * i) < y_max:
            self.logger.info(f"Y-axis scan iteration {i + 1}")

            # Update Y position for this iteration
            current_position.y = start_position.y + y_move * i

            # Adjust workflow for current position
            # Note: This would need WorkflowService integration
            # For now, we'll log the position
            self.logger.debug(
                f"Scan position - x: {current_position.x}, y: {current_position.y}, "
                f"z: {current_position.z}, r: {current_position.r}"
            )

            # Calculate centered Z position for MIP
            z_centered = (current_position.z + z_end) / 2
            position_centered = copy.deepcopy(current_position)
            position_centered.z = z_centered

            # Execute workflow and get image data
            # This would require WorkflowExecutionService
            # For now, we'll create a placeholder
            try:
                image_data = self._execute_workflow_and_get_image(
                    position_centered,
                    workflow_dict,
                    workflow_zstack_name,
                )

                if image_data is None:
                    self.logger.warning(f"No image data received at iteration {i}")
                    i += 1
                    continue

                # Calculate rolling Y intensity
                _, y_intensity_map = calculate_rolling_y_intensity(image_data, 21)

                # Store coordinates and intensity data
                coords.append([copy.deepcopy(current_position), y_intensity_map])

                # Check if we've found all sample boundaries
                processing_output_full = [
                    y_intensity for coord in coords for _, y_intensity in coord[1]
                ]

                bounds = find_peak_bounds(processing_output_full, num_peaks=sample_count)

                # Check if all bounds are found (none are None)
                if bounds is not None and all(
                    b is not None for sublist in bounds for b in sublist
                ):
                    self.logger.info(f"Found all sample bounds: {bounds}")
                    break

            except Exception as e:
                self.logger.error(f"Error during Y-axis scan iteration {i}: {e}")
                # Continue to next iteration

            i += 1

        self.logger.info(
            f"Y-axis scan complete after {i} iterations. "
            f"Final position: {current_position.y:.3f}mm"
        )

        return bounds, coords, current_position, i

    def scan_z_axis(
        self,
        start_position: Position,
        z_params: Dict[str, Any],
    ) -> Tuple[Optional[float], List[List], Optional[List[Tuple[int, int]]], Any]:
        """
        Scan along Z-axis to find optimal focus position.

        This method replicates z_axis_sample_boundary_search from the old code.
        It scans through Z in sub-stacks and finds the brightest plane using
        MIP intensity analysis.

        Args:
            start_position: Starting position for Z scan
            z_params: Dictionary containing:
                - z_init: Initial central Z position
                - z_search_depth_mm: Total Z search range
                - z_step_depth_mm: Depth of each sub-Z-stack
                - workflow_dict: Workflow configuration
                - workflow_zstack_name: Workflow file name
                - iteration: Current iteration index (i)
                - total_loops: Total number of loops to perform

        Returns:
            Tuple containing:
                - optimal_z: Optimal Z position (centered), or None
                - coords_z: List of [position, intensity] pairs
                - bounds: Peak bounds or [[None, None]]
                - image_data: Last acquired image data

        Raises:
            ValueError: If required parameters are missing
        """
        # Extract parameters
        z_init = z_params.get('z_init')
        z_search_depth_mm = z_params.get('z_search_depth_mm')
        z_step_depth_mm = z_params.get('z_step_depth_mm')
        workflow_dict = z_params.get('workflow_dict')
        workflow_zstack_name = z_params.get('workflow_zstack_name')
        i = z_params.get('iteration', 0)
        loops = z_params.get('total_loops', 1)

        if not all([z_init is not None, z_search_depth_mm, z_step_depth_mm,
                   workflow_dict, workflow_zstack_name]):
            raise ValueError("Missing required Z-axis search parameters")

        self.logger.info(
            f"Z-axis scan: subset {i} of {loops - 1}, "
            f"depth={z_step_depth_mm}mm"
        )

        coords_z = z_params.get('coords_z', [])

        # Calculate Z positions for this sub-stack
        z_start = z_init - z_search_depth_mm / 2 + i * z_step_depth_mm
        z_end = z_init - z_search_depth_mm / 2 + (i + 1) * z_step_depth_mm

        self.logger.debug(f"Z scan range: {z_start:.4f} to {z_end:.4f} mm")

        current_position = copy.deepcopy(start_position)
        current_position.z = z_start

        # Center position for MIP
        z_centered = (z_start + z_end) / 2
        position_centered = copy.deepcopy(current_position)
        position_centered.z = z_centered

        try:
            # Execute workflow and get image
            image_data = self._execute_workflow_and_get_image(
                position_centered,
                workflow_dict,
                workflow_zstack_name,
            )

            if image_data is None:
                self.logger.warning("No image data received during Z scan")
                return None, coords_z, [[None, None]], None

            # Calculate intensity metric
            # Note: The old code uses calculate_rolling_y_intensity just for the
            # mean_largest_quarter value, not the intensity map
            mean_largest_quarter, _ = calculate_rolling_y_intensity(image_data, 3)

            # Store Z coordinate and intensity
            coords_z.append([copy.deepcopy(position_centered), mean_largest_quarter])

            # Extract intensity values for peak detection
            top25_percentile_means = [coord[1] for coord in coords_z]

            # Don't search for peaks too early
            bounds = [[None, None]]
            if len(top25_percentile_means) > 4:
                bounds_result = find_peak_bounds(
                    top25_percentile_means,
                    threshold_pct=30
                )
                if bounds_result:
                    bounds = bounds_result
                    self.logger.info(f"Z-axis bounds found: {bounds}")

            return z_centered, coords_z, bounds, image_data

        except Exception as e:
            self.logger.error(f"Error during Z-axis scan: {e}")
            return None, coords_z, [[None, None]], None

    def find_sample_boundaries(
        self,
        num_samples: int,
        start_position: Position,
        search_config: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        Find boundaries for multiple samples in the field of view.

        This is a high-level method that combines Y-axis scanning with
        optional Z-axis focus optimization.

        Args:
            num_samples: Expected number of samples
            start_position: Starting scan position
            search_config: Configuration dictionary containing search parameters

        Returns:
            List of dictionaries, each containing:
                - bounds: (start, end) tuple for the sample
                - position: Position object for sample center
                - intensity: Peak intensity value

        Raises:
            ValueError: If search fails
        """
        self.logger.info(f"Finding boundaries for {num_samples} samples")

        # Perform Y-axis scan
        bounds, coords, final_position, iterations = self.scan_y_axis(
            num_samples,
            start_position,
            search_config,
        )

        if bounds is None:
            raise ValueError("Failed to find sample boundaries in Y-axis scan")

        # Replace None values in bounds if needed
        y_max = search_config.get('y_max', 100.0)
        bounds = self._replace_none_in_bounds(bounds, int(y_max))

        # Build result list
        results = []
        for bound_pair in bounds:
            start_idx, end_idx = bound_pair

            # Calculate center position
            # This is simplified - in reality, you'd map indices back to positions
            center_idx = (start_idx + end_idx) // 2

            result = {
                'bounds': (start_idx, end_idx),
                'center_index': center_idx,
                'width': end_idx - start_idx,
            }
            results.append(result)

        self.logger.info(f"Found {len(results)} sample boundaries")
        return results

    def _replace_none_in_bounds(
        self,
        bounds: List[Tuple[Optional[int], Optional[int]]],
        replacement_max: int,
    ) -> List[Tuple[int, int]]:
        """
        Replace None values in bounds with default values.

        This replicates the replace_none function from the old code.
        If the first element is None, replace with 0.
        If the second element is None, replace with the provided maximum.

        Args:
            bounds: List of (start, end) tuples that may contain None
            replacement_max: Value to use for None in the second position

        Returns:
            List of (start, end) tuples with None values replaced
        """
        result = []
        for bound_pair in bounds:
            start, end = bound_pair

            if start is None:
                start = 0
                self.logger.info("Bounds edge hit - using 0 for start")

            if end is None:
                end = replacement_max
                self.logger.info(f"Bounds edge hit - using {replacement_max} for end")

            result.append((start, end))

        return result

    def _execute_workflow_and_get_image(
        self,
        position: Position,
        workflow_dict: Dict[str, Any],
        workflow_name: str,
    ) -> Optional[np.ndarray]:
        """
        Execute a workflow and retrieve the resulting image.

        This is a helper method that would integrate with WorkflowExecutionService
        and ImageAcquisitionService. For now, it provides a skeleton implementation.

        Args:
            position: Position for the workflow
            workflow_dict: Workflow configuration
            workflow_name: Name of the workflow file

        Returns:
            Image data as numpy array, or None if failed

        Note:
            This method requires integration with:
            - WorkflowExecutionService (to send workflows)
            - ImageAcquisitionService (to receive images)
            - File I/O for workflow files
        """
        self.logger.debug(
            f"Executing workflow '{workflow_name}' at position {position}"
        )

        # This is where we would:
        # 1. Update workflow_dict with the current position
        # 2. Write workflow file
        # 3. Send workflow start command
        # 4. Wait for system idle
        # 5. Retrieve image from queue

        # For now, return None to indicate this needs implementation
        self.logger.warning(
            "_execute_workflow_and_get_image is a placeholder - "
            "requires WorkflowExecutionService integration"
        )

        return None

    def _wait_for_workflow_completion(
        self,
        timeout: float = 60.0,
    ) -> bool:
        """
        Wait for workflow to complete by monitoring system_idle event.

        Args:
            timeout: Maximum time to wait in seconds

        Returns:
            True if workflow completed, False if timeout
        """
        system_idle = self.event_manager.get_event('system_idle')
        start_time = time.time()

        while not system_idle.is_set():
            if time.time() - start_time > timeout:
                self.logger.error(f"Workflow timeout after {timeout}s")
                return False

            time.sleep(0.1)

        return True

    def _get_image_from_queue(
        self,
        position: Position,
        timeout: float = 10.0,
    ) -> Optional[np.ndarray]:
        """
        Retrieve image data from the image queue.

        Args:
            position: Position information for the image
            timeout: Maximum time to wait for image

        Returns:
            Image data as numpy array, or None if timeout
        """
        image_queue = self.queue_manager.get_queue('image')
        terminate_event = self.event_manager.get_event('terminate')

        start_time = time.time()
        while True:
            try:
                image_data = image_queue.get(timeout=1.0)
                self.logger.debug(f"Received image data at {position}")
                return image_data

            except Empty:
                if terminate_event.is_set():
                    self.logger.warning("Terminate event set, aborting image retrieval")
                    return None

                if time.time() - start_time > timeout:
                    self.logger.error(f"Image retrieval timeout after {timeout}s")
                    return None

        return None


# ============================================================================
# Legacy Compatibility
# ============================================================================

def create_sample_search_service(
    queue_manager: QueueManager,
    event_manager: EventManager,
) -> SampleSearchService:
    """
    Factory function for creating SampleSearchService instances.

    Args:
        queue_manager: QueueManager instance
        event_manager: EventManager instance

    Returns:
        Configured SampleSearchService instance
    """
    return SampleSearchService(
        queue_manager=queue_manager,
        event_manager=event_manager,
    )
