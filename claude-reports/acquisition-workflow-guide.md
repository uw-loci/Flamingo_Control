# Flamingo Control — Acquisition Workflow Guide

A practical guide to acquiring data with the Flamingo light sheet microscope control software. Covers the end-to-end workflow from connection through targeted high-resolution Z-stack acquisition.

---

## Table of Contents

1. [Quick-Start Summary](#quick-start-summary)
2. [Step 1: Connect to the Microscope](#step-1-connect-to-the-microscope)
3. [Step 2: Open Sample View](#step-2-open-sample-view)
4. [Step 3: LED 2D Overview Scan](#step-3-led-2d-overview-scan)
5. [Step 4: Select Tiles of Interest](#step-4-select-tiles-of-interest)
6. [Step 5: Configure and Run Tile Workflows](#step-5-configure-and-run-tile-workflows)
7. [Step 6: Monitor Execution and View Results](#step-6-monitor-execution-and-view-results)
8. [Alternative Workflows](#alternative-workflows)
9. [Pipeline Editor (Automated Workflows)](#pipeline-editor-automated-workflows)
10. [Key Concepts](#key-concepts)

---

## Quick-Start Summary

The typical acquisition workflow follows this path:

```
Connect → Sample View → LED 2D Overview → Select Tiles → Configure Z-Stacks → Execute Queue
```

1. **Connect** to the microscope (Connection tab)
2. **Open Sample View** to see 3D viewer + camera + stage controls
3. **Run LED 2D Overview** to quickly map the sample area at two rotation angles
4. **Select tiles** containing sample (manually or auto-threshold)
5. **Create tile workflows** (Z-stack per tile, with illumination + save settings)
6. **Execute** — workflows run sequentially, data saved to disk, optionally shown live in 3D

---

## Step 1: Connect to the Microscope

**Location:** Main Window → Connection tab

1. Select or enter the microscope IP address and port
2. Click **Connect**
3. Wait for "Connection established" status and settings retrieval
4. Stage controls and menu items become active once connected

The status indicator in the bottom bar shows connection state (green = connected, yellow = busy, red = error).

---

## Step 2: Open Sample View

**Location:** Connection tab → "Open Sample View" button (or automatic on connection)

The Sample View window provides:

| Panel | Purpose |
|-------|---------|
| **3D napari viewer** | Multi-channel volume rendering with interactive rotation |
| **MIP panels** | 2D Maximum Intensity Projections (XY, XZ, YZ planes) |
| **Stage controls** | Sliders and input fields for X, Y, Z, R (rotation) |
| **Illumination panel** | Real-time laser/LED control with on/off and intensity |
| **Camera controls** | Live view toggle, exposure, frame rate |

**Key interactions:**
- **Click-to-move in MIP views**: Right-click any point in a 2D projection to move the stage there
- **Position presets**: Save/load named positions for quick navigation
- **Live view**: Toggle camera feed to see sample in real-time

Before proceeding, verify you can see the sample by:
1. Starting Live View
2. Enabling an LED channel
3. Adjusting stage position until sample is visible

---

## Step 3: LED 2D Overview Scan

**Location:** Extensions → LED 2D Overview

This creates a rapid low-resolution map of the sample area at two rotation angles (0 and 90 degrees by default). It captures a grid of tiles using LED illumination and focus stacking.

### 3a. Define the Scan Region

Set two corner points to define the XY bounding box:

- **Point A**: Navigate to one corner of the region of interest, click **"Get Pos"**
- **Point B**: Navigate to the opposite corner, click **"Get Pos"**
- Optionally add **Point C** to expand the box

The scan will cover the full rectangle between these points at the current Z position.

### 3b. Set Rotation Angles

- **Starting R**: The first rotation angle (default: 0°)
- The second angle is automatically R + 90°
- Click **"Get Current R"** to capture the current rotation value

### 3c. Configure Settings

| Setting | Default | Description |
|---------|---------|-------------|
| Z Step Size | 250 µm | Plane spacing during focus sweep (larger = faster) |
| Focus Stacking | On | Combines best-focused regions from all Z planes |

### 3d. Set Illumination

The scan uses LED illumination (not lasers):
1. Make sure Live View is active in Sample View with an LED enabled
2. Click **"Refresh from Sample View"** to detect the current LED channel and intensity
3. Or manually configure LED settings

### 3e. Run the Scan

Click **"Start Scan"**. The system will:
1. Move to each tile position in a serpentine pattern
2. Capture a focus sweep at each position
3. Focus-stack the result
4. Repeat for the second rotation angle
5. Open the **Result Window** when complete

Progress is shown in real-time (tiles completed / total tiles).

---

## Step 4: Select Tiles of Interest

The LED 2D Overview Result Window shows side-by-side images from both rotation angles with a tile grid overlay.

### Manual Selection

- **Click a tile** to select it (cyan border highlight appears)
- **Click again** to deselect
- **Select All / Clear Selection** for bulk operations

### Automatic Selection (Thresholder)

Click **"Auto-Select..."** to open the Thresholder Dialog:

| Method | What it Detects |
|--------|----------------|
| **Variance** | Tiles with varying pixel values (sample) vs. uniform (background) |
| **Edge Detection** | Tiles with high-contrast features and texture |
| **Intensity Range** | Tiles within a brightness range |
| **Combined** | Uses variance OR edge detection together |

1. Choose a detection method
2. Adjust the sensitivity slider — preview updates in real-time
3. Use **"Invert Selection"** if needed (swap sample/background)
4. Click **"Apply Selection"**

### Tile Navigation

- **Right-click any tile** to move the stage to that tile's center position
- Useful for verifying which tiles actually contain sample before proceeding

### Saving the Overview

- **"Save Whole Session"**: Saves images + metadata as a folder (can be reloaded later via Extensions → Load 2D Overview Session)

---

## Step 5: Configure and Run Tile Workflows

With tiles selected, click **"Collect Tiles..."** to open the Tile Collection Dialog.

### 5a. Workflow Type

| Type | Use Case |
|------|----------|
| **Z-Stack** (default) | Multiple planes through Z — most common for high-res acquisition |
| **Snapshot** | Single image per tile — fast but no Z information |

### 5b. Illumination Settings

Configure which laser/LED channels to use for the final acquisition:

- Select one or more laser channels (e.g., 488nm, 561nm)
- Set power level for each channel (mW for lasers, % for LEDs)
- Multiple channels can be acquired per tile

### 5c. Camera Settings

| Setting | Description |
|---------|-------------|
| Exposure | Integration time per frame |
| Frame Rate | Frames per second (affects Z velocity) |
| AOI (Area of Interest) | Optional crop region |

### 5d. Z-Stack Settings (for Z-Stack mode)

| Setting | Description |
|---------|-------------|
| Z Step Size (µm) | Distance between planes — smaller = higher Z resolution |
| Z Range | Automatically calculated from tile positions, or set manually |
| Plane Count | Auto-calculated from range / step size |
| Z Velocity | Auto-calculated from step size × frame rate |

**Tip:** For minimal Z step (highest resolution), use the smallest step your system supports. The plane count and total acquisition time will increase accordingly.

### 5e. Save Configuration

- **Drive**: Select the storage drive for data
- **Folder structure**: Automatically organized as `Year/Month/Day/TileName/`
- **Workflow name prefix**: All tiles get this prefix plus an index

### 5f. Optional: Live 3D Visualization

Check **"Add Z-stacks to Sample View (live)"** to see frames appear in the 3D viewer in real-time as they are captured.

### 5g. Create and Execute

Click **"Create Workflows"**:
1. System generates one workflow file per tile
2. Workflows are added to the execution queue
3. Queue starts executing immediately (sequentially, one tile at a time)

---

## Step 6: Monitor Execution and View Results

### Queue Progress

The Workflow Queue Service executes tiles one at a time:
- Progress signals show: current tile index, total tiles, file path
- Per-tile progress shows images acquired vs. expected
- Status indicator updates in the main window status bar

### Completion Detection

The system detects workflow completion via:
1. **Primary**: Server sends SYSTEM_STATE_IDLE callback
2. **Backup**: CAMERA_STACK_COMPLETE callback + polling
3. **Timeout**: 30 minutes max per workflow (configurable)

### Results

- **Files**: TIFF stacks saved to the configured drive/folder
- **3D View**: If live visualization was enabled, the volume is visible in Sample View
- **Metadata**: Tile positions and acquisition parameters are preserved

---

## Alternative Workflows

### MIP Overview (Working with Existing Data)

**Extensions → MIP Overview**

If you already have tile data on disk (from a previous session), you can:
1. Browse to the data folder containing `X{x}_Y{y}/*_MP.tif` files
2. Load MIPs into a stitched overview
3. Select tiles for re-acquisition (same selection tools as LED 2D Overview)
4. Create new workflows for just the selected tiles

### Union of Thresholders (3D Volume Analysis)

**Extensions → Union of Thresholders**

For 3D analysis of data already loaded in Sample View:

1. Set per-channel brightness thresholds (mask appears as colored contours in napari)
2. Apply filtering: Gaussian smoothing, morphological opening, minimum object size
3. View statistics: voxel count, volume in mm3, bounding box
4. **Generate Acquisition Profile**: Creates variable-Z-depth workflows based on 3D mask shape
   - Each tile gets a Z range proportional to the mask depth at that position
   - Supports multiple rotation angles

This is the most sophisticated method — it uses the 3D shape of the fluorescence to create per-tile Z ranges that avoid wasting time imaging empty space.

### Workflow Tab (Manual Single Workflow)

**Main Window → Workflow tab**

Build a single workflow from scratch without the tile selection process:
- Choose workflow type: Snapshot, Z-Stack, Time-Lapse, Tile Scan, Multi-Angle
- Configure all parameters manually
- Save as template for reuse

---

## Pipeline Editor (Automated Workflows)

**Extensions → Pipeline Editor**

The Pipeline Editor provides a visual node-graph for composing multi-step automated workflows. This is the system for building "acquire, then analyze, then for each detected object acquire again" type workflows.

### Available Node Types

| Node | Purpose |
|------|---------|
| **Workflow** | Execute an acquisition (z-stack, tile scan, etc.) |
| **Threshold** | Analyze volumes and detect objects |
| **ForEach** | Iterate over a list of detected objects |
| **Conditional** | Branch based on a comparison (count > N, etc.) |
| **External Command** | Run an external script or program |

### Example Pipeline: Acquire → Analyze → Re-acquire

This is the canonical smart microscopy workflow:

```
[Acquire Overview] → [Threshold & Detect] → [For Each Object] → [Acquire Z-Stack at Object]
     volume              objects list          current_item          position input
```

1. **Workflow node** captures an initial overview volume
2. **Threshold node** analyzes it, produces a list of detected objects (with centroids)
3. **ForEach node** iterates over each detected object
4. **Workflow node** (inside ForEach body) captures a high-res Z-stack at each object's location

### Using the Editor

1. **Drag** node types from the left palette onto the canvas
2. **Connect** ports by dragging from an output (right) to an input (left)
   - Color coding shows port types; green highlight indicates compatible connections
3. **Configure** each node by clicking it and editing properties in the right panel
4. **Validate** to check for errors (cycles, missing connections, type mismatches)
5. **Save** the pipeline as JSON for reuse
6. **Run** to execute — node status dots show progress (blue=running, green=done, red=error)
7. **Stop** to cancel at any time

Pipelines are saved as JSON files to `~/.flamingo/pipelines/`.

---

## Key Concepts

### Coordinate System

- **Stage coordinates**: (X, Y, Z, R) in millimeters and degrees
- **Napari display**: (Z, Y, X) order, Y-axis inverted (0 = top)
- **X inversion**: Optionally enabled in config to match microscope orientation

### Data Flow Summary

```
LED 2D Overview  →  Focus-stacked tiles at 2 angles
        ↓
Tile Selection   →  Subset of tiles containing sample
        ↓
Tile Collection  →  One workflow file per tile (with illumination + Z settings)
        ↓
Queue Service    →  Sequential execution, one tile at a time
        ↓
Voxel Storage    →  3D volume assembled from incoming frames (optional live view)
```

### File Organization

Acquired data follows this folder structure:
```
Drive:/
  YYYY/
    MM/
      DD/
        WorkflowName_001/
          X{x}_Y{y}/
            frame_0001.tif
            frame_0002.tif
            ...
```

### Queue vs. Pipeline

| Feature | Workflow Queue | Pipeline Editor |
|---------|---------------|-----------------|
| Complexity | Linear sequence of same-type workflows | DAG of different node types |
| Logic | None — just execute in order | ForEach, Conditional branching |
| Analysis | Manual (user selects tiles) | Automated (Threshold node detects objects) |
| Use case | Batch tile acquisition | Smart microscopy, adaptive acquisition |
