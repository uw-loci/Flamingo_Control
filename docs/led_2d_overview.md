# LED 2D Overview - User & Developer Guide

Quick sample orientation scanning for finding samples and planning acquisitions.

**Last Updated:** 2026-01-28

---

## Table of Contents

1. [Overview](#overview)
2. [Features](#features)
3. [Quick Start](#quick-start)
4. [User Workflow](#user-workflow)
   - [Stage 1: Configuration](#stage-1-configuration)
   - [Stage 2: Scanning](#stage-2-scanning)
   - [Stage 3: Results & Selection](#stage-3-results--selection)
   - [Stage 4: Tile Collection](#stage-4-tile-collection)
5. [Configuration Parameters](#configuration-parameters)
6. [Architecture (Developer Reference)](#architecture-developer-reference)
   - [Component Overview](#component-overview)
   - [Data Flow](#data-flow)
   - [Key Classes](#key-classes)
   - [Signal Flow](#signal-flow)
7. [Troubleshooting](#troubleshooting)
8. [Related Features](#related-features)

---

## Overview

The LED 2D Overview feature creates fast 2D overview maps of your sample at two rotation angles (R and R+90 degrees). This allows you to:

- Quickly locate samples within the imaging volume
- See the sample from two orthogonal views
- Select specific regions of interest for detailed imaging
- Generate tile collection workflows directly from the overview

The feature uses LED illumination (rather than lasers) for safe, quick scanning without photobleaching concerns.

---

## Features

- **Dual-rotation scanning** - Creates overview maps at R and R+90 degrees
- **Multiple visualization types** - Best focus, Extended Depth of Focus (EDF), min/max/mean intensity projections
- **Interactive tile selection** - Click to select tiles for detailed imaging
- **Auto-selection** - Threshold-based automatic tile selection to find sample regions
- **Direct workflow integration** - Generate Tile Collection workflows from selected tiles
- **Save/load sessions** - Save complete scan sessions for later review and workflow generation
- **Fast mode** - Continuous Z sweeps for quick scanning (~6 Z-planes with 250µm steps)

---

## Quick Start

1. **Open** the dialog from `Extensions → LED 2D Overview...`
2. **Define bounding box** using 2-3 points (use "Get Pos" buttons at sample corners)
3. **Set LED settings** - Start Live View with LED enabled, then click "Refresh from Sample View"
4. **Set rotation angle** - Use current R or enter a starting angle
5. **Click Start Scan** - Wait for dual-rotation scan to complete
6. **View results** - Select tiles and use "Collect tiles" for detailed imaging

---

## User Workflow

### Stage 1: Configuration

#### Bounding Points

Define the scan region using 2-3 corner points:

| Point | Required | Description |
|-------|----------|-------------|
| Point A | Yes | First corner of bounding box |
| Point B | Yes | Opposite corner (defines X, Y, Z ranges) |
| Point C | Optional | Expands bounding box if provided |

**Setting Points:**
1. Navigate to sample corner using stage controls
2. Click **"Get Pos"** to capture current position
3. Repeat for Point B (and optionally Point C)

**Loading Presets:**
- Select target point (A, B, or C) from dropdown
- Select saved position preset
- Click **"Load"** to fill in coordinates

#### LED Settings

The scan uses LED illumination settings from Sample View:

1. Open Sample View
2. Start Live View with desired LED enabled
3. Return to LED 2D Overview dialog
4. Click **"Refresh from Sample View"** to capture settings

Alternatively, click **"Reload Last Used"** to use settings from a previous scan.

#### Rotation Settings

| Parameter | Default | Description |
|-----------|---------|-------------|
| Starting R | 0° | First rotation angle (second will be +90°) |
| Z Step Size | 250 µm | Step size for focus search (larger = faster) |
| Focus Stacking | Off | Combine best-focused regions from Z-stack |

**Tip:** Click **"Get Current R"** to use the current stage rotation.

### Stage 2: Scanning

After clicking **Start Scan**:

1. System confirms scan parameters (tiles, rotations, region)
2. Stage rotates to first angle (R)
3. Tiles are captured in serpentine pattern with Z-stacks at each position
4. Stage rotates to second angle (R+90)
5. Second rotation tiles are captured
6. Results window opens automatically

**Progress Tracking:**
- Start button shows percentage: "In Progress... 45%"
- Cancel button appears during scan
- Click **"Cancel Scan"** to stop (partial results are preserved)

### Stage 3: Results & Selection

The Results window displays two side-by-side overview images:

**Left Panel:** Initial rotation (R)
**Right Panel:** Rotated view (R+90)

#### Visualization Types

Select from dropdown to change view:

| Type | Description | Use Case |
|------|-------------|----------|
| Best Focus | Single best-focused frame per tile | General viewing |
| Extended Depth of Focus | Combined sharp regions from Z-stack | Uneven samples |
| Minimum Intensity | Min projection through Z | See through bright spots |
| Maximum Intensity | Max projection (MIP) | Brightest features |
| Mean Intensity | Average intensity | Overall sample shape |

#### Tile Selection

**Manual Selection:**
- Left-click tiles to toggle selection (cyan border indicates selected)
- Selected tiles appear in both panels

**Bulk Selection:**
- **"Select All"** - Selects all tiles
- **"Clear Selection"** - Deselects all tiles
- **"Auto-Select..."** - Opens thresholder for automatic selection

**Auto-Selection (Thresholder):**
1. Click **"Auto-Select..."**
2. Adjust threshold slider until sample tiles are highlighted
3. Preview shows which tiles will be selected
4. Click **"Apply"** to confirm selection

#### Navigation

**Right-click** on a tile to move stage Z to that tile's center Z position.

**Zoom/Pan:**
- Mouse wheel to zoom
- Click and drag to pan
- **"Fit"** button to fit image to view
- **"1:1"** button for 100% zoom

### Stage 4: Tile Collection

After selecting tiles, click **"Collect tiles"** to open the Tile Collection Dialog:

1. Configure acquisition parameters (Z-stack settings, illumination, save location)
2. Review the list of workflows to be generated
3. Click **"Generate Workflows"** to create and execute

The system generates one workflow per selected tile, using the tile's position and Z-stack range from the overview scan.

---

## Configuration Parameters

### Scan Settings

| Parameter | Default | Range | Description |
|-----------|---------|-------|-------------|
| Z Step Size | 250 µm | 50-1000 µm | Step between Z planes during focus search |
| Fast Mode | On | On/Off | Use continuous Z sweeps (faster) |
| Focus Stacking | Off | On/Off | Combine best-focused regions |

### LED Settings (from Sample View)

| Parameter | Source | Description |
|-----------|--------|-------------|
| LED Color | Sample View | Red, Green, Blue, or White |
| LED Intensity | Sample View | 0-100% power |

### Calculated Values

| Parameter | Formula | Description |
|-----------|---------|-------------|
| Tiles X | `ceil(width / FOV) + 1` | Number of tiles in X |
| Tiles Y | `ceil(height / FOV) + 1` | Number of tiles in Y |
| Z Planes | `ceil(z_depth / z_step) + 1` | Z-stack depth per tile |
| Total Tiles | `tiles_x × tiles_y × 2` | Both rotations |

**Note:** FOV is queried from camera service dynamically to ensure accuracy.

---

## Architecture (Developer Reference)

### Component Overview

The LED 2D Overview feature consists of 7 main files (~228KB total):

| File | Size | Purpose |
|------|------|---------|
| `views/dialogs/led_2d_overview_dialog.py` | 60KB | Configuration dialog |
| `views/dialogs/led_2d_overview_result.py` | 68KB | Results display window |
| `views/dialogs/tile_collection_dialog.py` | 73KB | Tile workflow generation |
| `workflows/led_2d_overview_workflow.py` | 52KB | Scan execution engine |
| `views/dialogs/overview_thresholder_dialog.py` | 20KB | Auto-selection tool |
| `views/dialogs/mip_overview_dialog.py` | 28KB | Related overview feature |
| `models/mip_overview.py` | 8KB | Shared data models |

### Data Flow

```
LED2DOverviewDialog (Configuration)
        │
        │ scan_requested signal
        ▼
LED2DOverviewWorkflow (Execution)
        │
        │ Captures tiles at each rotation
        │ Calculates projections (best_focus, min, max, mean, EDF)
        │ Stitches tiles into grid
        │
        │ scan_completed signal
        ▼
LED2DOverviewResultWindow (Display)
        │
        │ User selects tiles
        │
        ▼
TileCollectionDialog (Workflow Generation)
        │
        │ Generates per-tile workflows
        ▼
WorkflowQueueService (Execution)
```

### Key Classes

#### Configuration Classes

```python
@dataclass
class BoundingBox:
    """Axis-aligned bounding box for scan region."""
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    z_min: float
    z_max: float

@dataclass
class ScanConfiguration:
    """Complete scan configuration for LED 2D Overview."""
    bounding_box: BoundingBox
    starting_r: float          # Rotation angle in degrees
    led_name: str              # e.g., "led_red"
    led_intensity: float       # percentage (0-100)
    z_step_size: float = 0.050 # mm (50 um default)
    use_focus_stacking: bool = False
    fast_mode: bool = True
```

#### Result Classes

```python
@dataclass
class TileResult:
    """Result for a single tile."""
    x: float
    y: float
    z: float
    tile_x_idx: int
    tile_y_idx: int
    images: dict              # visualization_type -> np.ndarray
    rotation_angle: float     # Rotation angle in degrees
    z_stack_min: float        # Z-stack bounds for overlap calculation
    z_stack_max: float

@dataclass
class RotationResult:
    """Result for a single rotation angle."""
    rotation_angle: float
    tiles: List[TileResult]
    stitched_images: dict     # visualization_type -> np.ndarray
    tiles_x: int
    tiles_y: int
    invert_x: bool            # Whether X-axis is inverted for display
```

### Signal Flow

```
LED2DOverviewDialog
    │
    ├── scan_requested(ScanConfiguration) ──► LED2DOverviewWorkflow
    │
LED2DOverviewWorkflow
    │
    ├── scan_started() ──────────────────► UI updates
    ├── scan_progress(str, float) ───────► Progress display
    ├── tile_completed(rot_idx, tile_idx, total) ──► Per-tile progress
    ├── rotation_completed(rot_idx, RotationResult) ──► Rotation done
    ├── scan_completed(List[RotationResult]) ──► Results window opens
    ├── scan_cancelled() ────────────────► Cleanup
    └── scan_error(str) ─────────────────► Error handling

LED2DOverviewResultWindow
    │
    ├── selection_changed() ─────────────► Update collect button state
    │
ImagePanel
    │
    ├── tile_clicked(tile_x_idx, tile_y_idx) ──► Toggle selection
    └── tile_right_clicked(tile_x_idx, tile_y_idx) ──► Move to Z
```

### Integration Points

The feature integrates with the main application through minimal, clean interfaces:

**Menu Entry** (`main_window.py`, lines 267-276):
```python
# Extensions menu
led_2d_overview_action = QAction("LED 2D Overview...", self)
led_2d_overview_action.triggered.connect(self._show_led_2d_overview)
extensions_menu.addAction(led_2d_overview_action)
```

**Service Dependencies** (via injection):
- `PositionPresetService` - Loading saved position presets
- `CameraService` - FOV calculation, frame capture
- `StageService` - Stage movement

**Application State**:
- `app.start_acquisition()` / `app.stop_acquisition()` - Locks microscope controls
- Signals for acquisition state (prevents conflicts)

**Architecture Quality:** This feature is well-separated (9/10) and could be extracted to a separate package with ~90% import path changes only. No direct database modifications, global state pollution, or monkey-patching.

---

## Troubleshooting

### Common Issues

**"FOV unknown - camera not ready"**
- Ensure camera is connected and initialized
- Start Live View in Sample View first
- Check that camera service is returning valid dimensions

**"No LED settings loaded"**
- Open Sample View
- Start Live View with LED enabled
- Click "Refresh from Sample View" in LED 2D Overview dialog
- Or click "Reload Last Used" if you've run a successful scan before

**Black images in results**
- LED was not enabled during scan
- Check LED intensity is not 0%
- Verify LED color is correct for your sample

**Missing R+90 rotation**
- "Tip of sample mount" preset not calibrated
- Use `Tools → Calibrate` to set tip position
- First rotation will still complete successfully

**Tiles appear inverted**
- Check `invert_x` setting in visualization config
- This matches stage direction to camera view

### Performance Tips

- Use larger Z step size (250-500 µm) for faster scans
- Fast mode (continuous Z sweeps) is significantly faster than step-by-step
- Reduce bounding box size to only cover sample area
- Use auto-select to quickly identify sample tiles

---

## Related Features

- **[MIP Overview](mip_overview_dialog.py)** - Maximum Intensity Projection overview from saved data
- **[Tile Collection Dialog](tile_collection_dialog.py)** - Workflow generation for selected tiles
- **[Workflow System](workflow_system.md)** - Underlying workflow execution engine
- **[3D Visualization](3d_visualization_usage.md)** - 3D volume rendering

---

**Document Version:** 1.0
**Based on LED 2D Overview implementation v1.0**
