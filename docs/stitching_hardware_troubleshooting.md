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

**Separate scratch drive (now supported, strongly recommended for TB runs)**:
set a **Scratch dir** to redirect `.stitch_tmp/` (tile spill + fused memmap)
onto a fast **local** SSD/NVMe, independent of the output drive. Single most
important knob for full-res runs — the fuse phase streams >1 TB through
`.stitch_tmp`, so a slow/spinning/network scratch drive backs writes up in RAM
and can **freeze the whole machine** (memory thrash; Kernel-Power 41
`BugcheckCode=0` + Resource-Exhaustion 2004). Pair with stitcher **≥ v0.5.7**,
whose auto super-block fusion bounds the fuse working set (~127 GB → ~32 GB at
full res). See the shipped `docs/stitching_hardware_troubleshooting.md` §2
("the whole computer freezes") and `2026-07-10-stitcher-freeze-fix-and-superblock.md`.

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

### Symptom: Run aborts seconds in with `Tile N shape (1502, 1024, 1024) != expected (1287, 1024, 1024)`

The acquisition has **per-tile Z ranges** — what Collect Tiles produces, so
the scope images only the depth where the sample actually is. Tiles then
legitimately differ in plane count.

Fixed in **v0.10.1**. Before that, `_materialize_tiles_to_disk` probed
`tiles[0]` and imposed its shape on every tile. Nothing downstream required
that: `_build_fusion_inputs` already places each tile at its own `z_min_mm`,
and multiview-stitcher accepts views of differing shape.

Two things made this hard to see coming:

* the header line `Planes per tile: 1287 (Z range: …)` reported **tile 0
  only**, so the log gave no hint the tiles were ever different sizes. It now
  reports the range (`Planes per tile: 1287–1502 (varies — per-tile Z ranges)`).
* the border-QC pass failed first with the same message and was swallowed as
  `Border QC pass failed (skipped)`.

Frame size (Y/X) must still match across tiles — that means the AOI or binning
changed mid-acquisition, and no placement can reconcile it. The Z **step** must
also match; a differing step now warns, because one voxel size is applied to
every tile and a mismatch silently stretches Z.

### Symptom: Stitching aborts with `ArrayMemoryError: Unable to allocate 5.68 GiB`

Three separate bugs were all rooted in silent full-tile copies inside
dask / numpy:

* `np.ascontiguousarray` on the camera-X-flip view (commit `5e17a60`)
* `dask.from_array(memmap, asarray=False)` — asarray flag was ignored
  (commit `88f88c2`; fixed by building dask graph by hand)
* `volume.astype(np.float64)` inside depth attenuation (commit `a11edd4`)

If you hit a new one on a recent branch, grab the full traceback — the
line number pins which copy is the culprit.

### Symptom: Run finishes the fuse, then aborts at "Writing pyramidal OME-TIFF" with `Unable to allocate ~N GiB ... float64`

The whole streaming pipeline succeeded — the fused volume is already on
disk as `fused.dat`. Only the **pyramid builder** OOM'd, because it
materialised a whole downsample level in RAM as float64 (`.mean()` before
`.astype`). The tell is the error shape: **half the fused Y/X, dtype
float64**, e.g. `(1662, 7424, 5120)` for a `(1662, 14849, 10241)` fuse =
471 GiB. This is *not* a full-resolution limit.

Fixed in **v0.5.0** (`75aa9de`): `_downsample_yx_to_memmap` spills each
pyramid level to disk one Z-plane at a time. Check the version banner at
the top of the log; if it predates 0.5.0, update. Immediate workaround at
the same resolution: switch **Output format -> OME-Zarr (sharded)**, which
writes the pyramid chunk-by-chunk and never holds a level in RAM.

Related, same run: `Registration failed: Missing optional dependency
'pandas' ... Falling back to metadata positions only` means the tiles
were **not** registered (raw stage positions used). v0.5.0 makes pandas a
hard dependency (`pyproject.toml`), so the silent fallback can't happen.

### When you hit an OOM: what to change (keeps the chosen resolution)

On a memory failure the tool now prints step-aware advice in the log
(and the memory-watchdog popup mirrors the top of it), keyed to the step
that ran out and to the settings actually in force — it never suggests a
lever already engaged (e.g. "switch to Streaming" while already
streaming). The reference table:

| Step that OOM'd | Levers (all keep full resolution, most-effective first) |
|---|---|
| **Write** | OME-TIFF/Imaris -> **OME-Zarr (sharded)** (streams pyramid); update to v0.5.0+; fewer pyramid levels |
| **Fuse / preprocess** | Lower preprocess/fuse workers to 1-2 (workers x tile = the streaming working set); turn off content-based blending; disable per-tile float buffers (deconvolution, depth attenuation, non-fast destripe, flat-field); Leonardo illum-fusion -> Max/Mean; scratch dir on a fast, roomy disk |
| **Register** | Tick "Skip registration (use stage positions)" if positions are good; ensure pandas is installed |
| **Any, in-memory mode** | Switch Memory mode -> Streaming — the single biggest lever |
| **Last resort (any)** | Raise XY or Z downsample one step — *the only lever that lowers resolution* |

Logic lives in `oom_advice.py` (pure, unit-tested); the GUI failure
handler and the watchdog popup both call it.

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

### Symptom: log says `multiview-stitcher 0.1.44 is TOO OLD` on a build that bundles 0.1.59

The guard was reading **package metadata**, and the metadata was stale.
Inno Setup's `[Files]` only adds and overwrites — it never removes a file the
new bundle no longer contains. Python metadata lives in *versioned* directory
names (`multiview_stitcher-0.1.44.dist-info`), so upgrading in place left every
old release's dist-info sitting beside the new one, and `importlib.metadata`
answered with whichever it found first.

CI proves the build was never at fault: every release back to **v0.8.4**
resolved multiview-stitcher **0.1.59**.

Fixed in **v0.10.1**, both halves:

* `installer.iss` now has an `[InstallDelete]` wiping `{app}\_internal` before
  the copy, so each upgrade is a clean slate.
* the guard judges the **imported module's** `__version__` (the code that
  actually runs), and reports any module-vs-metadata disagreement explicitly.

If a log still shows a mismatch line, the install directory predates v0.10.1 —
uninstall, then reinstall.

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

## 5. Tuning by RAM Tier

The defaults target a 64 GB box. Larger systems get bigger wins from
**chunk-count reduction** (XL fusion chunks) than from RAM-fed tricks —
the fuse-store inner loop is dask-task-dispatch-bound at ~35 tasks/s
per 4 cores regardless of memory.

Reference run for sizing: 66 tiles × 2 channels × 727 Z × 2048² → 215 GB
output, 6 h 8 min total wall-clock on 192 GB / 28-core / NVMe Gen 5.
Channel fuse-store dominated (162 + 181 min). Imaris write was 11 min.

| RAM        | Memory Mode | Fusion chunk | preprocess_workers | fuse_workers | Notes                                                                 |
|------------|-------------|--------------|--------------------|---------------|-----------------------------------------------------------------------|
| 16–32 GB   | Streaming   | Small (4 MB) | 1–2 (auto)         | 1–2 (auto)    | Memory pill must be green. Expect ~1.5–2× longer than the table below. |
| 64 GB      | Streaming   | Medium (16 MB) | 2–4 (auto)        | 2–4 (auto)    | Defaults. Safe headroom for 200+ GB outputs.                          |
| 128 GB     | Streaming   | **Large (32 MB)** | 4 (auto)        | 4 (auto)      | Current default chunk size — sweet spot for usability + speed.       |
| 192 GB     | Streaming   | Large or XL  | 4 (auto)           | 4 (auto)      | Validated: 6 h 8 min for 215 GB output, Large chunks.                |
| 256 GB+    | In-memory if output < ½ RAM, else Streaming | XL (128 MB) | 6–8 (manual) | 6–8 (manual) | XL drops chunk count ~4× → fuse-store ~3× faster. Biggest single win.|
| 512 GB+    | In-memory   | XL (128 MB)  | 8 (manual)         | 8 (manual)    | Skips fused.dat memmap entirely. Saves ~45 min per 200 GB output.    |

**Knob priorities (most-to-least impact for >128 GB systems):**

1. **XL fusion chunks (128 MB).** Dialog → Fusion chunk size → XL.
   Reduces dask task count ~4× vs Large, ~32× vs Small. The fuse-store
   `~35 tasks/s` ceiling is per-task, so chunks ÷ 4 ≈ wall-clock ÷ 4 for
   the fuse phase. *Caveat:* slightly worse zoom/random-access UX in
   napari/Imaris because each chunk is a 128 MB I/O unit.
2. **In-memory mode** (Streaming OFF). Skips the 215 GB `fused.dat`
   round-trip on the output drive. Saves ~20–25 min per channel. Only
   safe when the green memory pill stays green at full settings.
3. **Raise `fuse_workers` to 8.** Edit `stitching_config.yaml`
   (no UI yet). Helps modestly because some tasks block on tile-memmap
   reads; numpy/scipy contention caps the gain at ~5–15%.
4. **Raise `preprocess_workers` to 8.** Same YAML. Tiny win — preprocess
   is already <5% of total wall-time.

**What more RAM does NOT help:**

- The dask scheduler ceiling (~35 tasks/s/4-cores). Pure Python overhead.
- Imaris write throughput (already I/O-bound at ~325 MB/s on NVMe).
- Per-chunk numpy/scipy cost in content-based fusion (CPU-bound).

**Recommendation by use case:**

- *Production stitching, exploring in napari/Imaris afterward*: stick with
  Large chunks, In-memory mode if RAM allows. Best balance of run time
  and post-stitch usability.
- *Bulk batch jobs, archive-only output*: XL chunks + In-memory + 8
  workers. Wall-clock matters more than zoom UX.
- *Tight RAM (≤64 GB)*: defaults. The auto-pickers are tuned for this case.

---

## 6. TODOs for Docs / Code

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
