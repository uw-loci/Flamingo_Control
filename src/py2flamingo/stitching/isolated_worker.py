"""Isolated preprocessing worker — runs in separate Python environment.

This module is invoked as a subprocess by IsolatedPreprocessingService:

    worker_python -m py2flamingo.stitching.isolated_worker

Communication protocol:
    - Receives a JSON task spec on stdin
    - Attaches to named shared memory blocks for input arrays
    - Runs the requested task (flat_field_estimate, flat_field_apply, leonardo_fuse)
    - Writes output arrays to new shared memory blocks
    - Prints a JSON response to stdout

The main process owns and cleans up input shared memory.
Output shared memory is created here but cleaned up by the main process
after reading the results.
"""

import json
import sys
import time
import uuid
from multiprocessing.shared_memory import SharedMemory

import numpy as np


def _attach_arrays(inputs: dict) -> tuple:
    """Attach to shared memory blocks described in the inputs dict.

    Returns (arrays_dict, shm_handles_list).
    """
    arrays = {}
    handles = []
    for key, info in inputs.items():
        shm = SharedMemory(name=info["name"])
        handles.append(shm)
        arrays[key] = np.ndarray(
            tuple(info["shape"]), dtype=info["dtype"], buffer=shm.buf
        )
    return arrays, handles


def _create_output_shm(arr: np.ndarray, key: str) -> tuple:
    """Copy *arr* into a new shared memory block.

    Returns (metadata_dict, shm_handle).
    """
    name = f"flamingo_out_{key}_{uuid.uuid4().hex[:6]}"
    shm = SharedMemory(create=True, size=arr.nbytes, name=name)
    view = np.ndarray(arr.shape, dtype=arr.dtype, buffer=shm.buf)
    view[:] = arr
    return (
        {"name": name, "shape": list(arr.shape), "dtype": str(arr.dtype)},
        shm,
    )


# ---------------------------------------------------------------------------
# Task handlers
# ---------------------------------------------------------------------------


def handle_flat_field_estimate(arrays: dict, params: dict) -> dict:
    """Estimate flat-field and dark-field profiles using BaSiCPy.

    Input arrays:
        stack: (N_tiles, H, W) uint16 — middle-Z sample planes

    Returns:
        flatfield: (H, W) float32
        darkfield: (H, W) float32
    """
    from basicpy import BaSiC

    stack = arrays["stack"]
    basic = BaSiC(fitting_mode="approximate")
    basic.fit(stack)

    return {
        "flatfield": basic.flatfield.astype(np.float32),
        "darkfield": basic.darkfield.astype(np.float32),
    }


def handle_flat_field_apply(arrays: dict, params: dict) -> dict:
    """Apply flat-field correction to a volume.

    Input arrays:
        volume: (Z, Y, X) uint16
        flatfield: (Y, X) float32
        darkfield: (Y, X) float32

    Returns:
        corrected: (Z, Y, X) uint16
    """
    volume = arrays["volume"]
    flatfield = arrays["flatfield"].astype(np.float32)
    darkfield = arrays["darkfield"].astype(np.float32)

    flatfield = np.where(flatfield > 0.001, flatfield, 1.0)

    result = np.empty_like(volume)
    for z in range(volume.shape[0]):
        corrected = (volume[z].astype(np.float32) - darkfield) / flatfield
        result[z] = np.clip(corrected, 0, 65535).astype(np.uint16)

    return {"corrected": result}


def handle_leonardo_fuse(arrays: dict, params: dict) -> dict:
    """Run Leonardo FUSE_illu dual-illumination fusion.

    Input arrays:
        left: (Z, Y, X) uint16 — left illumination volume
        right: (Z, Y, X) uint16 — right illumination volume

    Returns:
        fused: (Z, Y, X) uint16
    """
    from leonardo_toolset.fusion.fuse_illu import FUSE_illu

    left = arrays["left"].astype(np.float32)
    right = arrays["right"].astype(np.float32)

    fuser = FUSE_illu()
    fused = fuser.fuse(left, right)

    return {"fused": np.clip(fused, 0, 65535).astype(np.uint16)}


TASK_HANDLERS = {
    "flat_field_estimate": handle_flat_field_estimate,
    "flat_field_apply": handle_flat_field_apply,
    "leonardo_fuse": handle_leonardo_fuse,
}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main():
    """Read task spec from stdin, execute, write results to stdout."""
    spec = json.loads(sys.stdin.read())
    task = spec["task"]
    inputs_meta = spec["inputs"]
    params = spec.get("params", {})

    if task not in TASK_HANDLERS:
        json.dump(
            {"status": "error", "message": f"Unknown task: {task}"},
            sys.stdout,
        )
        sys.exit(1)

    # Attach to input shared memory
    arrays, input_handles = _attach_arrays(inputs_meta)

    try:
        t0 = time.time()
        result_arrays = TASK_HANDLERS[task](arrays, params)
        elapsed = time.time() - t0
    except Exception as e:
        # Cleanup input handles before exiting
        for h in input_handles:
            h.close()
        json.dump(
            {"status": "error", "message": str(e), "type": type(e).__name__},
            sys.stdout,
        )
        sys.stdout.flush()
        sys.exit(1)

    # Write output arrays to shared memory
    output_info = {}
    output_handles = []
    for key, arr in result_arrays.items():
        meta, shm = _create_output_shm(arr, key)
        output_info[key] = meta
        output_handles.append(shm)

    # Respond with success
    json.dump(
        {"status": "ok", "elapsed": elapsed, "outputs": output_info},
        sys.stdout,
    )
    sys.stdout.flush()

    # Cleanup input handles (don't unlink — main process owns them)
    for h in input_handles:
        h.close()

    # Don't close/unlink output handles — main process will read then cleanup


if __name__ == "__main__":
    main()
