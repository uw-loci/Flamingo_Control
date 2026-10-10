---
paths:
  - "src/py2flamingo/workflows/**"
  - "src/py2flamingo/**/*workflow*"
  - "workflows/**"
---

# Workflow system reference

Loads when you work on workflow code or workflow files.

## Workflow System Reference

For the workflow file format (structure, sections, fields, generation and parsing code paths), read
`/home/msnelson/LSControl/claude-reports/design/workflow_file_format.md`.

### Quick Reference

#### Workflow Command Codes
- `12292 (0x3004)` - WORKFLOW_START
- `12293 (0x3005)` - WORKFLOW_STOP
- `12331 (0x302B)` - CHECK_STACK

#### cmdDataBits0 Flags for Workflows
```python
# DO NOT use TRIGGER_CALL_BACK (0x80000000) for workflow commands!
EXPERIMENT_TIME_REMAINING   = 0x00000001  # Timelapse
STAGE_POSITIONS_IN_BUFFER   = 0x00000002  # Multi-position
MAX_PROJECTION              = 0x00000004  # Z-stack MIP
SAVE_TO_DISK                = 0x00000008  # Save images
STAGE_ZSWEEP                = 0x00000020  # Z-stack operation
```

#### Common Flag Combinations
| Type | Flags | Value |
|------|-------|-------|
| Snapshot (save) | SAVE_TO_DISK | 0x00000008 |
| Z-Stack (save) | ZSWEEP \| SAVE_TO_DISK | 0x00000028 |
| Z-Stack with MIP | ZSWEEP \| MAX_PROJECTION \| SAVE_TO_DISK | 0x0000002C |

#### Illumination Source Format
```
Laser 3 488 nm = 5.00 1    # "power on/off" format (5% power, enabled)
LED_RGB_Board = 50.0 1     # 50% power, enabled
```

#### Data Save Locations

Save paths are **embedded in workflow files**, NOT queried via TCP:
- There is no saved-locations command: `0x6009` (24585) is the server's `STAGE_VELOCITY_SET`, and `SAVE_LOCATIONS_GET` is `None` in `core/command_codes.py`. Do not send it as a query; it would set the stage velocity.
- No "list available drives" command exists

Workflow fields:
```
Save image drive = /media/deploy/ctlsm1    # Base path (microscope perspective)
Save image directory = experiment_01        # Subdirectory
Save image data = Tiff                      # Format: NotSaved, Tiff, BigTiff, Raw
Save to subfolders = true                   # Organize by S{sample}/t{timepoint}/
```

Server creates: `{drive}/{datetime}_{directory}/S001_t000001_V001_R0001_X001_Y001_C01_I0.tiff`
