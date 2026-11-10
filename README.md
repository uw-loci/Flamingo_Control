# Flamingo Microscope Control

Control software for Flamingo light sheet microscope systems. Communicates with microscope over TCP/IP, manages acquisition workflows, and displays images via PyQt5 GUI or Napari integration.

## About the Flamingo Microscope

The Flamingo light sheet microscope was originally designed and developed by the [Huisken Lab](https://huiskenlab.com/flamingo/). This repository provides a Python-based control system for Flamingo microscopes, building upon the hardware and software architecture developed by the Huisken Lab. For more information about the microscope design, sample data, and scientific applications, visit the [Huisken Lab Flamingo page](https://huiskenlab.com/flamingo/).

## Stitching tiled acquisitions: Flamingo Stitcher

To turn a folder of raw tiles into one stitched 3D volume, use **[Flamingo Stitcher](https://github.com/uw-loci/flamingo-stitcher)**. It runs on any Windows computer without the microscope or this control software: download the installer from its [Releases page](https://github.com/uw-loci/flamingo-stitcher/releases), and it updates itself. Its [README](https://github.com/uw-loci/flamingo-stitcher#readme) explains the settings and how to read a stitching log.

This application uses the same stitching code, under **Extensions > Tile Stitching...** and **Extensions > Tile Stitching (Single Workflow)...**.

---

## Quick Start

### Installation

```bash
# Create virtual environment
python -m venv .venv

# Activate (Linux/macOS)
source .venv/bin/activate

# Activate (Windows PowerShell)
.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Run Application

```bash
# Set Python path and run
export PYTHONPATH=src  # Linux/macOS
python -m py2flamingo

# Windows PowerShell
$env:PYTHONPATH="src"
python -m py2flamingo
```

The application will:
1. **Auto-discover** microscope configurations from `microscope_settings/`
2. **Display** available microscopes in dropdown menu
3. **Test connection** before connecting
4. **Load settings** from configuration files

---

## Documentation

- **[INSTALLATION.md](docs/INSTALLATION.md)** - Complete installation guide for all platforms
- **[DEVELOPER.md](docs/DEVELOPER.md)** - Developer guide, architecture, testing
- **[Headless Pipelines](docs/headless_pipelines.md)** - Running pipelines from the command line
- **[LED 2D Overview Guide](docs/led_2d_overview.md)** - Sample orientation scanning
- **[Pipeline JSON Format](docs/pipeline_json_format.md)** - Pipeline serialization reference (10 NodeTypes, port compatibility, examples)
- **[Pipeline AI Prompt](docs/pipeline_ai_prompt.md)** - AI-assisted pipeline generation
- **`py2flamingo-pipeline run <file.json>`** - Run a pipeline from the command line, no GUI required (installed automatically by `pip install -e .`). See **[docs/headless_pipelines.md](docs/headless_pipelines.md)** for the headless Python API.

The sections below are folded. Click a section's line to open it.

---

## Features

<details>
<summary>Configuration management, MVC architecture, dual network support, viewers, LED 2D Overview</summary>

### Configuration Management
- **Auto-discovery** of microscope configurations
- **Dropdown selector** for easy microscope switching
- **Connection testing** before establishing full connection
- **Settings display** (microscope name, IP, port, laser power, etc.)

### MVC Architecture
- **Clean separation** of concerns (Model-View-Controller)
- **Automated tests** (over 2,200) that run without hardware
- **Observable pattern** for reactive UI updates
- **Dependency injection** for easy testing

### Dual Network Support
- **Internet + microscope subnet** configuration
- **Automatic routing** through correct network interface
- **Built-in diagnostics** (`scripts/check_network.sh` / `scripts/check_network.ps1`)
- **Connection testing** to verify routing

### Viewer Integration
- **Standalone GUI** - PyQt5 interface in its own window
- **Napari integration** - Dock widget with Napari canvas display
- **Viewer abstraction** - Easy to add new viewers

### LED 2D Overview (Sample Orientation)
Quick scanning feature for sample orientation and tile selection:
- **Dual-rotation overview maps** at R and R+90 degrees
- **Multiple visualization types** - Best focus, EDF, min/max/mean projections
- **Interactive tile selection** from overview images
- **Direct workflow generation** for selected tiles
- See [LED 2D Overview Guide](docs/led_2d_overview.md) for details

</details>

## Requirements and Platform Support

<details>
<summary>Python 3.11 or newer on Windows 10/11, Linux or macOS; firmware v2.16.2 on the microscope</summary>

| Platform | Installation Guide |
|----------|-------------------|
| **Windows 10/11** | [INSTALLATION.md](docs/INSTALLATION.md#network-configuration-windows) |
| **Linux** (Ubuntu 20.04+, Fedora 35+) | [INSTALLATION.md](docs/INSTALLATION.md#network-configuration-linux) |
| **macOS** (10.15+) | [INSTALLATION.md](docs/INSTALLATION.md#network-configuration-macos) |

### Microscope Side
- Flamingo firmware v2.16.2
- Network access (Morgridge network or VPN)

### Control Computer
- Python 3.11 or newer
- Network connection to microscope subnet (for dual-network setups, two network adapters)
- PyQt5 and NumPy (installed from `requirements.txt`)
- Optional: Napari for advanced visualization
- Optional: Zarr for compressed 2D session save/load

### Configuration Files
- `microscope_settings/FlamingoMetaData.txt` (or other config files)
- `workflows/*.txt` (workflow template files)

</details>

## Usage

1. **Launch the application:** `PYTHONPATH=src python -m py2flamingo`
2. **Select a microscope** from the configuration dropdown.
3. **Test the connection** (optional but recommended).
4. **Connect** to the microscope.
5. **Load or send workflows** in the GUI.

<details>
<summary>Command-line options and microscope configuration files</summary>

### Command-Line Options

```bash
# Specify IP and port directly
python -m py2flamingo --ip 10.129.37.22 --port 53717

# Load a workflow file on startup
python -m py2flamingo --workflow workflows/ZStack.txt

# Enable debug logging
python -m py2flamingo --log-level DEBUG
```

### Configuration Files

The application auto-discovers microscope configurations from `microscope_settings/` directory:

**Example configuration file:**
```xml
<Instrument>
  <Type>
    Microscope name = n7
    Microscope address = 10.129.37.22 53717
  </Type>
</Instrument>
```

Multiple configurations are supported - create one file per microscope.

</details>

## Network Configuration

<details>
<summary>Dual-network setup and the network check scripts</summary>

### Dual-Network Setup

Most deployments use two network interfaces:
1. **Internet connection** (192.168.x.x or similar)
2. **Microscope subnet** (10.129.37.0/24)

The operating system automatically routes traffic based on destination IP.

### Quick Network Check

**Windows:**
```powershell
.\scripts\check_network.ps1
```

**Linux/macOS:**
```bash
./scripts/check_network.sh
```

**Expected result:** ✓✓✓ EXCELLENT

### Configuration Steps

See **[INSTALLATION.md](docs/INSTALLATION.md#network-configuration)** for detailed platform-specific network configuration instructions.

</details>

## Testing Without Hardware

<details>
<summary>Run the application against the mock microscope server</summary>

### Start Mock Server

```bash
# Terminal 1
python tests/mock_server.py
```

### Create Test Configuration

```bash
cat > microscope_settings/FlamingoMetaData_test.txt << 'EOF'
<Instrument>
  <Type>
    Microscope name = Mock Server
    Microscope address = 127.0.0.1 53717
  </Type>
</Instrument>
EOF
```

### Run Application

```bash
# Terminal 2
PYTHONPATH=src python -m py2flamingo
```

Select "Mock Server" from dropdown and test connection.

</details>

## Troubleshooting

<details>
<summary>Connection timeouts, network configuration, import errors, the application not starting</summary>

### Connection Issues

**"Connection timeout"**
```bash
# Windows
Test-NetConnection -ComputerName 10.129.37.22 -Port 53717

# Linux/macOS
nc -zv 10.129.37.22 53717
```

**"Network configuration"**
```bash
# Run diagnostic
.\scripts\check_network.ps1  # Windows
./scripts/check_network.sh   # Linux/macOS
```

### Import Errors

```bash
# Verify PYTHONPATH is set
echo $PYTHONPATH  # Linux/macOS
echo $env:PYTHONPATH  # Windows PowerShell

# Should show: src
```

### Application Won't Start

```bash
# Enable debug logging
python -m py2flamingo --log-level DEBUG
```

See **[INSTALLATION.md](docs/INSTALLATION.md#troubleshooting)** for comprehensive troubleshooting guide.

</details>

## Architecture

<details>
<summary>MVC layout and key components</summary>

### MVC Pattern

```
Application (py2flamingo/__main__.py)
    │
    ├── Models     (Data structures, state)
    ├── Views      (PyQt5 UI components)
    ├── Controllers (Business logic, coordination)
    │
    ├── Services   (Reusable business logic)
    │   ├── ConfigurationManager (Config discovery)
    │   ├── ConnectionService    (TCP management)
    │   └── WorkflowService      (Workflow processing)
    │
    └── Core       (Infrastructure)
        ├── TCPConnection     (Low-level sockets)
        ├── ProtocolEncoder   (Binary protocol)
        └── QueueManager      (Inter-thread communication)
```

### Key Components

- **Models:** `ConnectionModel`, `WorkflowModel`, `Position`, `Command`
- **Views:** `ConnectionView`, `WorkflowView`, `ViewerWidget`
- **Controllers:** `ConnectionController`, `WorkflowController`, `PositionController`
- **Services:** `ConfigurationManager`, `MVCConnectionService`, `MVCWorkflowService`

See **[DEVELOPER.md](docs/DEVELOPER.md#architecture-overview)** for detailed architecture documentation.

</details>

## Development

<details>
<summary>Running the tests and contributing</summary>

### Running Tests

```bash
# All unit tests
PYTHONPATH=src pytest tests/ --ignore=tests/integration -v

# Specific test
PYTHONPATH=src pytest tests/test_services.py -v

# With coverage
PYTHONPATH=src pytest tests/ --cov=src/py2flamingo --cov-report=html
```

### Contributing

1. Fork repository
2. Create feature branch
3. Write tests for new functionality
4. Ensure tests pass
5. Submit pull request

See **[DEVELOPER.md](docs/DEVELOPER.md#contributing)** for detailed contribution guidelines.

</details>

## Project Structure

<details>
<summary>Where things are in the repository</summary>

```
Flamingo_Control/
├── src/
│   └── py2flamingo/
│       ├── __main__.py          # Application entry point
│       ├── models/              # Data structures
│       ├── views/               # UI components (PyQt5)
│       ├── controllers/         # Business logic
│       ├── services/            # Reusable services
│       ├── core/                # TCP, protocol, infrastructure
│       └── utils/               # Utilities (parsers, etc.)
│
├── tests/                       # Unit and integration tests
│   └── mock_server.py           # Mock microscope for testing
├── microscope_settings/         # Microscope configurations
├── workflows/                   # Workflow template files
├── scripts/
│   ├── check_network.sh         # Network diagnostic (Linux/macOS)
│   └── check_network.ps1        # Network diagnostic (Windows)
├── docs/
│   ├── INSTALLATION.md          # Installation guide
│   └── DEVELOPER.md             # Developer guide
└── README.md                    # This file
```

</details>

## Version History

<details>
<summary>2026-02 and earlier</summary>

### 2026-02
- Zarr-based 2D session save/load for MIP and LED 2D Overview (compressed, backward-compatible TIFF loading)
- Fix multi-laser mode ignoring user setting in tile collection workflows
- Deferred 3D viewer setup — Sample View window appears ~5s faster
- Clear Data now unchecks channel visibility checkboxes

### 2025-10-14
- ✅ Configuration management with auto-discovery
- ✅ Connection testing before connecting
- ✅ Comprehensive network diagnostics
- ✅ Full Windows 11 support
- ✅ MVC architecture refactoring complete

### Earlier
- MVC interface implementation
- Napari integration
- Mock server for testing
- Legacy interface (still available)

</details>

---

## Getting Help

- **Installation Issues:** See [INSTALLATION.md](docs/INSTALLATION.md)
- **Development Questions:** See [DEVELOPER.md](docs/DEVELOPER.md)
- **Bug Reports:** [GitHub Issues](https://github.com/uw-loci/Flamingo_Control/issues)
- **Network Problems:** Run the diagnostic script (`scripts/check_network.sh` / `scripts/check_network.ps1`)

---

## License

See LICENSE file for details.

## Citation

If you use Flamingo Control in your work, please cite the Huisken Lab resources and this repository.

---

## Acknowledgments

Developed at the University of Wisconsin Laboratory for Optical and Computational Instrumentation (LOCI) for Flamingo light sheet microscope systems.

---

**Repository:** https://github.com/uw-loci/Flamingo_Control
**Last Updated:** 2026-11-10
**Status:** Production Ready
