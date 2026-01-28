# Flamingo Microscope Control

Control software for Flamingo light sheet microscope systems. Communicates with microscope over TCP/IP, manages acquisition workflows, and displays images via PyQt5 GUI or Napari integration.

## About the Flamingo Microscope

The Flamingo light sheet microscope was originally designed and developed by the [Huisken Lab](https://huiskenlab.com/flamingo/). This repository provides a Python-based control system for Flamingo microscopes, building upon the hardware and software architecture developed by the Huisken Lab. For more information about the microscope design, sample data, and scientific applications, visit the [Huisken Lab Flamingo page](https://huiskenlab.com/flamingo/).

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

## Features

### Configuration Management
- **Auto-discovery** of microscope configurations
- **Dropdown selector** for easy microscope switching
- **Connection testing** before establishing full connection
- **Settings display** (microscope name, IP, port, laser power, etc.)

### MVC Architecture
- **Clean separation** of concerns (Model-View-Controller)
- **Type-safe** code with full type hints
- **Comprehensive testing** (400+ tests, 95%+ coverage)
- **Observable pattern** for reactive UI updates
- **Dependency injection** for easy testing

### Dual Network Support
- **Internet + microscope subnet** configuration
- **Automatic routing** through correct network interface
- **Built-in diagnostics** (`check_network.sh` / `check_network.ps1`)
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

---

## Documentation

- **[INSTALLATION.md](INSTALLATION.md)** - Complete installation guide for all platforms
- **[DEVELOPER.md](docs/DEVELOPER.md)** - Developer guide, architecture, testing
- **[CLAUDE.md](docs/CLAUDE.md)** - AI assistant guidance for code work
- **[LED 2D Overview Guide](docs/led_2d_overview.md)** - Sample orientation scanning

---

## Platform Support

| Platform | Status | Installation Guide |
|----------|--------|-------------------|
| **Windows 10/11** | ✅ Fully Supported | [INSTALLATION.md](INSTALLATION.md#network-configuration-windows) |
| **Linux** (Ubuntu 20.04+, Fedora 35+) | ✅ Fully Supported | [INSTALLATION.md](INSTALLATION.md#network-configuration-linux) |
| **macOS** (10.15+) | ✅ Fully Supported | [INSTALLATION.md](INSTALLATION.md#network-configuration-macos) |

**Python Versions:** 3.8, 3.9, 3.10, 3.11

---

## Requirements

### Software
- Python 3.8-3.11
- PyQt5, NumPy (installed via requirements)
- Optional: Napari (for viewer integration)

### Hardware
- Network connection to microscope (or mock server for testing)
- For dual-network setups: Two network adapters

### Configuration Files
- `microscope_settings/FlamingoMetaData.txt` (or other config files)
- `workflows/*.txt` (workflow template files)

---

## Usage

### Basic Usage

1. **Launch application:**
   ```bash
   PYTHONPATH=src python -m py2flamingo
   ```

2. **Select microscope** from configuration dropdown

3. **Test connection** (optional but recommended)

4. **Connect** to microscope

5. **Load/send workflows** via GUI

### Command-Line Options

```bash
# Specify IP and port directly
python -m py2flamingo --ip 10.129.37.22 --port 53717

# Enable debug logging
python -m py2flamingo --log-level DEBUG

# Legacy standalone mode (no Napari)
python -m py2flamingo --mode standalone

# Napari integration mode
python -m py2flamingo --mode napari
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

---

## Network Configuration

### Dual-Network Setup

Most deployments use two network interfaces:
1. **Internet connection** (192.168.x.x or similar)
2. **Microscope subnet** (10.129.37.0/24)

The operating system automatically routes traffic based on destination IP.

### Quick Network Check

**Windows:**
```powershell
.\check_network.ps1
```

**Linux/macOS:**
```bash
./check_network.sh
```

**Expected result:** ✓✓✓ EXCELLENT

### Configuration Steps

See **[INSTALLATION.md](INSTALLATION.md#network-configuration)** for detailed platform-specific network configuration instructions.

---

## Testing Without Hardware

Use the mock server to test without a microscope:

### Start Mock Server

```bash
# Terminal 1
python mock_server.py
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

---

## Architecture

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

See **[DEVELOPER.md](DEVELOPER.md#architecture-overview)** for detailed architecture documentation.

---

## Development

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

See **[DEVELOPER.md](DEVELOPER.md#contributing)** for detailed contribution guidelines.

---

## Troubleshooting

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
.\check_network.ps1  # Windows
./check_network.sh   # Linux/macOS
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

See **[INSTALLATION.md](INSTALLATION.md#troubleshooting)** for comprehensive troubleshooting guide.

---

## Project Structure

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
├── microscope_settings/         # Microscope configurations
├── workflows/                   # Workflow template files
│
├── check_network.sh             # Network diagnostic (Linux/macOS)
├── check_network.ps1            # Network diagnostic (Windows)
├── mock_server.py               # Mock microscope for testing
│
├── README.md                    # This file
├── INSTALLATION.md              # Installation guide
├── DEVELOPER.md                 # Developer guide
└── CLAUDE.md                    # AI assistant guidance
```

---

## Version History

### Latest (2025-10-14)
- ✅ Configuration management with auto-discovery
- ✅ Connection testing before connecting
- ✅ Comprehensive network diagnostics
- ✅ Full Windows 11 support
- ✅ MVC architecture refactoring complete
- ✅ 400+ tests with 95%+ coverage

### Earlier
- MVC interface implementation
- Napari integration
- Mock server for testing
- Legacy interface (still available)

---

## Requirements

### Microscope Side
- Flamingo firmware v2.16.2
- Network access (Morgridge network or VPN)

### Control Computer
- Python 3.8-3.11
- Network connection to microscope subnet
- PyQt5 for GUI
- NumPy for image processing
- Optional: Napari for advanced visualization

---

## Getting Help

- **Installation Issues:** See [INSTALLATION.md](INSTALLATION.md)
- **Development Questions:** See [DEVELOPER.md](docs/DEVELOPER.md)
- **Bug Reports:** [GitHub Issues](https://github.com/uw-loci/Flamingo_Control/issues)
- **Network Problems:** Run diagnostic script (`check_network.sh` / `check_network.ps1`)

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
**Last Updated:** 2025-10-14
**Status:** Production Ready
