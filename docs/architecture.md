# Architecture: layers, paths and shared state

For developers. This page says which package may import which, what belongs in
each, the one path every command takes to the server, the one path every stage
move takes, the safety path, and the module-level state that lives outside any
object. `tests/test_layering.py` enforces the layers; the rest is enforced by the
tests named in each section.

Naming (unit suffixes, class-role suffixes, names that must never change) is in
[glossary.md](glossary.md).

## Layers

Lowest first. A module may import from its own layer and the layers below it,
never from a layer above. "Import" includes imports inside functions and under
`if TYPE_CHECKING:`: a function-level import does not make an upward dependency
go away, it only hides it from a reader of the file's first lines.

| # | Layer | Packages | What belongs here |
|---|---|---|---|
| 0 | Protocol | `core/`, `_version.py`, `py2flamingo/__init__.py` | The 128-byte frame (`frames.py`, `tcp_protocol.py`), the command-code table (`command_codes.py`) and per-build layouts (`server_layout.py`), the TCP connection and its background reader (`tcp_connection.py`, `socket_reader.py`), queues and events, the error types (`errors.py`). Nothing here knows the GUI or a service. |
| 1 | Data | `models/` (and `models/data/`, `models/hardware/`) | Dataclasses and enums: `Position`, the workflow settings models, connection state, display settings, overview results. No file I/O beyond a model's own `to_dict`/`from_dict`, no config reads. |
| 2 | Pure helpers | `utils/`, `workflows/`, `stitching/` (shim over `flamingo_stitcher`), `psf_analysis/`, `functions/`, `resources/` | Parsers (`workflow_keys`, `file_handlers`, `workflow_parser`), geometry (`tile_geometry`, `stage_limits`), tile metrics, MIP overview discovery, saved-data version stamps. No Qt widgets, no services, no connection. |
| 2b | Hardware configuration | `configs/` | `config_loader.get_hardware_config()` (calibration > ScopeSettings.txt > YAML) and the one pixel-size reader, `configs/fov.py`. It reads files through layer 2's parsers. |
| 3 | Services | `services/`, `visualization/` | One facade per subsystem over the one connection (`StageService`, `CameraService`, `LaserLEDService`, `MVCWorkflowService`, `MVCConnectionService`), the stores that own a file (presets, calibrations, window geometry), the motion tracker and the debug commands, the safety module, and the voxel and tile data stores. |
| 4 | Engines | `acquisitions/`, `pipeline/models`, `pipeline/engine` (+ `node_runners`), `pipeline/services`, `pipeline/builder.py`, `pipeline/headless_io.py`, `verification/`, `testing/` | Scans the client drives itself (LED 2D overview, volume scan), the pipeline executor and its runners, post-update verification, and the synthetic microscope with its phantom data. |
| 5 | Controllers | `controllers/`, `pipeline/controllers`, `pipeline/headless_services.py` | One UI action each: take a request from a view, call services, report back. The headless pipeline host is the controller of a run without the editor. |
| 6 | Views | `views/` (and `dialogs/`, `widgets/`, `workflow_panels/`), `pipeline/ui` | Windows, dialogs, panels, widgets, the pipeline editor, and the color palette (`views/widgets/colors.py`). |
| 7 | Composition roots | `application.py`, `main_window.py`, `component_factory.py`, `cli.py`, `__main__.py`, `pipeline/cli.py`, the `__main__` of `verification`, `psf_analysis` and `stitching` | Build the objects of every layer and wire them. Only these modules may know every layer. |

Inside a package, an `__init__.py` that re-exports its own sub-packages is not
a cycle (that is how a package lists its members). Any other module of a package
importing a sub-package counts as an edge between them, and the package graph
must have no cycle.

Two narrower rules:

- `testing/synthetic_microscope.py` models the vendor server, so it imports only
  `core/`. It must behave like the server (`~/LSControl/server-src`), not like
  the client's idea of the server.
- `import py2flamingo` imports only `_version.py`, so the package can be
  imported without Qt, napari or a socket.

### How a lower layer reaches up

When lower code needs something from above, the upper layer hands it down; the
lower layer never imports it.

- **A callable passed in.** `LED2DOverviewAcquisition(results_presenter=...)`:
  the dialog passes the function that shows the result window.
  `PipelineController(editor_factory=...)`: the composition root passes
  `PipelineEditorDialog`.
- **A Protocol in the lower module.** `workflow_queue_service.WorkflowRunner`
  describes the six members the queue calls on `WorkflowController`.
- **A registration hook.** `stage_motion_gate.register_stop_hook(name, hook)`:
  scans register what the emergency stop must cancel.
  `safe_state.set_activity_probe(probe)`: the application registers what is
  running, so the exit path knows whether to stop the stage.
- **Move the helper down.** When the shared code has no upward dependency of its
  own (the tile metrics, the color palette, the display enums), move it to the
  layer its users share instead of adding a hook.

### Role suffixes

From the glossary; the layer a class lives in follows from its role.

| Suffix | Role | Layer |
|---|---|---|
| `Service` | Talks to the server, or owns one resource (a file, a device, a store) | 3 (pipeline analysis services: 4) |
| `Controller` | Orchestrates one UI action: takes a request from a view, calls services, reports back. Never encodes a frame or reads a socket | 5 |
| `Manager` | Owns a lifecycle or a registry (queues, events, windows, sessions) | where what it manages lives |
| `Worker` | Runs on a background thread (`QThread`, `QRunnable`, or a `QObject` moved to a thread) | the layer of the work it does |
| `Runner` | Executes one pipeline node type (`pipeline/engine/node_runners/`) | 4 |
| `Handler` | A `logging.Handler` | any |
| `Dialog`, `Window`, `View`, `Panel`, `Widget` | A Qt view | 6 |

Classes whose name does not match their role yet are listed in the step 5 and
step 6 reports; they are renamed only together with their `window_geometry.json`
IDs and the names flamingo-mcp uses (glossary, "Names that must never change").

## One protocol path

Every command the client sends, and every reply it reads, takes the same path.

1. **Encode.** `core/tcp_protocol.ProtocolEncoder.encode_command` builds the
   128-byte frame (codes from `core/command_codes.py` and, for the COMMON block,
   the active `core/server_layout`).
2. **Write.** One lock: `TCPConnection.send_lock`, taken by
   `TCPConnection.send_command_bytes`. Everything above core writes through
   `MVCConnectionService.send_raw_bytes`, which calls it; a frame and the payload
   after it go out in one locked write. Stop commands wait at most 0.5 s for the
   lock and then send anyway (`safe_state.send_packet_now`).
3. **Read.** One reader: the background `SocketReader` is the only code that
   calls `recv` on the command socket while it runs. `core/frames.py` parses
   every frame (`parse_frame`, `recv_exactly`).
4. **Match.** The reader's `MessageDispatcher` keeps a FIFO of pending requests
   per reply code; a reply goes to the oldest request that accepts it (a request
   may wait on several codes and carry a predicate, as `get_axis_position` does
   for its axis). Unsolicited messages (motion stopped, stack complete, device
   notices) go to callbacks registered with `register_callback`.
5. **Services.** `MicroscopeCommandService` is the base of the camera, stage and
   laser/LED services: `_query_command` / `_send_command` for typed commands,
   `exchange_frame` for the debug commands that show a whole reply. The
   synchronous read paths in these services run only when no background reader
   is running (never, in the app).

Nothing above the services layer reads a socket, encodes a frame or reads a
connection's private `_command_socket`
(`tests/test_protocol_stays_in_services.py`). The bytes of every command are
pinned by `tests/test_wire_bytes_unchanged.py`; the one-writer and one-reader
rules by `tests/test_one_writer.py` and `tests/test_one_reader.py`; reply
matching by `tests/test_reply_matching.py`.

## The stage-move path

`StageService.move_to_position(axis, position_mm, before_send=None)` is the one
place a move is sent. The GUI (through `PositionController`), the workflow queue
and the LED overview all call it. In order, it:

1. refuses the move while the emergency stop is latched or the instrument's
   limits are unknown (`stage_motion_gate.ensure_motion_allowed`), sending
   nothing;
2. refuses an axis other than 1-4 or a position that is not a finite number;
3. drops an exact duplicate of the move already in flight
   (`stage_command_dedup.should_send`; returns False);
4. calls `before_send` (PositionController arms its `MotionTracker` here, so a
   fast stop is not missed), then sends `STAGE_POSITION_SET` (0x6004) with the
   axis in `int32Data0` and the target in `doubleData`.

Stage limits are the caller's: `PositionController.move_x/y/z/r` raise outside
the limits, `move_to_position` and the jog panel clamp, the LED overview shifts
its whole tile block (`utils/stage_limits.py` is the one clamp helper;
`services/server_stage_limits.py` narrows the limits to the server's soft
limits). Completion is the server's `STAGE_MOTION_STOPPED` callback, waited for
by `services/motion_tracker.MotionTracker`; when the wait fails the position is
marked unknown rather than assumed to be the target.

Tests: `tests/test_one_move_path.py`, `tests/test_motion_wait_honest.py`,
`tests/test_stage_command_dedup.py`, `tests/test_unconfigured_scope_blocks_motion.py`.

## The safety path

`services/safe_state.py` holds the commands that must go out at once:
`STAGE_HALT` for all axes, `CAMERA_WORK_FLOW_STOP`, `CAMERA_LIVE_VIEW_STOP`,
`LASER_DISABLE_ALL` and per-line disables, `LED_PREVIEW_DISABLE`. They are sent
fire-and-forget on the socket that is open now: no reconnect, no retry, no wait
for a reply. Each send reports whether its bytes left the computer.

- `safe_state()` is the one function that turns the emitters off and, when
  asked, stops the stage and the server workflow.
- `leave_safe()` decides how much of it to send when the client lets go of the
  instrument: window close, quit, disconnect, or an uncaught exception
  (`install_excepthook`).
- **Emergency stop.** `PositionController.emergency_stop` sends HALT at once,
  latches `stage_motion_gate` (every later move is refused by
  `StageService.move_to_position`), sends the workflow stop, runs the registered
  stop hooks (a running scan cancels itself), and reports "HALT NOT SENT" when
  the HALT bytes could not be written. The latch stays until an operator clears it
  (`stage_motion_gate.clear_emergency_stop`).
- The verification runner has its own copy of the stop sequence
  (`verification/runner.py`) that resolves codes by name for the server build
  under test.

Tests: `tests/test_safe_state.py`, `tests/test_emergency_stop*.py`,
`tests/test_light_off*.py`.

## Module-level state

State that lives in a module rather than an object, who sets it, and who resets
it in the tests. A test that leaves one of these set changes the result of every
later test, so each is reset around every test, under both runners:
`tests/conftest.py` (pytest, autouse fixtures) and `tests/run_tests.py`
(`_reset_module_state`, the unittest runner CI uses). New module-level state
gets a reset in both.

| State | Module | Set by | Reset in tests |
|---|---|---|---|
| Emergency-stop latch, stop hooks | `services/stage_motion_gate.py` | `latch_emergency_stop` (e-stop), `register_stop_hook` (scans) | both runners: `clear_emergency_stop()`, `_stop_hooks.clear()` |
| Motion block (limits unknown) | `services/stage_motion_gate.py` | `ConfigurationService` on load and reload | both runners: `set_motion_blocked(None)` |
| Server soft limits | `services/server_stage_limits.py` | `set_from_settings` at connect | both runners: `clear()` |
| Active server layout | `core/server_layout.py` (`set_active`, `active`) | `MVCConnectionService` after it identifies the server build | the tests that set it restore it (`test_server_layout`, `test_wire_bytes_unchanged`, `test_command_codes_match_the_server`, `test_verification`) |
| Moves in flight (duplicate suppression) | `services/stage_command_dedup.py` | `StageService.move_to_position`, cleared by the motion-stopped callback | the stage tests call `reset()` |
| Hardware config cache, camera AOI | `configs/config_loader.py` | first `get_hardware_config()`, `set_camera_aoi` | `invalidate_hardware_config()` where a test changes the files |
| Default window-geometry manager | `services/window_geometry_manager.py` | `component_factory.create_services_layer` | not reset; tests that need one set their own |
| Activity probe | `services/safe_state.py` (`set_activity_probe`) | `FlamingoApplication` at startup | not reset; `None` means nothing is running |
| Log de-duplication | `configs/fov.py` (`_last_logged`) | the pixel-size reader | not reset (logging only) |
