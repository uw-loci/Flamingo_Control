# src/py2flamingo/services/acquisition_timing_service.py

"""
Service for tracking acquisition timing and learning correction factors.

This service records actual acquisition durations and uses regression
analysis to learn overhead correction factors for more accurate predictions.
"""

import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
import statistics

from py2flamingo.models.acquisition_timing import (
    AcquisitionTimingRecord,
    LearnedOverheadComponents,
    TimingHistory
)


class AcquisitionTimingService:
    """
    Service for tracking and predicting acquisition timing.

    Maintains a history of acquisition timings and uses regression
    to learn correction factors for more accurate predictions.
    """

    def __init__(self, timing_file: Optional[str] = None, max_records: int = 100):
        """
        Initialize acquisition timing service.

        Args:
            timing_file: Path to timing history JSON file. If None, uses default.
            max_records: Maximum number of records to keep per workflow type.
        """
        self.logger = logging.getLogger(__name__)

        if timing_file is None:
            settings_dir = Path("microscope_settings")
            settings_dir.mkdir(exist_ok=True)
            self.timing_file = settings_dir / "acquisition_timing_history.json"
        else:
            self.timing_file = Path(timing_file)

        self._max_records = max_records
        self._history: TimingHistory = TimingHistory(max_records=max_records)
        self._load_history()

    def _load_history(self) -> None:
        """Load timing history from JSON file."""
        try:
            if self.timing_file.exists():
                with open(self.timing_file, 'r') as f:
                    data = json.load(f)
                    self._history = TimingHistory.from_dict(data)
                self.logger.info(
                    f"Loaded {len(self._history.records)} timing records from {self.timing_file}"
                )
            else:
                self.logger.info(
                    f"No timing file found at {self.timing_file}, starting fresh"
                )
        except Exception as e:
            self.logger.error(f"Error loading timing history: {e}", exc_info=True)
            self._history = TimingHistory(max_records=self._max_records)

    def _save_history(self) -> None:
        """Save timing history to JSON file."""
        try:
            with open(self.timing_file, 'w') as f:
                json.dump(self._history.to_dict(), f, indent=2)
            self.logger.debug(f"Saved timing history to {self.timing_file}")
        except Exception as e:
            self.logger.error(f"Error saving timing history: {e}", exc_info=True)

    def record_acquisition(
        self,
        workflow_type: str,
        num_planes: int,
        num_lasers: int,
        z_velocity_mm_s: float,
        z_range_mm: float,
        total_z_travel_mm: float,
        exposure_us: float,
        theoretical_duration_s: float,
        actual_duration_s: float
    ) -> AcquisitionTimingRecord:
        """
        Record a completed acquisition's timing data.

        Args:
            workflow_type: Type of workflow (ZSTACK, etc.)
            num_planes: Number of planes acquired
            num_lasers: Number of lasers used
            z_velocity_mm_s: Z stage velocity
            z_range_mm: Z range covered
            total_z_travel_mm: Total Z travel including return
            exposure_us: Exposure time
            theoretical_duration_s: Predicted duration
            actual_duration_s: Actual measured duration

        Returns:
            The created timing record
        """
        record = AcquisitionTimingRecord.create(
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

        self._history.add_record(record)

        # Recalculate learned factors
        self._update_learned_factors()

        # Save to disk
        self._save_history()

        self.logger.info(
            f"Recorded acquisition timing: {workflow_type}, "
            f"theoretical={theoretical_duration_s:.2f}s, "
            f"actual={actual_duration_s:.2f}s, "
            f"overhead={record.overhead_s:.2f}s"
        )

        return record

    def get_corrected_estimate(
        self,
        theoretical_time: float,
        num_planes: int,
        num_lasers: int,
        total_z_travel_mm: float
    ) -> Tuple[float, int]:
        """
        Get corrected time estimate using learned factors.

        Args:
            theoretical_time: Base theoretical calculation
            num_planes: Number of planes to acquire
            num_lasers: Number of lasers (affects filter switches)
            total_z_travel_mm: Total Z travel including return

        Returns:
            Tuple of (corrected_time, sample_count)
        """
        if not self._history.learned_factors.is_valid():
            # Not enough data, return theoretical
            return (theoretical_time, 0)

        corrected = self._history.learned_factors.get_corrected_estimate(
            theoretical_time=theoretical_time,
            num_planes=num_planes,
            num_lasers=num_lasers,
            total_z_travel_mm=total_z_travel_mm
        )

        return (corrected, self._history.learned_factors.sample_count)

    def get_learned_factors(self) -> LearnedOverheadComponents:
        """Get current learned overhead factors."""
        return self._history.learned_factors

    def get_statistics(self, workflow_type: Optional[str] = None) -> Dict[str, Any]:
        """
        Get timing statistics.

        Args:
            workflow_type: Optional filter by workflow type

        Returns:
            Dictionary with statistics
        """
        if workflow_type:
            records = self._history.get_records_by_type(workflow_type)
        else:
            records = self._history.records

        if not records:
            return {
                'sample_count': 0,
                'mean_overhead_s': 0,
                'std_overhead_s': 0,
                'min_overhead_s': 0,
                'max_overhead_s': 0,
            }

        overheads = [r.overhead_s for r in records]

        return {
            'sample_count': len(records),
            'mean_overhead_s': statistics.mean(overheads),
            'std_overhead_s': statistics.stdev(overheads) if len(overheads) > 1 else 0,
            'min_overhead_s': min(overheads),
            'max_overhead_s': max(overheads),
        }

    def _update_learned_factors(self) -> None:
        """
        Update learned overhead factors using regression analysis.

        Uses ordinary least squares regression to fit:
        overhead = base + (filter_switch * num_switches) +
                   (settle_correction * num_planes) + (z_overhead * z_travel)
        """
        records = self._history.records

        if len(records) < 5:
            # Not enough data for reliable regression
            self.logger.debug(
                f"Only {len(records)} records, need at least 5 for regression"
            )
            return

        # Simple approach: use mean overhead per component
        # For a more sophisticated approach, use numpy for actual regression

        # Calculate average overhead per plane (settle correction)
        overhead_per_plane = []
        for r in records:
            if r.num_planes > 0:
                overhead_per_plane.append(r.overhead_s / r.num_planes)

        # Calculate average overhead per mm of Z travel
        overhead_per_mm = []
        for r in records:
            if r.total_z_travel_mm > 0:
                overhead_per_mm.append(r.overhead_s / r.total_z_travel_mm)

        # Calculate filter switch overhead
        # Group by number of lasers and compare
        single_laser = [r.overhead_s for r in records if r.num_lasers == 1]
        multi_laser = [r.overhead_s for r in records if r.num_lasers > 1]

        filter_switch_overhead = 0.0
        if single_laser and multi_laser:
            avg_single = statistics.mean(single_laser)
            avg_multi = statistics.mean(multi_laser)
            avg_switches = statistics.mean([r.num_lasers - 1 for r in records if r.num_lasers > 1])
            if avg_switches > 0:
                filter_switch_overhead = (avg_multi - avg_single) / avg_switches

        # Calculate base overhead (minimum observed overhead)
        min_overhead = min(r.overhead_s for r in records)
        base_overhead = max(0, min_overhead)  # Don't go negative

        # Update learned factors
        self._history.learned_factors = LearnedOverheadComponents(
            base_overhead_s=base_overhead,
            filter_switch_s=max(0, filter_switch_overhead),
            settle_correction_per_plane_s=statistics.mean(overhead_per_plane) if overhead_per_plane else 0,
            z_overhead_per_mm_s=statistics.mean(overhead_per_mm) if overhead_per_mm else 0,
            sample_count=len(records),
            last_updated=datetime.now().isoformat()
        )

        self.logger.info(
            f"Updated learned factors from {len(records)} samples: "
            f"base={self._history.learned_factors.base_overhead_s:.3f}s, "
            f"filter_switch={self._history.learned_factors.filter_switch_s:.3f}s, "
            f"settle_per_plane={self._history.learned_factors.settle_correction_per_plane_s:.4f}s, "
            f"z_per_mm={self._history.learned_factors.z_overhead_per_mm_s:.4f}s"
        )

    def clear_history(self) -> None:
        """Clear all timing history (for testing/reset)."""
        self._history = TimingHistory(max_records=self._max_records)
        self._save_history()
        self.logger.warning("Cleared all timing history")

    def get_record_count(self) -> int:
        """Get number of timing records."""
        return len(self._history.records)

    def get_recent_records(self, count: int = 10) -> List[AcquisitionTimingRecord]:
        """Get most recent timing records."""
        return self._history.records[:count]
