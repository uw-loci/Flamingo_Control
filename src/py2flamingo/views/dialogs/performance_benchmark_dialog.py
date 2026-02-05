"""Performance Benchmark Dialog.

Dialog for running and displaying performance benchmarks on the 3D visualization pipeline.
Tests Gaussian smoothing, rotation transforms, translation shifts, and full pipeline timing.
"""

import logging
import time
import json
import csv
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QGroupBox, QGridLayout, QProgressBar, QTableWidget, QTableWidgetItem,
    QSpinBox, QCheckBox, QFileDialog, QMessageBox, QHeaderView
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QIcon

from py2flamingo.services.window_geometry_manager import PersistentDialog
from py2flamingo.resources import get_app_icon

logger = logging.getLogger(__name__)


@dataclass
class BenchmarkResult:
    """Result from a single benchmark test."""
    test_name: str
    volume_size: str
    iterations: int
    mean_time_ms: float
    std_time_ms: float
    min_time_ms: float
    max_time_ms: float
    throughput_voxels_per_sec: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class BenchmarkWorker(QThread):
    """Worker thread for running benchmarks without blocking the GUI."""

    progress = pyqtSignal(int, str)  # (percentage, status_message)
    result_ready = pyqtSignal(object)  # BenchmarkResult
    finished_all = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, tests: List[str], volume_size: int, iterations: int,
                 voxel_storage=None, quality: int = 0, parent=None):
        """Initialize benchmark worker.

        Args:
            tests: List of test names to run
            volume_size: Size of test volume (e.g., 200 for 200³)
            iterations: Number of iterations per test
            voxel_storage: Optional storage instance
            quality: Interpolation order (0=nearest-neighbor/fast, 1=linear/quality)
            parent: Parent QObject
        """
        super().__init__(parent)
        self.tests = tests
        self.volume_size = volume_size
        self.iterations = iterations
        self.voxel_storage = voxel_storage
        self.quality = quality  # 0=fast (nearest-neighbor), 1=quality (linear)
        self._cancelled = False

    def cancel(self):
        """Cancel the benchmark run."""
        self._cancelled = True

    def run(self):
        """Run all selected benchmarks."""
        try:
            # Import scipy here to avoid import issues if not available
            from scipy import ndimage

            total_tests = len(self.tests)

            for test_idx, test_name in enumerate(self.tests):
                if self._cancelled:
                    break

                base_progress = int((test_idx / total_tests) * 100)
                self.progress.emit(base_progress, f"Running {test_name}...")

                result = self._run_single_test(test_name, ndimage)
                if result:
                    self.result_ready.emit(result)

            self.finished_all.emit()

        except Exception as e:
            logger.exception("Benchmark error")
            self.error.emit(str(e))

    def _run_single_test(self, test_name: str, ndimage) -> Optional[BenchmarkResult]:
        """Run a single benchmark test."""
        # Create test volume
        size = self.volume_size
        volume = np.random.randint(0, 65535, (size, size, size), dtype=np.uint16)
        total_voxels = size ** 3

        times = []

        for i in range(self.iterations):
            if self._cancelled:
                return None

            start = time.perf_counter()

            if test_name == "Gaussian Smoothing":
                sigma = (1.0, 1.0, 1.0)
                _ = ndimage.gaussian_filter(volume, sigma)

            elif test_name == "Rotation 15°":
                _ = self._run_rotation(volume, 15.0, ndimage)

            elif test_name == "Rotation 45°":
                _ = self._run_rotation(volume, 45.0, ndimage)

            elif test_name == "Rotation 90°":
                _ = self._run_rotation(volume, 90.0, ndimage)

            elif test_name == "Translation Shift":
                offset = (10.0, 5.0, -3.0)
                _ = self._run_translation(volume, offset, ndimage)

            elif test_name == "Downsample 3x":
                _ = self._run_downsample(volume, 3)

            elif test_name == "Full Pipeline":
                # Simulate full pipeline: gaussian + rotation + translation
                smoothed = ndimage.gaussian_filter(volume, (1.0, 1.0, 1.0))
                rotated = self._run_rotation(smoothed, 15.0, ndimage)
                _ = self._run_translation(rotated, (5.0, 5.0, 5.0), ndimage)

            elapsed = (time.perf_counter() - start) * 1000  # Convert to ms
            times.append(elapsed)

        times_array = np.array(times)
        mean_time = np.mean(times_array)

        # Calculate throughput
        throughput = total_voxels / (mean_time / 1000.0)  # voxels per second

        return BenchmarkResult(
            test_name=test_name,
            volume_size=f"{size}³",
            iterations=self.iterations,
            mean_time_ms=mean_time,
            std_time_ms=np.std(times_array),
            min_time_ms=np.min(times_array),
            max_time_ms=np.max(times_array),
            throughput_voxels_per_sec=throughput
        )

    def _run_rotation(self, volume: np.ndarray, angle: float, ndimage) -> np.ndarray:
        """Run rotation transform using affine_transform."""
        from scipy.spatial.transform import Rotation

        # Create rotation matrix around Y axis
        rot = Rotation.from_euler('y', angle, degrees=True)
        rot_matrix = rot.as_matrix()

        # Calculate center
        center = np.array(volume.shape) / 2

        # Create affine transform matrix (3x3 rotation + offset)
        offset = center - rot_matrix @ center

        # Apply affine transform with quality-appropriate interpolation
        # order=0: nearest-neighbor (fast), order=1: linear (quality)
        return ndimage.affine_transform(
            volume, rot_matrix, offset=offset, order=self.quality, mode='constant', cval=0
        )

    def _run_translation(self, volume: np.ndarray, offset: tuple, ndimage) -> np.ndarray:
        """Run translation with quality-appropriate method.

        For fast mode (order=0), uses numpy.roll which is ~10x faster.
        For quality mode (order=1), uses scipy.ndimage.shift with linear interpolation.
        """
        if self.quality == 0:
            # Fast mode: use numpy.roll for integer shifts
            offset_array = np.array(offset)
            int_offset = np.round(offset_array).astype(int)

            result = volume.copy()
            for axis, shift_val in enumerate(int_offset):
                if shift_val != 0:
                    result = np.roll(result, shift_val, axis=axis)
                    # Zero out wrapped values
                    if shift_val > 0:
                        slices = [slice(None)] * 3
                        slices[axis] = slice(0, shift_val)
                        result[tuple(slices)] = 0
                    elif shift_val < 0:
                        slices = [slice(None)] * 3
                        slices[axis] = slice(shift_val, None)
                        result[tuple(slices)] = 0
            return result
        else:
            # Quality mode: use scipy.ndimage.shift with linear interpolation
            return ndimage.shift(volume, offset, order=1, mode='constant', cval=0)

    def _run_downsample(self, volume: np.ndarray, factor: int) -> np.ndarray:
        """Downsample volume by block averaging."""
        shape = volume.shape
        new_shape = tuple(s // factor for s in shape)

        # Trim to exact multiple
        trimmed = volume[:new_shape[0]*factor, :new_shape[1]*factor, :new_shape[2]*factor]

        # Reshape and average
        reshaped = trimmed.reshape(
            new_shape[0], factor,
            new_shape[1], factor,
            new_shape[2], factor
        )
        return reshaped.mean(axis=(1, 3, 5)).astype(volume.dtype)


class PerformanceBenchmarkDialog(PersistentDialog):
    """Dialog for running performance benchmarks on the 3D visualization pipeline.

    Features:
    - Configurable volume sizes (100³, 200³, 300³, or current data)
    - Multiple test types (Gaussian, Rotation, Translation, Downsample, Full Pipeline)
    - Configurable iterations for statistical accuracy
    - Results table with timing statistics
    - Export to CSV/JSON
    """

    # Available benchmark tests
    AVAILABLE_TESTS = [
        "Gaussian Smoothing",
        "Rotation 15°",
        "Rotation 45°",
        "Rotation 90°",
        "Translation Shift",
        "Downsample 3x",
        "Full Pipeline"
    ]

    # Volume size presets
    VOLUME_SIZES = {
        "Small (100³)": 100,
        "Medium (200³)": 200,
        "Large (300³)": 300,
    }

    def __init__(self, voxel_storage=None, parent=None):
        """Initialize the benchmark dialog.

        Args:
            voxel_storage: Optional DualResolutionVoxelStorage instance for testing with real data
            parent: Parent widget
        """
        super().__init__(parent)
        self._logger = logging.getLogger(__name__)
        self.voxel_storage = voxel_storage
        self.results: List[BenchmarkResult] = []
        self.worker: Optional[BenchmarkWorker] = None

        self.setWindowTitle("Performance Benchmark")
        self.setWindowIcon(get_app_icon())  # Use flamingo icon
        self.setMinimumWidth(700)
        self.setMinimumHeight(500)

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Create and layout UI components."""
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Configuration section
        config_group = QGroupBox("Benchmark Configuration")
        config_layout = QGridLayout()
        config_layout.setSpacing(8)

        # Volume size selection
        config_layout.addWidget(QLabel("Volume Size:"), 0, 0)
        self._volume_combo = QComboBox()
        for name in self.VOLUME_SIZES.keys():
            self._volume_combo.addItem(name)
        self._volume_combo.setCurrentIndex(1)  # Default to Medium
        config_layout.addWidget(self._volume_combo, 0, 1)

        # Iterations
        config_layout.addWidget(QLabel("Iterations:"), 0, 2)
        self._iterations_spin = QSpinBox()
        self._iterations_spin.setRange(1, 100)
        self._iterations_spin.setValue(5)
        self._iterations_spin.setToolTip("Number of times to run each test for averaging")
        config_layout.addWidget(self._iterations_spin, 0, 3)

        # Quality mode selection (Row 1)
        config_layout.addWidget(QLabel("Interpolation:"), 1, 0)
        self._quality_combo = QComboBox()
        self._quality_combo.addItem("Fast (Nearest-Neighbor)", 0)
        self._quality_combo.addItem("Quality (Linear)", 1)
        self._quality_combo.setCurrentIndex(0)  # Default to Fast
        self._quality_combo.setToolTip("FAST: ~3-5x faster, blocky appearance\nQUALITY: smoother results, slower")
        config_layout.addWidget(self._quality_combo, 1, 1)

        # Compare both checkbox
        self._compare_both_cb = QCheckBox("Compare Both Modes")
        self._compare_both_cb.setToolTip("Run benchmarks in both FAST and QUALITY modes for comparison")
        config_layout.addWidget(self._compare_both_cb, 1, 2, 1, 2)

        config_group.setLayout(config_layout)
        layout.addWidget(config_group)

        # Test selection
        tests_group = QGroupBox("Tests to Run")
        tests_layout = QGridLayout()
        tests_layout.setSpacing(4)

        self._test_checkboxes = {}
        row, col = 0, 0
        for test_name in self.AVAILABLE_TESTS:
            cb = QCheckBox(test_name)
            cb.setChecked(True)
            self._test_checkboxes[test_name] = cb
            tests_layout.addWidget(cb, row, col)
            col += 1
            if col > 2:
                col = 0
                row += 1

        # Select all / none buttons
        btn_layout = QHBoxLayout()
        select_all_btn = QPushButton("Select All")
        select_all_btn.clicked.connect(self._select_all_tests)
        btn_layout.addWidget(select_all_btn)

        select_none_btn = QPushButton("Select None")
        select_none_btn.clicked.connect(self._select_no_tests)
        btn_layout.addWidget(select_none_btn)
        btn_layout.addStretch()

        tests_layout.addLayout(btn_layout, row + 1, 0, 1, 3)
        tests_group.setLayout(tests_layout)
        layout.addWidget(tests_group)

        # Progress section
        progress_layout = QHBoxLayout()
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        progress_layout.addWidget(self._progress_bar)

        self._status_label = QLabel("Ready")
        self._status_label.setMinimumWidth(200)
        progress_layout.addWidget(self._status_label)
        layout.addLayout(progress_layout)

        # Results table
        results_group = QGroupBox("Results")
        results_layout = QVBoxLayout()

        self._results_table = QTableWidget()
        self._results_table.setColumnCount(7)
        self._results_table.setHorizontalHeaderLabels([
            "Test", "Volume", "Mean (ms)", "Std (ms)",
            "Min (ms)", "Max (ms)", "Throughput (Mvox/s)"
        ])
        self._results_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._results_table.setAlternatingRowColors(True)
        results_layout.addWidget(self._results_table)

        results_group.setLayout(results_layout)
        layout.addWidget(results_group)

        # Control buttons
        button_layout = QHBoxLayout()

        self._run_btn = QPushButton("Run Benchmarks")
        self._run_btn.setStyleSheet(
            "QPushButton { background-color: #4CAF50; color: white; font-weight: bold; padding: 8px 16px; }"
            "QPushButton:hover { background-color: #45a049; }"
            "QPushButton:disabled { background-color: #888; }"
        )
        self._run_btn.clicked.connect(self._on_run_clicked)
        button_layout.addWidget(self._run_btn)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)
        button_layout.addWidget(self._cancel_btn)

        button_layout.addStretch()

        self._export_csv_btn = QPushButton("Export CSV")
        self._export_csv_btn.setEnabled(False)
        self._export_csv_btn.clicked.connect(self._on_export_csv)
        button_layout.addWidget(self._export_csv_btn)

        self._export_json_btn = QPushButton("Export JSON")
        self._export_json_btn.setEnabled(False)
        self._export_json_btn.clicked.connect(self._on_export_json)
        button_layout.addWidget(self._export_json_btn)

        self._clear_btn = QPushButton("Clear Results")
        self._clear_btn.clicked.connect(self._on_clear_results)
        button_layout.addWidget(self._clear_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

    def _select_all_tests(self):
        """Select all test checkboxes."""
        for cb in self._test_checkboxes.values():
            cb.setChecked(True)

    def _select_no_tests(self):
        """Deselect all test checkboxes."""
        for cb in self._test_checkboxes.values():
            cb.setChecked(False)

    def _on_run_clicked(self):
        """Start running benchmarks."""
        # Get selected tests
        selected_tests = [
            name for name, cb in self._test_checkboxes.items()
            if cb.isChecked()
        ]

        if not selected_tests:
            QMessageBox.warning(self, "No Tests Selected",
                              "Please select at least one test to run.")
            return

        # Get volume size
        size_name = self._volume_combo.currentText()
        volume_size = self.VOLUME_SIZES.get(size_name, 200)

        # Get iterations
        iterations = self._iterations_spin.value()

        # Get quality mode
        quality = self._quality_combo.currentData()
        compare_both = self._compare_both_cb.isChecked()

        # Disable controls
        self._run_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._volume_combo.setEnabled(False)
        self._iterations_spin.setEnabled(False)
        self._quality_combo.setEnabled(False)
        self._compare_both_cb.setEnabled(False)
        for cb in self._test_checkboxes.values():
            cb.setEnabled(False)

        # If comparing both modes, we'll run twice
        if compare_both:
            # Modify test names to include quality mode
            fast_tests = [f"{t} [FAST]" for t in selected_tests]
            quality_tests = [f"{t} [QUALITY]" for t in selected_tests]
            all_tests = fast_tests + quality_tests
            # Store quality mapping for the worker to use
            self._pending_quality_runs = [(0, fast_tests), (1, quality_tests)]
            self._current_quality_run_idx = 0
            self._run_next_quality_benchmark(selected_tests, volume_size, iterations)
        else:
            # Single quality mode run
            self._pending_quality_runs = None
            self.worker = BenchmarkWorker(
                tests=selected_tests,
                volume_size=volume_size,
                iterations=iterations,
                voxel_storage=self.voxel_storage,
                quality=quality,
                parent=self
            )
            self.worker.progress.connect(self._on_progress)
            self.worker.result_ready.connect(self._on_result_ready)
            self.worker.finished_all.connect(self._on_finished)
            self.worker.error.connect(self._on_error)
            self.worker.start()

    def _run_next_quality_benchmark(self, tests: List[str], volume_size: int, iterations: int):
        """Run the next quality benchmark when comparing both modes."""
        if self._current_quality_run_idx >= len(self._pending_quality_runs):
            self._on_finished()
            return

        quality, labeled_tests = self._pending_quality_runs[self._current_quality_run_idx]
        quality_name = "FAST" if quality == 0 else "QUALITY"

        self._status_label.setText(f"Running {quality_name} mode benchmarks...")

        self.worker = BenchmarkWorker(
            tests=tests,  # Use original test names
            volume_size=volume_size,
            iterations=iterations,
            voxel_storage=self.voxel_storage,
            quality=quality,
            parent=self
        )
        # Store info for labeling results
        self.worker._quality_suffix = f" [{quality_name}]"

        self.worker.progress.connect(self._on_progress)
        self.worker.result_ready.connect(self._on_result_ready_with_suffix)
        self.worker.finished_all.connect(lambda: self._on_quality_run_finished(tests, volume_size, iterations))
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _on_result_ready_with_suffix(self, result: BenchmarkResult):
        """Handle result with quality suffix added to test name."""
        if hasattr(self.worker, '_quality_suffix'):
            # Create new result with suffixed name
            result = BenchmarkResult(
                test_name=result.test_name + self.worker._quality_suffix,
                volume_size=result.volume_size,
                iterations=result.iterations,
                mean_time_ms=result.mean_time_ms,
                std_time_ms=result.std_time_ms,
                min_time_ms=result.min_time_ms,
                max_time_ms=result.max_time_ms,
                throughput_voxels_per_sec=result.throughput_voxels_per_sec
            )
        self.results.append(result)
        self._add_result_to_table(result)

    def _on_quality_run_finished(self, tests: List[str], volume_size: int, iterations: int):
        """Handle completion of one quality run when comparing both."""
        self._current_quality_run_idx += 1
        self._run_next_quality_benchmark(tests, volume_size, iterations)

    def _on_cancel_clicked(self):
        """Cancel running benchmarks."""
        if self.worker:
            self.worker.cancel()
            self._status_label.setText("Cancelling...")

    def _on_progress(self, percentage: int, status: str):
        """Handle progress updates."""
        self._progress_bar.setValue(percentage)
        self._status_label.setText(status)

    def _on_result_ready(self, result: BenchmarkResult):
        """Handle a single benchmark result."""
        self.results.append(result)
        self._add_result_to_table(result)

    def _add_result_to_table(self, result: BenchmarkResult):
        """Add a result row to the table."""
        row = self._results_table.rowCount()
        self._results_table.insertRow(row)

        # Format throughput as Mvox/s
        throughput_mvox = result.throughput_voxels_per_sec / 1e6

        items = [
            result.test_name,
            result.volume_size,
            f"{result.mean_time_ms:.1f}",
            f"{result.std_time_ms:.1f}",
            f"{result.min_time_ms:.1f}",
            f"{result.max_time_ms:.1f}",
            f"{throughput_mvox:.1f}"
        ]

        for col, text in enumerate(items):
            item = QTableWidgetItem(str(text))
            item.setTextAlignment(Qt.AlignCenter)
            self._results_table.setItem(row, col, item)

    def _on_finished(self):
        """Handle benchmark completion."""
        self._progress_bar.setValue(100)
        self._status_label.setText("Complete")
        self._restore_controls()

        # Enable export if we have results
        has_results = len(self.results) > 0
        self._export_csv_btn.setEnabled(has_results)
        self._export_json_btn.setEnabled(has_results)

    def _on_error(self, error_msg: str):
        """Handle benchmark error."""
        self._status_label.setText(f"Error: {error_msg}")
        self._restore_controls()
        QMessageBox.critical(self, "Benchmark Error", error_msg)

    def _restore_controls(self):
        """Re-enable controls after benchmark completion."""
        self._run_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._volume_combo.setEnabled(True)
        self._iterations_spin.setEnabled(True)
        self._quality_combo.setEnabled(True)
        self._compare_both_cb.setEnabled(True)
        for cb in self._test_checkboxes.values():
            cb.setEnabled(True)

    def _on_clear_results(self):
        """Clear all results."""
        self.results.clear()
        self._results_table.setRowCount(0)
        self._progress_bar.setValue(0)
        self._status_label.setText("Ready")
        self._export_csv_btn.setEnabled(False)
        self._export_json_btn.setEnabled(False)

    def _on_export_csv(self):
        """Export results to CSV file."""
        if not self.results:
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Benchmark Results",
            "benchmark_results.csv",
            "CSV Files (*.csv)"
        )

        if file_path:
            try:
                with open(file_path, 'w', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=self.results[0].to_dict().keys())
                    writer.writeheader()
                    for result in self.results:
                        writer.writerow(result.to_dict())

                self._logger.info(f"Exported benchmark results to {file_path}")
                QMessageBox.information(self, "Export Complete",
                                       f"Results exported to {file_path}")
            except Exception as e:
                self._logger.exception("Export failed")
                QMessageBox.critical(self, "Export Failed", str(e))

    def _on_export_json(self):
        """Export results to JSON file."""
        if not self.results:
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Benchmark Results",
            "benchmark_results.json",
            "JSON Files (*.json)"
        )

        if file_path:
            try:
                data = {
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "results": [result.to_dict() for result in self.results]
                }

                with open(file_path, 'w') as f:
                    json.dump(data, f, indent=2)

                self._logger.info(f"Exported benchmark results to {file_path}")
                QMessageBox.information(self, "Export Complete",
                                       f"Results exported to {file_path}")
            except Exception as e:
                self._logger.exception("Export failed")
                QMessageBox.critical(self, "Export Failed", str(e))

    def closeEvent(self, event):
        """Handle dialog close - cancel any running benchmark."""
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(1000)  # Wait up to 1 second
        super().closeEvent(event)
