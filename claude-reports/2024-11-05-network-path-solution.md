# Network Path Solution for Sample Information

## Problem

The original sample information view assumed the Python client would save the data, but in reality:
- Python client runs on PC at `192.168.1.2`
- Microscope control software runs on a different PC
- **Microscope saves the data**, not the Python software
- Paths need to be from the microscope's perspective (network UNC paths)

## Solution Implemented

The `SampleInfoView` has been redesigned to handle network paths properly with a three-component approach:

### 1. Network Share Base (Required)
**What:** The base UNC path that the microscope PC can access
**Format:** `\\192.168.1.2\CTLSM1`
**Purpose:** This is what the microscope will use to access the network share

### 2. Local Mount Point (Optional)
**What:** Where the network share is mounted on your local PC
**Format:** `D:\microscope_data` or similar
**Purpose:** Allows you to browse and create directories locally

### 3. Subdirectory (Optional)
**What:** Relative path appended to the base
**Format:** `data/experiment1`
**Purpose:** Organize data within the network share

## How It Works

The UI now has three sections:

### Network Share Configuration
```
Network Share Base:    \\192.168.1.2\CTLSM1
Local Mount Point:     D:\microscope_data  (optional)
```

### Data Subdirectory
```
Subdirectory:          data/sample1
```

### Path Display
The view shows **both** paths in real-time:
- **Network Path** (green box): What gets sent to microscope
  - `\\192.168.1.2\CTLSM1\data\sample1`
- **Local Path** (gray text): Local equivalent for verification
  - `D:\microscope_data\data\sample1 (✓ exists)`

## Usage Examples

### Example 1: Basic Setup
```
Network Share Base:  \\192.168.1.2\CTLSM1
Subdirectory:        data
Result sent to microscope: \\192.168.1.2\CTLSM1\data
```

### Example 2: With Local Mount
```
Network Share Base:  \\192.168.1.2\CTLSM1
Local Mount Point:   D:\microscope_data
Subdirectory:        experiments/2025-01-15/sample001
Result sent to microscope: \\192.168.1.2\CTLSM1\experiments\2025-01-15\sample001
Local equivalent:    D:\microscope_data\experiments\2025-01-15\sample001
```

### Example 3: Direct Network Path (No Local Mount)
```
Network Share Base:  \\192.168.1.2\CTLSM1
Subdirectory:        data/quick_test
Result sent to microscope: \\192.168.1.2\CTLSM1\data\quick_test
```

## Key Features

### 1. Dual Path Display
- Always shows the network path that will be sent to microscope (primary)
- Shows local equivalent if mount point configured (secondary)
- Real-time validation: green ✓ if exists, orange ⚠ if needs creation

### 2. Smart Browsing
- **Without mount point:** Type subdirectory directly
- **With mount point:** Use browse button to navigate local filesystem
- Automatically converts absolute local path to relative subdirectory

### 3. Directory Creation
- Only works if local mount point is configured
- Creates directory locally (which creates it on network share)
- Shows both local and network paths in confirmation

### 4. Path Translation
- Input uses forward slashes for convenience: `data/experiment1`
- Automatically converts to Windows UNC format: `\\192.168.1.2\CTLSM1\data\experiment1`
- Handles all path separator edge cases

## API for Integration

The view provides these methods for acquisition services:

```python
# Get the full network path to send to microscope
network_path = sample_info_view.get_network_path()
# Returns: "\\192.168.1.2\CTLSM1\data\sample1"

# Get the sample name
sample_name = sample_info_view.get_sample_name()
# Returns: "Sample_001"

# Get just the subdirectory (relative)
subdir = sample_info_view.get_save_path()
# Returns: "data/sample1"

# Get local path (if configured)
local_path = sample_info_view.get_local_path()
# Returns: "D:\microscope_data\data\sample1" or None
```

## Configuration Persistence

The network share base and local mount point should be saved in configuration:

```python
# In application initialization
sample_info_view.set_network_share_base(r"\\192.168.1.2\CTLSM1")
sample_info_view.set_local_mount_point(r"D:\microscope_data")
```

## Common Scenarios

### Scenario 1: Different Network Shares
If you have multiple microscopes with different shares:
- Save configurations per microscope
- Update network share base when switching microscopes

### Scenario 2: No Local Access
If the network share is not mounted locally:
- Leave "Local Mount Point" empty
- Type subdirectories directly
- "Create Directory" button will be disabled
- Create directories on the network share manually

### Scenario 3: Mapped Drive Letter
If the network share is mapped to a drive letter (e.g., `Z:\`):
- Use the drive letter as the local mount point: `Z:\`
- Network share base stays the same: `\\192.168.1.2\CTLSM1`
- Browse and create work as normal

## Notes

1. **UNC Path Format:** Always use double backslash format: `\\server\share`
2. **Forward Slashes:** You can type subdirectories with forward slashes (`data/exp1`), they'll be converted
3. **Path Validation:** Green means path exists locally, not on microscope - verify microscope can access
4. **Signal Emission:** `save_path_changed` signal emits the **full network path**, not just subdirectory

## Migration from Old Version

Old behavior:
```python
get_save_path()  # Returned: "G:\Github\...\data"
```

New behavior:
```python
get_network_path()  # Returns: "\\192.168.1.2\CTLSM1\data"
get_save_path()     # Returns: "data" (subdirectory only)
```

**Action Required:** Update any code that calls `get_save_path()` to use `get_network_path()` instead.
