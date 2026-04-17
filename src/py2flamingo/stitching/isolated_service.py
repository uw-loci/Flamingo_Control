"""Service for running preprocessing in an isolated Python environment.

Flat-field correction (basicpy) and dual-illumination fusion (leonardo-toolset)
have dependency conflicts with the main application (basicpy needs scipy<1.13,
our app needs scipy>=1.14; leonardo pulls jax+torch+open3d).

This module manages an isolated Python venv and communicates with it via
subprocess + multiprocessing.shared_memory for zero-copy array transfer.

The isolated venv lives at:
    Windows: %APPDATA%/Flamingo/preprocessing_env
    Linux:   ~/.flamingo/preprocessing_env

Setup via the 'Setup Preprocessing...' button in the stitching dialog,
which runs scripts/create_preprocessing_env.bat (or .sh).
"""

import json
import logging
import subprocess
import sys
import uuid
from dataclasses import dataclass
from multiprocessing.shared_memory import SharedMemory
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Load timeout defaults from stitching config
try:
    from py2flamingo.configs.config_loader import get_stitching_value as _get_sv

    _IMPORT_CHECK_TIMEOUT = int(
        _get_sv("isolated_timeouts", "import_check", default=15)
    )
    _FLAT_FIELD_TIMEOUT = int(
        _get_sv("isolated_timeouts", "flat_field_estimation", default=300)
    )
    _LEONARDO_TIMEOUT = int(
        _get_sv("isolated_timeouts", "leonardo_fusion", default=1200)
    )
except Exception:
    _IMPORT_CHECK_TIMEOUT = 15
    _FLAT_FIELD_TIMEOUT = 300
    _LEONARDO_TIMEOUT = 1200


# ---------------------------------------------------------------------------
# SharedArray — numpy array backed by named shared memory
# ---------------------------------------------------------------------------
@dataclass
class SharedArray:
    """Numpy array backed by a named shared memory block.

    Used to pass large arrays between the main process and the isolated
    worker subprocess without serialization — both sides attach to the
    same OS-level shared memory by name.
    """

    name: str
    shape: tuple
    dtype: str
    shm: SharedMemory

    @classmethod
    def from_array(cls, arr: np.ndarray, prefix: str = "flamingo_") -> "SharedArray":
        """Create a SharedArray by copying *arr* into new shared memory."""
        name = f"{prefix}{uuid.uuid4().hex[:8]}"
        shm = SharedMemory(create=True, size=arr.nbytes, name=name)
        view = np.ndarray(arr.shape, dtype=arr.dtype, buffer=shm.buf)
        view[:] = arr  # single memcpy
        return cls(name=name, shape=arr.shape, dtype=str(arr.dtype), shm=shm)

    @classmethod
    def attach(cls, name: str, shape: tuple, dtype: str) -> "SharedArray":
        """Attach to existing shared memory by name."""
        shm = SharedMemory(name=name)
        return cls(name=name, shape=tuple(shape), dtype=dtype, shm=shm)

    def to_array(self) -> np.ndarray:
        """Copy data out of shared memory into a regular numpy array."""
        view = np.ndarray(self.shape, dtype=self.dtype, buffer=self.shm.buf)
        return np.array(view)

    def to_json(self) -> dict:
        """Serialize metadata (not data) for passing to worker."""
        return {"name": self.name, "shape": list(self.shape), "dtype": self.dtype}

    def close(self, unlink: bool = True):
        """Close and optionally unlink the shared memory."""
        self.shm.close()
        if unlink:
            try:
                self.shm.unlink()
            except (FileNotFoundError, PermissionError, OSError):
                pass  # Windows auto-cleans when all handles close


# ---------------------------------------------------------------------------
# IsolatedPreprocessingService
# ---------------------------------------------------------------------------
class IsolatedPreprocessingService:
    """Manages an isolated Python environment for basicpy/leonardo-toolset.

    Provides high-level methods that transparently handle:
    - Locating the isolated venv's Python executable
    - Copying input arrays into shared memory
    - Launching the worker subprocess
    - Passing task spec via stdin (JSON)
    - Reading results from output shared memory
    - Cleanup of all shared memory blocks
    """

    # Default venv locations
    _WINDOWS_ENV = (
        Path.home() / "AppData" / "Roaming" / "Flamingo" / "preprocessing_env"
    )
    _LINUX_ENV = Path.home() / ".flamingo" / "preprocessing_env"

    def __init__(self):
        self._worker_python = self._find_worker_python()
        # Cache import check results (cleared on env setup)
        self._import_cache: Dict[str, bool] = {}

    @classmethod
    def env_path(cls) -> Path:
        """Return the expected environment path for the current platform."""
        if sys.platform == "win32":
            return cls._WINDOWS_ENV
        return cls._LINUX_ENV

    def _find_worker_python(self) -> Optional[Path]:
        """Locate the isolated venv's Python executable."""
        if sys.platform == "win32":
            p = self._WINDOWS_ENV / "Scripts" / "python.exe"
        else:
            p = self._LINUX_ENV / "bin" / "python"
        return p if p.is_file() else None

    def is_available(self) -> bool:
        """Whether the isolated environment exists and has a Python executable."""
        return self._worker_python is not None

    def has_basicpy(self) -> bool:
        """Check if basicpy is installed in the isolated env."""
        return self._check_import("basicpy")

    def has_leonardo(self) -> bool:
        """Check if leonardo-toolset is installed in the isolated env."""
        return self._check_import("leonardo_toolset")

    def _check_import(self, module: str) -> bool:
        """Test whether *module* is importable in the isolated env."""
        if not self.is_available():
            return False
        if module in self._import_cache:
            return self._import_cache[module]
        try:
            result = subprocess.run(
                [str(self._worker_python), "-c", f"import {module}"],
                capture_output=True,
                timeout=_IMPORT_CHECK_TIMEOUT,
            )
            available = result.returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            available = False
        self._import_cache[module] = available
        return available

    def clear_cache(self):
        """Clear cached import check results (call after env setup)."""
        self._import_cache.clear()
        self._worker_python = self._find_worker_python()

    # ------------------------------------------------------------------
    # Low-level worker communication
    # ------------------------------------------------------------------

    def _run_worker(
        self,
        task: str,
        inputs: Dict[str, SharedArray],
        params: Optional[dict] = None,
        timeout: float = 600,
    ) -> dict:
        """Launch worker subprocess and communicate via SharedMemory + JSON.

        Args:
            task: Task name (e.g. "flat_field_estimate", "leonardo_fuse")
            inputs: Named SharedArray objects the worker should read
            params: Extra parameters passed as JSON
            timeout: Max seconds to wait for worker completion

        Returns:
            Worker response dict with "status", "outputs", "elapsed" keys.

        Raises:
            RuntimeError: If worker exits with non-zero code
            TimeoutError: If worker exceeds timeout
        """
        if not self.is_available():
            raise RuntimeError("Isolated preprocessing environment not found")

        task_spec = {
            "task": task,
            "inputs": {k: v.to_json() for k, v in inputs.items()},
            "params": params or {},
        }

        cmd = [str(self._worker_python), "-m", "py2flamingo.stitching.isolated_worker"]

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdout, stderr = proc.communicate(
                input=json.dumps(task_spec), timeout=timeout
            )
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise TimeoutError(f"Worker timed out after {timeout}s on task '{task}'")

        if proc.returncode != 0:
            raise RuntimeError(
                f"Isolated worker failed (exit {proc.returncode}):\n{stderr}"
            )

        try:
            return json.loads(stdout)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"Worker returned invalid JSON: {e}\nstdout: {stdout}\nstderr: {stderr}"
            )

    def _collect_output(self, result: dict, key: str) -> np.ndarray:
        """Read an output array from worker result and cleanup its shared memory."""
        info = result["outputs"][key]
        sa = SharedArray.attach(info["name"], tuple(info["shape"]), info["dtype"])
        arr = sa.to_array()
        sa.close(unlink=True)
        return arr

    # ------------------------------------------------------------------
    # High-level API: flat-field correction
    # ------------------------------------------------------------------

    def flat_field_estimate(
        self,
        sample_planes_per_channel: Dict[int, np.ndarray],
    ) -> Dict[int, Tuple[np.ndarray, np.ndarray]]:
        """Estimate flat-field and dark-field profiles via basicpy.

        Args:
            sample_planes_per_channel: {ch_id: (N_tiles, H, W) uint16 stack}

        Returns:
            {ch_id: (flatfield, darkfield)} — both float32 (H, W)
        """
        models = {}
        for ch_id, stack in sample_planes_per_channel.items():
            logger.info(
                f"  Estimating flat-field for channel {ch_id} "
                f"({stack.shape[0]} tiles) via isolated env..."
            )
            shm_in = SharedArray.from_array(stack)
            try:
                result = self._run_worker(
                    "flat_field_estimate",
                    {"stack": shm_in},
                    params={"channel_id": ch_id},
                    timeout=_FLAT_FIELD_TIMEOUT,
                )
                flatfield = self._collect_output(result, "flatfield")
                darkfield = self._collect_output(result, "darkfield")
                models[ch_id] = (flatfield, darkfield)
                logger.info(
                    f"  Channel {ch_id}: flat-field estimated "
                    f"(range {flatfield.min():.3f} – {flatfield.max():.3f})"
                )
            except Exception as e:
                logger.error(f"  Channel {ch_id}: flat-field estimation failed: {e}")
            finally:
                shm_in.close(unlink=True)

        return models

    def flat_field_apply(
        self,
        volume: np.ndarray,
        flatfield: np.ndarray,
        darkfield: np.ndarray,
    ) -> np.ndarray:
        """Apply flat-field correction to a volume.

        Args:
            volume: (Z, Y, X) uint16 volume
            flatfield: (Y, X) float32 flat-field profile
            darkfield: (Y, X) float32 dark-field profile

        Returns:
            Corrected (Z, Y, X) uint16 volume
        """
        # Flat-field application is simple enough to do in-process
        # (no dependency on basicpy — just division and subtraction)
        flatfield = flatfield.astype(np.float32)
        darkfield = darkfield.astype(np.float32)
        try:
            from py2flamingo.configs.config_loader import get_stitching_value

            _ff_thresh = float(
                get_stitching_value("flat_field", "min_threshold", default=0.001)
            )
        except Exception:
            _ff_thresh = 0.001
        flatfield = np.where(flatfield > _ff_thresh, flatfield, 1.0)

        result = np.empty_like(volume)
        for z in range(volume.shape[0]):
            corrected = (volume[z].astype(np.float32) - darkfield) / flatfield
            result[z] = np.clip(corrected, 0, 65535).astype(np.uint16)
        return result

    # ------------------------------------------------------------------
    # High-level API: Leonardo dual-illumination fusion
    # ------------------------------------------------------------------

    def fuse_illumination_leonardo(
        self,
        left: np.ndarray,
        right: np.ndarray,
    ) -> np.ndarray:
        """Run FUSE_illu dual-illumination fusion via isolated env.

        Args:
            left: (Z, Y, X) uint16 — left illumination volume
            right: (Z, Y, X) uint16 — right illumination volume

        Returns:
            Fused (Z, Y, X) uint16 volume
        """
        logger.info(
            f"Running Leonardo FUSE_illu via isolated env "
            f"(volume shape {left.shape})..."
        )
        shm_left = SharedArray.from_array(left)
        shm_right = SharedArray.from_array(right)
        try:
            result = self._run_worker(
                "leonardo_fuse",
                {"left": shm_left, "right": shm_right},
                timeout=_LEONARDO_TIMEOUT,  # Leonardo can be slow on large volumes
            )
            fused = self._collect_output(result, "fused")
            logger.info(
                f"Leonardo fusion complete in {result.get('elapsed', '?'):.1f}s"
            )
            return fused
        finally:
            shm_left.close(unlink=True)
            shm_right.close(unlink=True)
