# Post-update verification

After a server update, a client update or a reinstall, one run checks that every
command Flamingo_Control sends still reaches the right place on the server, gets the
reply the app reads, and does what it should on the instrument. It runs from the
Windows PC on the instrument network, either in the app or from a command prompt.

## Before a run

- Connect to the microscope, and stop live view and any workflow on **every** client
  (including the Mac's ScopeControl). The server sends most replies to every connected
  client, and a busy instrument fails the first state check.
- For the motion, light and acquisition tiers, mount a **test sample or target**.
- Have `microscope_settings/<name>_settings.json` with the stage limits (written by
  **Edit > Microscope Setup**). Without it the motion checks are skipped. If the stage
  is already outside those limits, `t0.positions` fails and no motion check runs: the
  file describes a different stage. Two rigs that report the same microscope name (for
  example the default `princess`) share one file, so set `Microscope name` in each
  server's settings. Verification moves also stay inside the server's own soft limits
  (`Stage limits` in its settings): on each axis the narrower of the two applies, and
  the window lists every bound the json set wider than the server. The limits are read
  again each time **Run** is pressed, so a change saved in **Edit > Microscope Setup**
  applies without reopening the window.

## In the app

**Tools > Connection Tests > Post-update verification** has one entry per area, so each
part of the instrument can be checked on its own, and **All checks** for the combined run:

| Menu entry | Checks |
|---|---|
| All checks... | everything below, in one run and one report |
| Server and connection... | command layout, version string, stale codes refused, settings, state, drives, board notices during the run |
| Stage... | positions, go to the target, each axis a step and back, HALT on a moving stage |
| Camera... | size, pixel size, exposure, trigger mode, frame rate, ROI, light-sheet registers, binning, snapshot, live view |
| Lasers, LED and illumination... | laser power, laser preview, filter wheel, LED, left/right paths, LED brightness in the live image |
| Workflows and acquisition... | the server's stack check, image of the target, a small workflow, stopping a workflow |
| Acquisition data files... | the folder a workflow wrote, read back with the app's and the stitcher's own readers |

An area brings along the checks it depends on (the stage area reads the settings and
positions first), and its window opens with the highest tier that area needs.

1. Type what is mounted (needed from T2 up).
2. Pick a target position, or **Current position**.
   **Report folder** is where the reports go, shown as a full path. It starts as the
   `Logs` folder on the server's data drive as this computer sees it (for example
   `D:\CTLSM1\Logs`), when that folder exists, and as `verification` in the app's folder
   otherwise. A folder chosen with **Browse...** is remembered.
   **Workflow data read from** shows the local folder the app has for the server's save
   drive (the one set in the Workflow tab's save settings). The data check reads the
   workflow's `<date>_<time>_verification_...` folder there.
   **LED test level** (default 30%, at most 50%) is the LED level the T3 and T4 checks
   use; the brightness check also uses twice it. Many of the LEDs stay dark below about
   25%. The brightness check measures the LED off, at the test level, at twice it and
   at 100%; no sample is needed, the LED lights the camera through an empty chamber.
   It passes when 100% is brighter than off, and the target image and the workflow use
   the lowest level that lit (with a note when that is above the test level).
3. Tick the highest tier to run. Each tier includes the ones before it:

   | Tier | What it does |
   |---|---|
   | T0 Read-only | Version and command layout, settings, state, stage positions, every camera and laser query. Changes nothing. |
   | T1 Settings | Trigger mode, ROI and laser power (lasers off) changed, read back and restored. |
   | T2 Stage motion | Goes to the target, moves each axis a small step and back, and tests HALT on a moving stage. |
   | T3 Lasers and LED | Each laser in preview at low power, the LED, the left and right illumination paths. |
   | T4 Acquisition | Snapshot, live view, LED brightness in the live image, an image of the target, two small workflows. |

4. **Dry run** lists every command the selected checks would send, and sends nothing.
5. **Run** asks for confirmation above T1, then shows each result as it finishes. The bar
   counts checks.
6. Settings a check changes are put back as soon as that check ends, so one check never
   leaves the camera or a laser changed for the next. The stage returns from the target to
   where the run started at the end.
7. **Abort** stops at once: all axes halted, lasers and LED off, live view and workflow
   stopped. Changed settings are restored; the stage stays where it stopped.

## From a command prompt

From the `Flamingo_Control` folder, with the app's environment active:

```
python -m py2flamingo.verification --host 10.129.37.20 --tiers T1
python -m py2flamingo.verification --host 10.129.37.20 --tiers T4 --sample "bead slide"
python -m py2flamingo.verification --dry-run --tiers T4
python -m py2flamingo.verification --compare verification\n7_20261119_101500 verification\n7_20261120_093000
```

`--area stage` (or `server`, `camera`, `light`, `acquisition`, `data`) runs one area,
at that area's highest tier unless `--tiers` says otherwise. `--data-root D:\CTLSM1` is the
server data folder for the data check. `--led-percent 40` sets the LED test level
(1-50, default 30).

T2 and above ask for confirmation at the prompt (or take `--confirm-motion`,
`--confirm-light`, `--confirm-acquisition`). Ctrl+C aborts as above. `--target NAME` picks a
target; `--save-drive` picks the server drive for T4's images (default: the first one
listed). The exit code is 0 when nothing failed.

## Reading the result

Each run writes a folder `<report folder>/<microscope>_<date>_<time>/`:

- `report.txt`: the header (app and server versions, command layout, tiers, what was
  mounted, what was confirmed), then **PROBLEMS**, then every check.
- `report.json`: the same, for `--compare`.
- `exchange.log`: every message sent and received, decoded, with the check it belongs to.
- `target_<name>.tif`: the T4 image of the target.

| Result | Meaning |
|---|---|
| PASS | The reply came back and the effect was confirmed (read back, position reached, image changed). |
| FAIL | Something did not happen as expected; the line says what was expected and what came back. |
| ACK-ONLY | The server acknowledged, but nothing can read the effect back. |
| BLOCKED | Not run, because a check it depends on did not pass (the reason is shown). |
| SKIPPED | Not applicable here (for example binning on a build without it, or no target defined). |
| ERROR | The check itself broke; the app log has the traceback. |

**Before and after an update:** run the same tiers once before and once after, then
compare the two folders (`--compare`). New failures, values that changed and checks that
became much slower are listed first.

## Targets

To image the same place on every run, add targets to the microscope's settings json:

```json
"verification_targets": [
  {"name": "bead slide", "x": 6.0, "y": 12.0, "z": 19.0, "r": 0.0,
   "reference_dir": "verification/references/bead slide"}
]
```

The target must lie inside the stage limits. Put a `target_*.tif` from a good run into
`reference_dir`, and later runs report the image shift against it, besides intensity and
focus. The json can also hold `keep_out_boxes` (name plus x/y/z ranges) that no
verification move may enter.

## Known results on a correct server

No check is expected to fail on a correct server.

Fixed: the acquisition files from servers dated 2026-10-06 and later
(`t4.acquisition_files`). These builds write `Left path recording = true`, `ROI width`,
`Exposure time (ms)` and each light source's recording as `true`/`false`, where 3.0.0
wrote `Left path = ON 1`, `AOI width`, `Exposure time (ns)` and `1`/`0`. The app's
readers knew only the old form, so they read every such acquisition as lit from neither
side and with no laser on. Since 2026-11-22 the app reads both forms. The check reads the
illumination side and the exposure from the server's `Workflow.txt` the way the app does.

Fixed: the camera ROI. The app sent only the left/top edge; the server also reads the
right/bottom edge (int32Data1) and the 2026-11-13 build reverted every change. Since
2026-11-21 both edges are sent.

Fixed: the workflow exposure. The app keeps it as `Exposure time (us)`, which no server
reads, so the camera kept its previous exposure. Since 2026-11-21 the app rewrites it when
it sends a workflow: `Exposure time (ns)` for 3.0.0, `Exposure time (ms)` for later builds,
in milliseconds.

The data check looks for the folder the server names `<date>_<time>_verification_...`
at the top of its save drive.
