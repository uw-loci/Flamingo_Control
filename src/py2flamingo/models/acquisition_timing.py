# src/py2flamingo/models/acquisition_timing.py

"""
Data models for acquisition timing and adaptive time estimation.

These models track actual acquisition durations vs estimated durations
to learn correction factors for more accurate predictions.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Dict, Any, List, Optional


@dataclass
class AcquisitionTimingRecord:
    """
    Record of a single acquisition's timing data.

    Captures all relevant parameters and timing results for learning
    overhead correction factors.

    Attributes:
        timestamp: ISO format timestamp when acquisition completed
        workflow_type: Type of workflow (SNAPSHOT, ZSTACK, etc.)
        num_planes: Number of Z planes acquired
        num_lasers: Number of lasers used (for filter switch overhead)
        z_velocity_mm_s: Z stage velocity in mm/s
        z_range_mm: Total Z range in mm
        total_z_travel_mm: Total Z movement including return to start
        exposure_us: Exposure time in microseconds
        theoretical_duration_s: Calculated duration before acquisition
        actual_duration_s: Measured duration after acquisition
        overhead_s: Difference (actual - theoretical)
    """
    timestamp: str
    workflow_type: str
    num_planes: int
    num_lasers: int
    z_velocity_mm_s: float
    z_range_mm: float
    total_z_travel_mm: float
    exposure_us: float
    theoretical_duration_s: float
    actual_duration_s: float
    overhead_s: float = field(init=False)

    def __post_init__(self):
        """Calculate overhead after initialization."""
        self.overhead_s = self.actual_duration_s - self.theoretical_duration_s

    def to_dict(self) -> Dict[str, Any]:
        """Convert record to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AcquisitionTimingRecord':
        """Create record from dictionary (loaded from JSON)."""
        # Remove overhead_s if present since it's calculated
        data = {k: v for k, v in data.items() if k != 'overhead_s'}
        return cls(**data)

    @classmethod
    def create(
        cls,
        workflow_type: str,
        num_planes: int,
        num_lasers: int,
        z_velocity_mm_s: float,
        z_range_mm: float,
        total_z_travel_mm: float,
        exposure_us: float,
        theoretical_duration_s: float,
        actual_duration_s: float
    ) -> 'AcquisitionTimingRecord':
        """
        Create a new timing record with current timestamp.

        Args:
            workflow_type: Type of workflow
            num_planes: Number of planes acquired
            num_lasers: Number of lasers used
            z_velocity_mm_s: Z velocity
            z_range_mm: Z range
            total_z_travel_mm: Total Z travel including return
            exposure_us: Exposure time
            theoretical_duration_s: Predicted duration
            actual_duration_s: Actual measured duration

        Returns:
            New AcquisitionTimingRecord
        """
        return cls(
            timestamp=datetime.now().isoformat(),
            workflow_type=workflow_type,
            num_planes=num_planes,
            num_lasers=num_lasers,
            z_velocity_mm_s=z_velocity_mm_s,
            z_range_mm=z_range_mm,
            total_z_travel_mm=total_z_travel_mm,
            exposure_us=exposure_us,
            theoretical_duration_s=theoretical_duration_s,
            actual_duration_s=actual_duration_s
        )


@dataclass
class LearnedOverheadComponents:
    """
    Learned overhead factors from regression analysis.

    These factors are used to correct theoretical estimates
    based on historical acquisition data.

    Attributes:
        base_overhead_s: Constant overhead per acquisition (init, finalize)
        filter_switch_s: Time per filter/laser switch
        settle_correction_per_plane_s: Correction to theoretical settle time
        z_overhead_per_mm_s: Extra time per mm of Z travel (accel/decel)
        sample_count: Number of samples used to calculate these factors
        last_updated: ISO timestamp of last update
    """
    base_overhead_s: float = 0.0
    filter_switch_s: float = 0.0
    settle_correction_per_plane_s: float = 0.0
    z_overhead_per_mm_s: float = 0.0
    sample_count: int = 0
    last_updated: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'LearnedOverheadComponents':
        """Create from dictionary (loaded from JSON)."""
        return cls(**data)

    def get_corrected_estimate(
        self,
        theoretical_time: float,
        num_planes: int,
        num_lasers: int,
        total_z_travel_mm: float
    ) -> float:
        """
        Calculate corrected time estimate using learned factors.

        Args:
            theoretical_time: Base theoretical calculation
            num_planes: Number of planes to acquire
            num_lasers: Number of lasers (filter switches = lasers - 1)
            total_z_travel_mm: Total Z travel including return

        Returns:
            Corrected time estimate in seconds
        """
        filter_switches = max(0, num_lasers - 1)

        correction = (
            self.base_overhead_s +
            self.filter_switch_s * filter_switches +
            self.settle_correction_per_plane_s * num_planes +
            self.z_overhead_per_mm_s * total_z_travel_mm
        )

        return theoretical_time + correction

    def is_valid(self) -> bool:
        """Check if learned factors have enough samples to be reliable."""
        return self.sample_count >= 5  # Minimum samples for reliability


@dataclass
class TimingHistory:
    """
    Container for timing history with learned components.

    Stores recent acquisition records and computed overhead factors.

    Attributes:
        records: List of timing records (most recent first)
        learned_factors: Computed overhead correction factors
        max_records: Maximum number of records to keep
    """
    records: List[AcquisitionTimingRecord] = field(default_factory=list)
    learned_factors: LearnedOverheadComponents = field(default_factory=LearnedOverheadComponents)
    max_records: int = 100

    def add_record(self, record: AcquisitionTimingRecord) -> None:
        """
        Add a new timing record and trim old records.

        Args:
            record: New timing record to add
        """
        self.records.insert(0, record)  # Most recent first

        # Trim old records
        if len(self.records) > self.max_records:
            self.records = self.records[:self.max_records]

    def get_records_by_type(self, workflow_type: str) -> List[AcquisitionTimingRecord]:
        """Get records filtered by workflow type."""
        return [r for r in self.records if r.workflow_type == workflow_type]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            'records': [r.to_dict() for r in self.records],
            'learned_factors': self.learned_factors.to_dict(),
            'max_records': self.max_records,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TimingHistory':
        """Create from dictionary (loaded from JSON)."""
        records = [
            AcquisitionTimingRecord.from_dict(r)
            for r in data.get('records', [])
        ]
        learned_factors = LearnedOverheadComponents.from_dict(
            data.get('learned_factors', {})
        )
        return cls(
            records=records,
            learned_factors=learned_factors,
            max_records=data.get('max_records', 100)
        )
