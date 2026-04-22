# Stitching: Hardware Requirements & Troubleshooting Log

Living document — things we've hit during real-world stitching runs, plus
the hardware assumptions the pipeline is making. Add to it as new
symptoms appear; remove items that no longer reproduce after code fixes.

---

## 1. Hardware Requirements

### RAM

| Use case                                     | Minimum | Recommended |
|----------------------------------------------|---------|-------------|
| Single-tile quick-look (<10 GB raw)          | 16 GB   | 32 GB       |
| Mid-size acquisitions (<100 GB raw)          | 32 GB   | 64 GB       |
| Full-resolution TB-scale (500+ GB raw)       | 64 GB   | **128+ GB** |
| With content-based fusion + multi-channel    | 64 GB   | **192+ GB** |

The pipeline **streams** by default when output exceeds ~60% of RAM —
it spills tile and fused data to disk, so RAM pressure stays low (the
auto-picker targets ~10 GB peak for streaming mode regardless of
output size). Systems down to 16 GB can run streaming jobs, they just
get fewer parallel workers:

* `preprocess_workers`: auto-picks 1–4 based on `psutil.virtual_memory().available / (2.5 × tile_bytes × 2)`.
* `fuse_workers`: auto-picks 1–4 based on `0.75 × available_ram / 1 GB`.
* Both fall back to single-threaded synchronous on very tight RAM (no crash, just slower).

The **memory estimate badge** in the stitching dialog (green / orange /
red "OOM!" pill) is the best real-time check — it recomputes as you
change settings.

### Storage

**Output drive must be fast, with sequential and random bandwidth to
match:**

* **NVMe Gen 4/5 SSD** — strongly recommended for full-resolution TB-scale
  runs (the dev box we validate on is a Samsung 9100 PRO, PCIe 5.0 ×4,
  ~14.8 GB/s sequential).
* **SATA SSD (500 MB/s)** — acceptable for <100 GB acquisitions, will
  cap fuse throughput at ~200–300 MB/s on content-based runs.
* **Spinning HDD** — **not supported.** Random memmap access patterns
  plus the ~215 GB fused memmap plus 375 GB tile spill will thrash the
  head for days. The pipeline will complete, but wall-clock is
  effectively unbounded.

**Disk headroom during a run**. Streaming mode keeps three things on
disk simultaneously for most of the run:

| Item                        | Typical size (66-tile 2-channel 750 GB acq) |
|-----------------------------|----------------------------------------------|
| Per-channel tile spill      | ~375 GB (one channel at a time)              |
| Fused (C, Z, Y, X) memmap   | ~215 GB                                      |
| Final output file           | ~215 GB                                      |
| **Peak total**              | **~805 GB** (fused + spill) + output growing |

The pipeline logs this up front:

```
Output drive (...): 1917 GB free, need ~215 GB output + ~375 GB tile spill + ~215 GB fused memmap
```

and warns if free space falls under 110% of need. **Plan for 2–3× the
raw acquisition size in free output-drive space.**

**Separate temp drive**: not currently supported. If your output drive
is slow/full, there's no way to redirect `.stitch_tmp/` to a faster
disk. This is a TODO (`StitchingConfig.temp_dir`).

### CPU

* 4+ physical cores recommended. The `fuse_workers` auto-picker caps at
  4 because content-based fusion hits numpy/scipy contention past that.
* Content-based fusion is **CPU-bound** (two NaN Gaussian filters per
  output chunk × ~3800 chunks for a 66-tile acquisition at 2048² tiles).
* Observation from real runs: synchronous-scheduler fusion on a 28-core
  box used **exactly one core (~4%)** while other cores sat idle. That's
  why we switched to threaded scheduling in commit `d43a02d`.

### GPU

* **Required for**: deconvolution (pycudadecon on NVIDIA CUDA, or
  RedLionfish on any GPU via OpenCL).
* **Not used for**: fusion, blending, pyramid generation, writing.
  Everything except deconv runs CPU-side.

---

## 2. Things to Check When a Run Looks Stuck

### Symptom: fuse phase logs "Storing channel N into fused memmap..." then no movement for 30+ min

**Look at CPU and disk in Task Manager.**

| CPU         | Disk        | Probable cause                                                                 | Fix |
|-------------|-------------|-------------------------------------------------------------------------------|-----|
| ~4% (1 core) | ~0%         | Synchronous scheduler on content-based fusion                                  | Pull latest (`d43a02d`+); auto-picker uses `threads×4` |
| ~16% (4 cores) | active     | Normal threaded fuse — let it run                                              | Wait, ~7–10 min/channel expected |
| ~0%         | ~0%         | Actual hang (deadlock, stuck Gaussian filter on NaN-heavy overlap)             | Kill; turn off content-based fusion; report log |
| ~100%       | ~0%         | GIL contention or unexpected Python loop                                       | Kill; capture py-spy if possible; report |
| any         | **pegged**  | Slow drive or contention with another process                                  | Move output to faster drive |

### Symptom: preprocessing 66 tiles takes 20+ min on NVMe

Per-tile time should be ~2–3 s with 4 workers on NVMe, ~8 s serial.
If per-tile is much higher:

* Check the tile size in the log — `Tile output shape: (727, 2048, 2048) (5.68 GB uint16)`.
  If Z range is huge (say 2000+ planes), tile_bytes is proportional.
* Check that the worker count log says `4 workers` and not `1 worker`.
  If 1, your RAM is tight — free memory first, or lower `preprocess_workers`
  is fine, just slow.
* Check that the input drive isn't being shared with the output drive
  *under heavy concurrent load from something else*.

### Symptom: Imaris write crawls (tens of seconds per 8 MB block)

Was an 11-day bug. Fixed in commit `c50322c` (fuse once to on-disk
memmap, writers read from memmap). If you see the old slowness again:

* Check git ref in log — `Git: c50322c` or later.
* Check that the "Step 6: Writing Imaris .ims" line reads "from fused
  memmap" and not "(block-streaming, no full-channel materialization)".

### Symptom: Stitching aborts with `ArrayMemoryError: Unable to allocate 5.68 GiB`

Three separate bugs were all rooted in silent full-tile copies inside
dask / numpy:

* `np.ascontiguousarray` on the camera-X-flip view (commit `5e17a60`)
* `dask.from_array(memmap, asarray=False)` — asarray flag was ignored
  (commit `88f88c2`; fixed by building dask graph by hand)
* `volume.astype(np.float64)` inside depth attenuation (commit `a11edd4`)

If you hit a new one on a recent branch, grab the full traceback — the
line number pins which copy is the culprit.

### Symptom: Stitching crashes before it starts with `SyntaxError` mentioning `\u00b5` or a backslash in f-string

Python ≤3.11 can't parse backslash escapes inside f-string expressions.
Fixed in commit `2a49f2d` (literal `µ` instead of `\u00b5`). If it
recurs, search the stitching module for `f".*\\u[0-9a-fA-F]{4}.*{.*}"`.

### Symptom: "Fast" checkbox stays enabled but "Destripe" is disabled

Fixed in commit `dfaedeb` — Fast's state now tracks Destripe. If Fast
ever slips back to checked while Destripe is off, pystripe will be
invoked silently inside the fuse graph.

### Symptom: Destripe starts checked even though pystripe isn't installed

Fixed in commit `a11edd4` — availability probe runs after `_restore_settings`.
QSettings can restore `True` on a machine where the backend was
uninstalled since.

### Symptom: OME-Zarr (Fiji-compatible) selected, output is OME-TIFF

Fixed in commit `23effbc` — `create_array` kwarg conflict was raising,
and the pipeline silently fell back to TIFF. If it recurs, grep for
"OME-Zarr v2 write failed" in the log.

### Symptom: content-based fusion crashes with "'NoneType' object is not subscriptable"

Fixed in commit `3813490`. `multiview-stitcher` v0.1.48's
`calculate_required_overlap` dereferences `weights_func_kwargs["sigma_2"]`
without a None check; we now pass the default `{sigma_1: 5, sigma_2: 11}`
explicitly. A fallback in `_fuse_with_fallback` also retries without
content-based if it crashes for any other reason.

### Symptom: "pystripe not installed, skipping destriping" on every tile

Harmless if destripe is genuinely off. If it's on (the dialog should
have prevented that — see commit `a11edd4`), install pystripe:

```
pip install pystripe
```

Same for `basicpy` (flat-field) and `leonardo-toolset` (dual-illum
fusion) — both require an isolated env via the "Setup Preprocessing..."
button because they pin incompatible scipy/jax versions.

### Symptom: Imaris install path unclear to new users

`PyImarisWriter` is Windows-only and requires a wheel from Bitplane.
See §8 of `lightsheet_stitching_options.md` for the install recipe.
The Imaris option in the format dropdown disables itself when
`import PyImarisWriter` fails.

---

## 3. Cross-System Gotchas

### Windows memmap file locks

`numpy.memmap` holds file locks on Windows until every Python reference
is gc'd. `shutil.rmtree('.stitch_tmp')` can fail with "file in use"
if any memmap object lives in a local variable or a traceback frame.
All the cleanup paths in `_run_streaming` now do `del stacked;
stacked = None; gc.collect()` before `rmtree`. If you see lingering
`.stitch_tmp/fused.dat` files after a completed run, report it —
it's a leaked reference somewhere.

### Python 3.11 vs 3.12 f-string syntax

Windows box and dev Linux box had different Python versions in our
test. Any `f"...\uXXXX..."` with a backslash *inside* the expression
portion crashes on ≤3.11. Use the literal Unicode character in source.

### QSettings across machines

The dialog persists checkbox state per-user. A checkbox that was True
on the first machine (where its backend was installed) will restore
True on a second machine (where it isn't) unless the availability
probe re-runs *after* `_restore_settings`. This is commit `a11edd4`.

### dask / ngff-zarr version pinning

`requirements.txt` excludes `dask` 2025.12.0–2026.3.0 because they
break ngff-zarr's zarr_format selection when writing OME-Zarr v0.4
(produces zarr v3 silently — Fiji can't open). Track
[ngff-zarr PR #480](https://github.com/fideus-labs/ngff-zarr/pull/480).
See `claude-reports/lightsheet_stitching_options.md` and the TODO in
`memory/MEMORY.md`.

---

## 4. Observability We Added (and Why)

| Log line                                    | Captures                                                                 | Added in  |
|---------------------------------------------|--------------------------------------------------------------------------|-----------|
| `Input data on disk: ~N GB across N files`  | sanity check vs what user thinks they're pointing at                     | `9948a8a` |
| `System RAM: N total, N available; peak N`  | real-time RAM headroom vs projected use                                  | `9948a8a` |
| `Output drive (...): N free, need ... + ...`| three-term disk demand with headroom warning                             | `9948a8a`, `c50322c` |
| `Materializing N tiles for channel X (K workers)` | worker count picked by auto-heuristic                              | `cfbc836` |
| `Preprocessed N tiles in Xs (Y s/tile, Z GB/s)` | per-channel aggregate throughput                                     | `cfbc836` |
| `Storing channel X (scheduler=threads×N)`   | fuse scheduler chosen (`threads×N` vs `synchronous`)                     | `d43a02d` |
| `Fused output memmap: (C,Z,Y,X) N GB → path`| confirms the memmap refactor is live                                     | `c50322c` |
| `Imaris writer overhead: ~N GB`             | format-specific RAM warning                                              | `9948a8a` |

---

## 5. TODOs for Docs / Code

* [ ] `StitchingConfig.temp_dir` — let users put `.stitch_tmp/` on a
      separate fast drive from the output drive.
* [ ] Per-chunk progress during `da.store` (a dask `Callback` firing
      every ~5% of chunks). Closes the "silent 2-hour wall" diagnostic
      gap we hit.
* [ ] User-facing requirements doc in the repo itself (currently lives
      in `claude-reports/`). Candidate path: `docs/stitching_hardware.md`.
* [ ] "Setup Preprocessing..." button documentation — how isolated env
      at `%APPDATA%/Flamingo/preprocessing_env` works, when it's needed,
      how to reset.
