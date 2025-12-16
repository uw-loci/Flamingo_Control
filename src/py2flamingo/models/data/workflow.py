"""Workflow models for microscope acquisition sequences.

This module provides enhanced models for workflows including
snapshots, z-stacks, tile scans, and time-lapse acquisitions.
"""

from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List, Union, Tuple
from enum import Enum
from datetime import datetime, timedelta
from pathlib import Path
from ..base import ValidatedModel, ValidationError
from ..hardware.stage import Position


class WorkflowType(Enum):
    """Types of acquisition workflows."""
    SNAPSHOT = "snapshot"          # Single image
    ZSTACK = "zstack"              # Z-stack acquisition
    TILE = "tile"                  # Tile scan
    MULTI_ANGLE = "multi_angle"    # Multi-angle acquisition
    TIME_LAPSE = "time_lapse"      # Time series
    MULTI_POSITION = "multi_pos"   # Multiple positions
    CUSTOM = "custom"              # Custom workflow


class WorkflowState(Enum):
    """Workflow execution states."""
    IDLE = "idle"                  # Not started
    VALIDATING = "validating"      # Checking parameters
    PREPARING = "preparing"        # Moving to start position
    EXECUTING = "executing"        # Acquiring images
    PAUSED = "paused"             # Temporarily halted
    COMPLETED = "completed"        # Successfully finished
    CANCELLED = "cancelled"        # User cancelled
    ERROR = "error"               # Error occurred


class TillingPattern(Enum):
    """Patterns for tile acquisition."""
    RASTER = "raster"             # Left-to-right, top-to-bottom
    SNAKE = "snake"               # Bidirectional (serpentine)
    SPIRAL_OUT = "spiral_out"     # Center outward spiral
    SPIRAL_IN = "spiral_in"       # Outside inward spiral
    RANDOM = "random"             # Random order


@dataclass
class IlluminationSettings(ValidatedModel):
    """Illumination configuration for workflow."""
    laser_channel: Optional[str] = None
    laser_power_mw: float = 0.0
    laser_enabled: bool = False
    led_channel: Optional[str] = None
    led_intensity_percent: float = 0.0
    led_enabled: bool = False
    filter_position: Optional[int] = None
    shutter_open_before_ms: float = 0.0  # Open shutter before acquisition
    shutter_close_after_ms: float = 0.0  # Keep open after acquisition

    def validate(self) -> None:
        """Validate illumination settings."""
        if self.laser_power_mw < 0:
            raise ValidationError(f"Laser power cannot be negative: {self.laser_power_mw} mW")

        if not (0 <= self.led_intensity_percent <= 100):
            raise ValidationError(
                f"LED intensity must be 0-100%: {self.led_intensity_percent}%"
            )

        if self.shutter_open_before_ms < 0 or self.shutter_close_after_ms < 0:
            raise ValidationError("Shutter timing cannot be negative")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to workflow dictionary format.

        Returns:
            Dictionary for workflow command
        """
        settings = {}

        # Laser settings
        if self.laser_channel:
            laser_on = 1 if self.laser_enabled else 0
            settings[self.laser_channel] = f"{self.laser_power_mw:.2f} {laser_on}"

        # LED settings
        if self.led_channel:
            led_on = 1 if self.led_enabled else 0
            settings[self.led_channel] = f"{self.led_intensity_percent:.1f} {led_on}"

        return settings


@dataclass
class StackSettings(ValidatedModel):
    """Settings for z-stack acquisition."""
    num_planes: int = 1
    z_step_um: float = 1.0
    z_range_um: Optional[float] = None  # Alternative to num_planes
    z_velocity_mm_s: float = 0.4
    bidirectional: bool = False
    return_to_start: bool = True
    use_piezo: bool = False  # Use piezo for fine Z movement
    settle_time_ms: float = 0.0  # Wait after Z movement

    def validate(self) -> None:
        """Validate stack settings."""
        if self.num_planes < 1:
            raise ValidationError(f"Number of planes must be >=1: {self.num_planes}")

        if self.z_step_um <= 0:
            raise ValidationError(f"Z step must be positive: {self.z_step_um} um")

        if self.z_velocity_mm_s <= 0:
            raise ValidationError(f"Z velocity must be positive: {self.z_velocity_mm_s} mm/s")

        if self.settle_time_ms < 0:
            raise ValidationError(f"Settle time cannot be negative: {self.settle_time_ms} ms")

        # Calculate num_planes from range if needed
        if self.z_range_um is not None:
            self.num_planes = int(self.z_range_um / self.z_step_um) + 1

    def calculate_z_range(self) -> float:
        """Calculate total Z range for stack.

        Returns:
            Total Z distance in micrometers
        """
        return (self.num_planes - 1) * self.z_step_um

    def calculate_acquisition_time(self, exposure_ms: float) -> float:
        """Estimate acquisition time for stack.

        Args:
            exposure_ms: Exposure time per plane

        Returns:
            Estimated time in seconds
        """
        # Time for Z movements
        z_range_mm = self.calculate_z_range() / 1000.0
        z_move_time = z_range_mm / self.z_velocity_mm_s

        # Add settle time for each plane
        settle_time_total = self.num_planes * self.settle_time_ms / 1000.0

        # Add exposure time
        exposure_time_total = self.num_planes * exposure_ms / 1000.0

        # Add return time if needed
        return_time = z_move_time if self.return_to_start else 0

        return z_move_time + settle_time_total + exposure_time_total + return_time


@dataclass
class TileSettings(ValidatedModel):
    """Settings for tile scan acquisition."""
    num_tiles_x: int = 1
    num_tiles_y: int = 1
    tile_size_x_mm: float = 1.0
    tile_size_y_mm: float = 1.0
    overlap_percent: float = 10.0
    pattern: TillingPattern = TillingPattern.SNAKE
    focus_each_tile: bool = False
    focus_strategy: str = "none"  # "none", "each", "predictive", "map"
    stage_settle_time_ms: float = 100.0

    def validate(self) -> None:
        """Validate tile settings."""
        if self.num_tiles_x < 1 or self.num_tiles_y < 1:
            raise ValidationError("Number of tiles must be >= 1")

        if self.tile_size_x_mm <= 0 or self.tile_size_y_mm <= 0:
            raise ValidationError("Tile size must be positive")

        if not (0 <= self.overlap_percent < 100):
            raise ValidationError(f"Overlap must be 0-99%: {self.overlap_percent}%")

        if self.stage_settle_time_ms < 0:
            raise ValidationError("Settle time cannot be negative")

    def calculate_scan_area(self) -> Tuple[float, float]:
        """Calculate total scan area.

        Returns:
            Tuple of (width_mm, height_mm)
        """
        overlap_factor = 1.0 - self.overlap_percent / 100.0
        width = self.tile_size_x_mm * (1 + (self.num_tiles_x - 1) * overlap_factor)
        height = self.tile_size_y_mm * (1 + (self.num_tiles_y - 1) * overlap_factor)
        return (width, height)

    def get_tile_positions(self, start_pos: Position) -> List[Position]:
        """Calculate positions for all tiles.

        Args:
            start_pos: Starting position (top-left corner)

        Returns:
            List of positions for each tile
        """
        positions = []
        overlap_factor = 1.0 - self.overlap_percent / 100.0
        step_x = self.tile_size_x_mm * overlap_factor
        step_y = self.tile_size_y_mm * overlap_factor

        if self.pattern == TillingPattern.SNAKE:
            for y in range(self.num_tiles_y):
                row_positions = []
                for x in range(self.num_tiles_x):
                    pos = Position(
                        x=start_pos.x + x * step_x,
                        y=start_pos.y + y * step_y,
                        z=start_pos.z,
                        r=start_pos.r
                    )
                    row_positions.append(pos)

                # Reverse even rows for snake pattern
                if y % 2 == 1:
                    row_positions.reverse()

                positions.extend(row_positions)

        elif self.pattern == TillingPattern.RASTER:
            for y in range(self.num_tiles_y):
                for x in range(self.num_tiles_x):
                    pos = Position(
                        x=start_pos.x + x * step_x,
                        y=start_pos.y + y * step_y,
                        z=start_pos.z,
                        r=start_pos.r
                    )
                    positions.append(pos)

        # Add other patterns as needed

        return positions

    @property
    def total_tiles(self) -> int:
        """Get total number of tiles."""
        return self.num_tiles_x * self.num_tiles_y


@dataclass
class TimeLapseSettings(ValidatedModel):
    """Settings for time-lapse acquisition."""
    num_timepoints: int = 1
    interval_seconds: float = 60.0
    interval_units: str = "seconds"  # "seconds", "minutes", "hours"
    total_duration: Optional[float] = None  # Alternative to num_timepoints
    start_delay_seconds: float = 0.0
    adaptive_interval: bool = False  # Adjust based on acquisition time

    def validate(self) -> None:
        """Validate time-lapse settings."""
        if self.num_timepoints < 1:
            raise ValidationError("Number of timepoints must be >= 1")

        if self.interval_seconds <= 0:
            raise ValidationError("Interval must be positive")

        if self.start_delay_seconds < 0:
            raise ValidationError("Start delay cannot be negative")

        # Calculate num_timepoints from duration if needed
        if self.total_duration is not None:
            interval = self.get_interval_seconds()
            self.num_timepoints = int(self.total_duration / interval) + 1

    def get_interval_seconds(self) -> float:
        """Get interval in seconds regardless of units.

        Returns:
            Interval in seconds
        """
        multipliers = {
            "seconds": 1.0,
            "minutes": 60.0,
            "hours": 3600.0
        }
        return self.interval_seconds * multipliers.get(self.interval_units, 1.0)

    def calculate_total_duration(self) -> float:
        """Calculate total duration of time-lapse.

        Returns:
            Total duration in seconds
        """
        interval = self.get_interval_seconds()
        return self.start_delay_seconds + (self.num_timepoints - 1) * interval


@dataclass
class ExperimentSettings(ValidatedModel):
    """General experiment and data saving settings."""
    save_data: bool = True
    save_format: str = "tiff"  # "tiff", "hdf5", "zarr", "ome-tiff"
    save_directory: Path = field(default_factory=lambda: Path("data"))
    file_prefix: str = "exp"
    compression: Optional[str] = None  # "none", "lzw", "zlib", etc.
    save_metadata: bool = True
    save_thumbnails: bool = True
    display_during_acquisition: bool = True
    max_projection_display: bool = True
    auto_contrast: bool = True
    comment: str = ""

    def validate(self) -> None:
        """Validate experiment settings."""
        valid_formats = ["tiff", "hdf5", "zarr", "ome-tiff", "png", "raw"]
        if self.save_format not in valid_formats:
            raise ValidationError(f"Invalid save format: {self.save_format}")

        if not self.file_prefix:
            raise ValidationError("File prefix cannot be empty")

    def get_output_path(self, timestamp: datetime = None) -> Path:
        """Generate output file path.

        Args:
            timestamp: Acquisition timestamp

        Returns:
            Full output path
        """
        if timestamp is None:
            timestamp = datetime.now()

        date_str = timestamp.strftime("%Y%m%d_%H%M%S")
        filename = f"{self.file_prefix}_{date_str}"

        return self.save_directory / filename


@dataclass
class WorkflowStep:
    """Single step in a workflow execution."""
    index: int
    name: str
    position: Optional[Position] = None
    z_position: Optional[float] = None  # For z-stacks
    timepoint: Optional[int] = None  # For time-lapse
    tile_index: Optional[int] = None  # For tiles
    channel: Optional[str] = None  # For multi-channel
    status: str = "pending"  # pending, executing, completed, error
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    error_message: Optional[str] = None
    image_path: Optional[Path] = None

    def mark_started(self) -> None:
        """Mark step as started."""
        self.status = "executing"
        self.start_time = datetime.now()

    def mark_completed(self, image_path: Optional[Path] = None) -> None:
        """Mark step as completed."""
        self.status = "completed"
        self.end_time = datetime.now()
        self.image_path = image_path

    def mark_error(self, error: str) -> None:
        """Mark step as failed."""
        self.status = "error"
        self.end_time = datetime.now()
        self.error_message = error

    def get_duration(self) -> Optional[float]:
        """Get step execution time in seconds."""
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return None


@dataclass
class Workflow(ValidatedModel):
    """Complete workflow model for acquisition sequences."""
    workflow_type: WorkflowType = WorkflowType.SNAPSHOT
    name: str = ""
    start_position: Position = field(default_factory=Position)
    end_position: Optional[Position] = None

    # Settings for different workflow types
    illumination: IlluminationSettings = field(default_factory=IlluminationSettings)
    stack_settings: Optional[StackSettings] = None
    tile_settings: Optional[TileSettings] = None
    time_lapse_settings: Optional[TimeLapseSettings] = None
    experiment_settings: ExperimentSettings = field(default_factory=ExperimentSettings)

    # Multiple positions support
    positions: List[Position] = field(default_factory=list)
    channels: List[str] = field(default_factory=list)

    # Execution state
    state: WorkflowState = WorkflowState.IDLE
    steps: List[WorkflowStep] = field(default_factory=list)
    current_step_index: int = 0
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    error_message: Optional[str] = None

    # Statistics
    images_acquired: int = 0
    images_expected: int = 0
    data_size_mb: float = 0.0

    def validate(self) -> None:
        """Validate workflow configuration."""
        # Validate workflow type has required settings
        if self.workflow_type == WorkflowType.ZSTACK and not self.stack_settings:
            raise ValidationError("Z-stack workflow requires stack settings")

        if self.workflow_type == WorkflowType.TILE and not self.tile_settings:
            raise ValidationError("Tile workflow requires tile settings")

        if self.workflow_type == WorkflowType.TIME_LAPSE and not self.time_lapse_settings:
            raise ValidationError("Time-lapse workflow requires time-lapse settings")

        # Validate positions
        if self.workflow_type == WorkflowType.MULTI_POSITION and not self.positions:
            raise ValidationError("Multi-position workflow requires position list")

    def calculate_total_images(self) -> int:
        """Calculate total number of images to acquire.

        Returns:
            Total image count
        """
        count = 1

        # Stack multiplier
        if self.stack_settings:
            count *= self.stack_settings.num_planes

        # Tile multiplier
        if self.tile_settings:
            count *= self.tile_settings.total_tiles

        # Time-lapse multiplier
        if self.time_lapse_settings:
            count *= self.time_lapse_settings.num_timepoints

        # Position multiplier
        if self.positions:
            count *= len(self.positions)

        # Channel multiplier
        if self.channels:
            count *= len(self.channels)

        self.images_expected = count
        return count

    def estimate_duration(self, exposure_ms: float = 10.0) -> float:
        """Estimate total workflow duration.

        Args:
            exposure_ms: Exposure time per image

        Returns:
            Estimated duration in seconds
        """
        base_time = self.calculate_total_images() * exposure_ms / 1000.0

        # Add movement times
        if self.stack_settings:
            base_time += self.stack_settings.calculate_acquisition_time(exposure_ms)

        if self.tile_settings:
            # Rough estimate for stage movements
            base_time += self.tile_settings.total_tiles * self.tile_settings.stage_settle_time_ms / 1000.0

        if self.time_lapse_settings:
            base_time = self.time_lapse_settings.calculate_total_duration()

        return base_time

    def generate_steps(self) -> List[WorkflowStep]:
        """Generate execution steps for workflow.

        Returns:
            List of workflow steps
        """
        steps = []
        step_index = 0

        # Determine iteration order based on workflow type
        positions = self.positions if self.positions else [self.start_position]
        channels = self.channels if self.channels else [None]

        # Time-lapse loop (outermost for time-series)
        timepoints = 1
        if self.time_lapse_settings:
            timepoints = self.time_lapse_settings.num_timepoints

        for t in range(timepoints):
            # Position loop
            for pos in positions:
                # Tile loop
                if self.tile_settings:
                    tile_positions = self.tile_settings.get_tile_positions(pos)
                else:
                    tile_positions = [pos]

                for tile_idx, tile_pos in enumerate(tile_positions):
                    # Channel loop
                    for channel in channels:
                        # Z-stack loop (innermost for speed)
                        if self.stack_settings:
                            for z in range(self.stack_settings.num_planes):
                                z_pos = tile_pos.z + z * self.stack_settings.z_step_um / 1000.0
                                step = WorkflowStep(
                                    index=step_index,
                                    name=f"T{t}_P{positions.index(pos)}_Tile{tile_idx}_Z{z}",
                                    position=tile_pos,
                                    z_position=z_pos,
                                    timepoint=t if self.time_lapse_settings else None,
                                    tile_index=tile_idx if self.tile_settings else None,
                                    channel=channel
                                )
                                steps.append(step)
                                step_index += 1
                        else:
                            step = WorkflowStep(
                                index=step_index,
                                name=f"T{t}_P{positions.index(pos)}_Tile{tile_idx}",
                                position=tile_pos,
                                timepoint=t if self.time_lapse_settings else None,
                                tile_index=tile_idx if self.tile_settings else None,
                                channel=channel
                            )
                            steps.append(step)
                            step_index += 1

        self.steps = steps
        return steps

    def start_execution(self) -> None:
        """Mark workflow as started."""
        self.state = WorkflowState.PREPARING
        self.start_time = datetime.now()
        self.current_step_index = 0
        self.images_acquired = 0

        if not self.steps:
            self.generate_steps()

    def mark_completed(self) -> None:
        """Mark workflow as completed."""
        self.state = WorkflowState.COMPLETED
        self.end_time = datetime.now()

    def mark_error(self, error: str) -> None:
        """Mark workflow as failed."""
        self.state = WorkflowState.ERROR
        self.end_time = datetime.now()
        self.error_message = error

    def get_progress(self) -> float:
        """Get workflow progress percentage.

        Returns:
            Progress from 0-100
        """
        if self.images_expected == 0:
            return 0.0
        return (self.images_acquired / self.images_expected) * 100.0

    def get_current_step(self) -> Optional[WorkflowStep]:
        """Get currently executing step.

        Returns:
            Current step or None
        """
        if 0 <= self.current_step_index < len(self.steps):
            return self.steps[self.current_step_index]
        return None

    def to_workflow_dict(self) -> Dict[str, Any]:
        """Convert to legacy workflow dictionary format.

        Returns:
            Dictionary for microscope communication
        """
        workflow = {}

        # Position information
        workflow["Start Position"] = {
            "X (mm)": self.start_position.x,
            "Y (mm)": self.start_position.y,
            "Z (mm)": self.start_position.z,
            "Angle (degrees)": self.start_position.r
        }

        end_pos = self.end_position or self.start_position
        workflow["End Position"] = {
            "X (mm)": end_pos.x,
            "Y (mm)": end_pos.y,
            "Z (mm)": end_pos.z,
            "Angle (degrees)": end_pos.r
        }

        # Illumination
        workflow["Illumination Source"] = self.illumination.to_dict()

        # Stack settings
        if self.stack_settings:
            workflow["Stack Settings"] = {
                "Number of planes": self.stack_settings.num_planes,
                "Change in Z axis (mm)": self.stack_settings.z_step_um / 1000.0,
                "Z stage velocity (mm/s)": str(self.stack_settings.z_velocity_mm_s),
                "Bidirectional": str(self.stack_settings.bidirectional).lower()
            }
        else:
            # Default for compatibility
            workflow["Stack Settings"] = {
                "Number of planes": 1,
                "Change in Z axis (mm)": 0.01,
                "Z stage velocity (mm/s)": "0.4",
                "Bidirectional": "false"
            }

        # Experiment settings
        save_format = "Tiff" if self.experiment_settings.save_data else "NotSaved"
        workflow["Experiment Settings"] = {
            "Comments": self.experiment_settings.comment,
            "Save image directory": str(self.experiment_settings.save_directory),
            "Save image data": save_format,
            "Display max projection": str(self.experiment_settings.max_projection_display).lower(),
            "Work flow live view enabled": str(self.experiment_settings.display_during_acquisition).lower()
        }

        return workflow

    @classmethod
    def create_snapshot(cls, position: Position,
                       laser_channel: str = "Laser 3 488 nm",
                       laser_power: float = 5.0) -> 'Workflow':
        """Create a simple snapshot workflow.

        Args:
            position: Position for snapshot
            laser_channel: Laser to use
            laser_power: Laser power in mW

        Returns:
            Configured snapshot workflow
        """
        return cls(
            workflow_type=WorkflowType.SNAPSHOT,
            name="Snapshot",
            start_position=position,
            illumination=IlluminationSettings(
                laser_channel=laser_channel,
                laser_power_mw=laser_power,
                laser_enabled=True
            ),
            experiment_settings=ExperimentSettings(
                save_data=False,
                comment="GUI Snapshot"
            )
        )