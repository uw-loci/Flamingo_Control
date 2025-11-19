"""Workflow Validator - Centralized validation logic for workflows.

This module consolidates all workflow validation logic that was previously
scattered across multiple components into a single, consistent validator.
"""

import logging
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass

from ..models.data.workflow import (
    Workflow, WorkflowType, WorkflowState,
    IlluminationSettings, StackSettings, TileSettings,
    TimeLapseSettings, ExperimentSettings
)
from ..models.hardware.stage import Position, StageLimits
from ..models.hardware.laser import PowerLimits
from ..core.errors import FlamingoError, ValidationError


logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of workflow validation."""
    is_valid: bool
    errors: List[str]
    warnings: List[str]
    suggestions: List[str]

    def add_error(self, message: str):
        """Add an error message."""
        self.errors.append(message)
        self.is_valid = False

    def add_warning(self, message: str):
        """Add a warning message."""
        self.warnings.append(message)

    def add_suggestion(self, message: str):
        """Add a suggestion."""
        self.suggestions.append(message)

    def __str__(self) -> str:
        """String representation of validation result."""
        if self.is_valid:
            return "Validation passed"
        else:
            errors_str = f"Errors: {', '.join(self.errors)}" if self.errors else ""
            warnings_str = f"Warnings: {', '.join(self.warnings)}" if self.warnings else ""
            return f"Validation failed. {errors_str} {warnings_str}"


@dataclass
class HardwareConstraints:
    """Hardware constraints for validation."""
    stage_limits: Optional[StageLimits] = None
    laser_power_limits: Optional[PowerLimits] = None
    available_lasers: List[str] = None
    available_filters: List[int] = None
    max_exposure_ms: float = 10000.0
    min_exposure_ms: float = 0.1
    max_z_velocity_mm_s: float = 2.0
    min_z_velocity_mm_s: float = 0.01
    camera_roi_width: int = 2048
    camera_roi_height: int = 2048
    max_file_size_gb: float = 100.0


class WorkflowValidationError(ValidationError):
    """Raised when workflow validation fails."""
    pass


class WorkflowValidator:
    """Validates workflows against hardware constraints and best practices.

    Consolidates validation logic from:
    - workflow_parser.validate_workflow() - Structure validation
    - WorkflowService.validate_workflow() - Content validation
    - WorkflowExecutionService.check_workflow() - Microscope validation
    """

    def __init__(self, constraints: Optional[HardwareConstraints] = None):
        """Initialize validator.

        Args:
            constraints: Hardware constraints for validation
        """
        self.constraints = constraints or HardwareConstraints()
        self._init_default_constraints()

    def _init_default_constraints(self):
        """Initialize default hardware constraints."""
        if not self.constraints.available_lasers:
            self.constraints.available_lasers = [
                "Laser 1 405 nm",
                "Laser 2 445 nm",
                "Laser 3 488 nm",
                "Laser 4 515 nm",
                "Laser 5 561 nm",
                "Laser 6 594 nm",
                "Laser 7 640 nm"
            ]

        if not self.constraints.available_filters:
            self.constraints.available_filters = list(range(1, 7))

        if not self.constraints.laser_power_limits:
            from ..models.hardware.laser import PowerLimits
            self.constraints.laser_power_limits = PowerLimits(
                min_mw=0, max_mw=100, safe_max_mw=50
            )

    # ==================== Main Validation ====================

    def validate(self, workflow: Workflow, strict: bool = False) -> bool:
        """Validate a workflow.

        Args:
            workflow: Workflow to validate
            strict: If True, warnings become errors

        Returns:
            True if valid

        Raises:
            WorkflowValidationError: If validation fails
        """
        result = self.validate_detailed(workflow)

        if strict and result.warnings:
            # Convert warnings to errors in strict mode
            for warning in result.warnings:
                result.add_error(f"Strict mode: {warning}")

        if not result.is_valid:
            error_msg = f"Workflow validation failed: {', '.join(result.errors)}"
            raise WorkflowValidationError(
                error_msg,
                suggestions=result.suggestions
            )

        return True

    def validate_detailed(self, workflow: Workflow) -> ValidationResult:
        """Perform detailed validation with results.

        Args:
            workflow: Workflow to validate

        Returns:
            Detailed validation result
        """
        result = ValidationResult(
            is_valid=True,
            errors=[],
            warnings=[],
            suggestions=[]
        )

        # Structure validation
        self._validate_structure(workflow, result)

        # Position validation
        self._validate_positions(workflow, result)

        # Illumination validation
        self._validate_illumination(workflow, result)

        # Settings validation based on workflow type
        if workflow.workflow_type == WorkflowType.ZSTACK:
            self._validate_stack_settings(workflow, result)
        elif workflow.workflow_type == WorkflowType.TILE:
            self._validate_tile_settings(workflow, result)
        elif workflow.workflow_type == WorkflowType.TIME_LAPSE:
            self._validate_time_lapse_settings(workflow, result)

        # Experiment settings validation
        self._validate_experiment_settings(workflow, result)

        # Hardware compatibility
        self._validate_hardware_compatibility(workflow, result)

        # Best practices validation
        self._validate_best_practices(workflow, result)

        logger.info(f"Validation result: {result}")
        return result

    # ==================== Structure Validation ====================

    def _validate_structure(self, workflow: Workflow, result: ValidationResult):
        """Validate workflow structure."""
        # Must have a name
        if not workflow.name or not workflow.name.strip():
            result.add_error("Workflow must have a name")

        # Must have a type
        if not workflow.workflow_type:
            result.add_error("Workflow must have a type")

        # Must have start position
        if not workflow.start_position:
            result.add_error("Workflow must have a start position")

        # Check required settings for workflow type
        if workflow.workflow_type == WorkflowType.ZSTACK and not workflow.stack_settings:
            result.add_error("Z-stack workflow requires stack settings")

        if workflow.workflow_type == WorkflowType.TILE and not workflow.tile_settings:
            result.add_error("Tile workflow requires tile settings")

        if workflow.workflow_type == WorkflowType.TIME_LAPSE and not workflow.time_lapse_settings:
            result.add_error("Time-lapse workflow requires time-lapse settings")

    # ==================== Position Validation ====================

    def _validate_positions(self, workflow: Workflow, result: ValidationResult):
        """Validate positions against stage limits."""
        if not self.constraints.stage_limits:
            result.add_warning("No stage limits defined for validation")
            return

        # Validate start position
        if workflow.start_position:
            if not self.constraints.stage_limits.is_position_valid(workflow.start_position):
                result.add_error(
                    f"Start position {workflow.start_position} is outside stage limits"
                )

        # Validate end position if present
        if workflow.end_position:
            if not self.constraints.stage_limits.is_position_valid(workflow.end_position):
                result.add_error(
                    f"End position {workflow.end_position} is outside stage limits"
                )

        # Validate multiple positions
        for i, pos in enumerate(workflow.positions):
            if not self.constraints.stage_limits.is_position_valid(pos):
                result.add_error(
                    f"Position {i} ({pos}) is outside stage limits"
                )

        # Validate tile positions if applicable
        if workflow.tile_settings and workflow.start_position:
            tile_positions = workflow.tile_settings.get_tile_positions(workflow.start_position)
            for i, pos in enumerate(tile_positions):
                if not self.constraints.stage_limits.is_position_valid(pos):
                    result.add_warning(
                        f"Tile {i} position ({pos}) is outside stage limits"
                    )
                    break  # Don't report all tiles

    # ==================== Illumination Validation ====================

    def _validate_illumination(self, workflow: Workflow, result: ValidationResult):
        """Validate illumination settings."""
        illum = workflow.illumination

        if not illum:
            result.add_warning("No illumination settings defined")
            return

        # Validate laser settings
        if illum.laser_enabled:
            if not illum.laser_channel:
                result.add_error("Laser enabled but no channel specified")

            if illum.laser_channel and illum.laser_channel not in self.constraints.available_lasers:
                result.add_error(
                    f"Laser channel '{illum.laser_channel}' not available. "
                    f"Available: {', '.join(self.constraints.available_lasers)}"
                )

            if illum.laser_power_mw < 0:
                result.add_error(f"Laser power cannot be negative: {illum.laser_power_mw} mW")

            if self.constraints.laser_power_limits:
                limits = self.constraints.laser_power_limits
                if not limits.is_valid_power(illum.laser_power_mw, use_safe_limit=False):
                    result.add_error(
                        f"Laser power {illum.laser_power_mw} mW outside limits "
                        f"({limits.min_mw}-{limits.max_mw} mW)"
                    )
                elif not limits.is_valid_power(illum.laser_power_mw, use_safe_limit=True):
                    result.add_warning(
                        f"Laser power {illum.laser_power_mw} mW exceeds safe limit "
                        f"({limits.safe_max_mw} mW)"
                    )

        # Validate LED settings
        if illum.led_enabled:
            if illum.led_intensity_percent < 0 or illum.led_intensity_percent > 100:
                result.add_error(
                    f"LED intensity must be 0-100%: {illum.led_intensity_percent}%"
                )

        # Validate filter position
        if illum.filter_position is not None:
            if illum.filter_position not in self.constraints.available_filters:
                result.add_error(
                    f"Filter position {illum.filter_position} not available. "
                    f"Available: {self.constraints.available_filters}"
                )

        # Check that at least one light source is enabled
        if not illum.laser_enabled and not illum.led_enabled:
            result.add_warning("No illumination source enabled")

    # ==================== Stack Settings Validation ====================

    def _validate_stack_settings(self, workflow: Workflow, result: ValidationResult):
        """Validate z-stack settings."""
        stack = workflow.stack_settings

        if not stack:
            return

        # Validate number of planes
        if stack.num_planes < 1:
            result.add_error(f"Number of planes must be >= 1: {stack.num_planes}")
        elif stack.num_planes > 1000:
            result.add_warning(f"Large number of planes ({stack.num_planes}) may take long time")

        # Validate z-step
        if stack.z_step_um <= 0:
            result.add_error(f"Z step must be positive: {stack.z_step_um} μm")
        elif stack.z_step_um < 0.1:
            result.add_warning(f"Very small Z step ({stack.z_step_um} μm) may not be achievable")
        elif stack.z_step_um > 100:
            result.add_warning(f"Large Z step ({stack.z_step_um} μm) may miss details")

        # Validate velocity
        if stack.z_velocity_mm_s <= 0:
            result.add_error(f"Z velocity must be positive: {stack.z_velocity_mm_s} mm/s")
        elif stack.z_velocity_mm_s < self.constraints.min_z_velocity_mm_s:
            result.add_error(
                f"Z velocity too slow: {stack.z_velocity_mm_s} mm/s "
                f"(min: {self.constraints.min_z_velocity_mm_s} mm/s)"
            )
        elif stack.z_velocity_mm_s > self.constraints.max_z_velocity_mm_s:
            result.add_error(
                f"Z velocity too fast: {stack.z_velocity_mm_s} mm/s "
                f"(max: {self.constraints.max_z_velocity_mm_s} mm/s)"
            )

        # Check total Z range
        total_range = stack.calculate_z_range()
        if total_range > 10000:  # 10mm
            result.add_warning(f"Large Z range ({total_range/1000:.1f} mm)")

    # ==================== Tile Settings Validation ====================

    def _validate_tile_settings(self, workflow: Workflow, result: ValidationResult):
        """Validate tile scan settings."""
        tile = workflow.tile_settings

        if not tile:
            return

        # Validate tile counts
        if tile.num_tiles_x < 1 or tile.num_tiles_y < 1:
            result.add_error("Number of tiles must be >= 1")

        total_tiles = tile.total_tiles
        if total_tiles > 1000:
            result.add_warning(f"Large number of tiles ({total_tiles}) will take long time")
            result.add_suggestion("Consider reducing tile count or using lower resolution")

        # Validate tile size
        if tile.tile_size_x_mm <= 0 or tile.tile_size_y_mm <= 0:
            result.add_error("Tile size must be positive")

        # Validate overlap
        if tile.overlap_percent < 0:
            result.add_error(f"Overlap cannot be negative: {tile.overlap_percent}%")
        elif tile.overlap_percent >= 100:
            result.add_error(f"Overlap must be < 100%: {tile.overlap_percent}%")
        elif tile.overlap_percent < 5:
            result.add_warning("Low overlap may cause stitching issues")
            result.add_suggestion("Consider 10-20% overlap for better stitching")

        # Check total scan area
        scan_width, scan_height = tile.calculate_scan_area()
        if scan_width > 50 or scan_height > 50:  # mm
            result.add_warning(f"Large scan area: {scan_width:.1f} x {scan_height:.1f} mm")

    # ==================== Time-Lapse Validation ====================

    def _validate_time_lapse_settings(self, workflow: Workflow, result: ValidationResult):
        """Validate time-lapse settings."""
        time_lapse = workflow.time_lapse_settings

        if not time_lapse:
            return

        # Validate timepoints
        if time_lapse.num_timepoints < 1:
            result.add_error("Number of timepoints must be >= 1")

        # Validate interval
        interval_s = time_lapse.get_interval_seconds()
        if interval_s <= 0:
            result.add_error("Time interval must be positive")
        elif interval_s < 1:
            result.add_warning("Very short interval may not allow acquisition to complete")

        # Check total duration
        total_duration = time_lapse.calculate_total_duration()
        if total_duration > 86400:  # 24 hours
            hours = total_duration / 3600
            result.add_warning(f"Long acquisition time: {hours:.1f} hours")
            result.add_suggestion("Ensure sample stability for long acquisitions")

    # ==================== Experiment Settings Validation ====================

    def _validate_experiment_settings(self, workflow: Workflow, result: ValidationResult):
        """Validate experiment settings."""
        exp = workflow.experiment_settings

        if not exp:
            result.add_warning("No experiment settings defined")
            return

        # Validate save format
        valid_formats = ["tiff", "hdf5", "zarr", "ome-tiff", "png"]
        if exp.save_data and exp.save_format not in valid_formats:
            result.add_error(f"Invalid save format: {exp.save_format}")

        # Check save directory
        if exp.save_data:
            if not exp.save_directory:
                result.add_error("Save directory not specified")
            # Note: Don't check if directory exists as it may be created

        # Estimate data size
        if exp.save_data and workflow.images_expected > 0:
            # Rough estimate: 4MB per image
            estimated_gb = (workflow.images_expected * 4) / 1024
            if estimated_gb > self.constraints.max_file_size_gb:
                result.add_error(
                    f"Estimated data size ({estimated_gb:.1f} GB) exceeds limit "
                    f"({self.constraints.max_file_size_gb} GB)"
                )
            elif estimated_gb > 10:
                result.add_warning(f"Large data size expected: {estimated_gb:.1f} GB")
                result.add_suggestion("Ensure sufficient disk space")

    # ==================== Hardware Compatibility ====================

    def _validate_hardware_compatibility(self, workflow: Workflow, result: ValidationResult):
        """Validate hardware compatibility."""
        compatibility = self.check_hardware_compatibility(workflow)

        for component, is_compatible in compatibility.items():
            if not is_compatible:
                result.add_error(f"Hardware incompatibility: {component}")

    def check_hardware_compatibility(self, workflow: Workflow) -> Dict[str, bool]:
        """Check if workflow is compatible with current hardware.

        Args:
            workflow: Workflow to check

        Returns:
            Dictionary of component -> compatibility status
        """
        compatibility = {}

        # Check stage compatibility
        if self.constraints.stage_limits and workflow.start_position:
            compatibility["stage"] = self.constraints.stage_limits.is_position_valid(
                workflow.start_position
            )
        else:
            compatibility["stage"] = True

        # Check laser compatibility
        if workflow.illumination and workflow.illumination.laser_channel:
            compatibility["laser"] = (
                workflow.illumination.laser_channel in self.constraints.available_lasers
            )
        else:
            compatibility["laser"] = True

        # Check filter compatibility
        if workflow.illumination and workflow.illumination.filter_position is not None:
            compatibility["filter"] = (
                workflow.illumination.filter_position in self.constraints.available_filters
            )
        else:
            compatibility["filter"] = True

        # Check camera compatibility (simplified)
        compatibility["camera"] = True

        return compatibility

    # ==================== Best Practices ====================

    def _validate_best_practices(self, workflow: Workflow, result: ValidationResult):
        """Validate against best practices."""
        # Check for reasonable exposure times
        if workflow.experiment_settings:
            # This would need actual exposure time from camera settings
            pass

        # Check for photobleaching risk
        if workflow.illumination and workflow.illumination.laser_enabled:
            power = workflow.illumination.laser_power_mw
            if workflow.time_lapse_settings:
                num_exposures = workflow.time_lapse_settings.num_timepoints
                if power > 10 and num_exposures > 100:
                    result.add_warning(
                        "High laser power with many timepoints may cause photobleaching"
                    )
                    result.add_suggestion("Consider reducing laser power or timepoints")

        # Check for reasonable acquisition rates
        if workflow.time_lapse_settings:
            interval = workflow.time_lapse_settings.get_interval_seconds()
            if workflow.stack_settings:
                stack_time = workflow.stack_settings.calculate_acquisition_time(10)  # 10ms exposure
                if stack_time > interval * 0.8:
                    result.add_warning(
                        "Stack acquisition time may exceed time-lapse interval"
                    )

        # Suggest optimizations
        if workflow.tile_settings and workflow.tile_settings.total_tiles > 100:
            if not workflow.tile_settings.focus_each_tile:
                result.add_suggestion(
                    "Consider enabling focus for each tile in large scans"
                )

    # ==================== Utilities ====================

    def update_constraints(self, constraints: HardwareConstraints):
        """Update hardware constraints.

        Args:
            constraints: New constraints
        """
        self.constraints = constraints
        logger.info("Updated hardware constraints")

    def set_stage_limits(self, stage_limits: StageLimits):
        """Set stage limits for validation.

        Args:
            stage_limits: Stage limits
        """
        self.constraints.stage_limits = stage_limits

    def set_available_lasers(self, lasers: List[str]):
        """Set available lasers.

        Args:
            lasers: List of laser names
        """
        self.constraints.available_lasers = lasers