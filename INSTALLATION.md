# Installation Guide

Complete installation guide for Flamingo Microscope Control software on Windows, Linux, and macOS.

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Python Environment Setup](#python-environment-setup)
- [Network Configuration](#network-configuration)
  - [Windows](#network-configuration-windows)
  - [Linux](#network-configuration-linux)
  - [macOS](#network-configuration-macos)
- [Installation](#installation)
- [Verification](#verification)
- [Troubleshooting](#troubleshooting)

---

## Prerequisites

### All Platforms
- **Python:** 3.8-3.11 (Python 3.12+ not yet tested)
- **Network:** Ethernet connection to microscope network
- **Required Files:** `microscope_settings/` directory with configuration files

### Windows Specific
- Windows 10 or Windows 11
- Administrator access (for network configuration)
- PowerShell 5.1+ (included with Windows)

### Linux Specific
- Modern distribution (Ubuntu 20.04+, Fedora 35+, etc.)
- sudo access (for network configuration)
- NetworkManager or netplan

### macOS Specific
- macOS 10.15 (Catalina) or newer
- Administrator access

---

## Quick Start

### Windows (PowerShell)
```powershell
# 1. Network setup (run as Administrator)
.\check_network.ps1

# 2. Python environment
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 3. Run application
$env:PYTHONPATH="src"
python -m py2flamingo
```

### Linux/macOS (Bash)
```bash
# 1. Network setup
./check_network.sh

# 2. Python environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Run application
PYTHONPATH=src python -m py2flamingo
```

---

## Python Environment Setup

### 1. Verify Python Installation

**Windows:**
```powershell
python --version
# Should show: Python 3.8.x through 3.11.x
```

**Linux/macOS:**
```bash
python3 --version
# Should show: Python 3.8.x through 3.11.x
```

### 2. Create Virtual Environment

**Windows:**
```powershell
python -m venv .venv
```

**Linux/macOS:**
```bash
python3 -m venv .venv
```

### 3. Activate Virtual Environment

**Windows (PowerShell):**
```powershell
.venv\Scripts\activate
```

**Windows (Command Prompt):**
```cmd
.venv\Scripts\activate.bat
```

**Linux/macOS:**
```bash
source .venv/bin/activate
```

### 4. Install Dependencies

```bash
# Standard installation (recommended)
pip install -r requirements.txt

# Minimal installation (MVC interface only, limited functionality)
pip install -r requirements-minimal.txt

# Optional: Napari integration
pip install napari
```

**Note:** The standard `requirements.txt` includes all dependencies needed for full functionality:
- PyQt5 (GUI framework)
- numpy (numerical computing)
- Pillow (image processing)
- scipy (scientific computing)
- scikit-learn (machine learning utilities)

---

## Network Configuration

Most installations require dual-network setup:
1. **Internet Connection** - For updates, software downloads
2. **Microscope Subnet** - Isolated network for microscope (10.129.37.0/24)

The operating system automatically routes traffic based on destination IP.

### Network Architecture
```
┌─────────────────┐
│  Your Computer  │
│                 │
│  ┌───────────┐  │         ┌──────────┐
│  │Interface 1│──┼────────→│ Internet │
│  │(eth0/WiFi)│  │         └──────────┘
│  └───────────┘  │
│                 │
│  ┌───────────┐  │         ┌─────────────────────┐
│  │Interface 2│──┼────────→│ Microscope Subnet   │
│  │   (eth1)  │  │         │ 10.129.37.0/24      │
│  └───────────┘  │         │                     │
└─────────────────┘         │ ┌─────────────────┐ │
                            │ │ Microscope(s)   │ │
                            │ │ 10.129.37.x     │ │
                            │ └─────────────────┘ │
                            └─────────────────────┘
```

---

## Network Configuration: Windows

### Option 1: GUI Method (Recommended)

1. Open **Settings** → **Network & Internet**
2. Click your microscope network adapter (usually **Ethernet 2**)
3. Click **Edit** next to "IP assignment"
4. Select **Manual**
5. Enable **IPv4**
6. Enter settings:
   - **IP address:** `10.129.37.5`
   - **Subnet mask:** `255.255.255.0`
   - **Gateway:** Leave empty
   - **DNS:** Leave empty
7. Click **Save**

### Option 2: PowerShell Method

```powershell
# Run PowerShell as Administrator

# List network adapters
Get-NetAdapter

# Configure static IP (replace 'Ethernet 2' with your adapter name)
New-NetIPAddress -InterfaceAlias "Ethernet 2" `
                 -IPAddress 10.129.37.5 `
                 -PrefixLength 24

# Verify configuration
Get-NetIPAddress -InterfaceAlias "Ethernet 2"
```

### Verify Windows Network Setup

```powershell
# Check routing table
Get-NetRoute -DestinationPrefix "10.129.37.0/24"

# Or use route command
route print | findstr "10.129.37"

# Run diagnostic script
.\check_network.ps1
```

**Expected output:**
```
Network Destination    Netmask          Gateway       Interface
10.129.37.0           255.255.255.0    On-link       10.129.37.5
```

### Windows Firewall Configuration

```powershell
# Run as Administrator

# Add firewall rules for microscope subnet
New-NetFirewallRule -DisplayName "Flamingo Microscope In" `
    -Direction Inbound `
    -LocalAddress 10.129.37.0/24 `
    -Action Allow

New-NetFirewallRule -DisplayName "Flamingo Microscope Out" `
    -Direction Outbound `
    -RemoteAddress 10.129.37.0/24 `
    -Action Allow
```

---

## Network Configuration: Linux

### Method 1: NetworkManager (Ubuntu/Fedora Desktop)

```bash
# List connections
nmcli connection show

# Configure microscope interface (replace 'eth1' with your interface)
nmcli connection modify eth1 ipv4.addresses 10.129.37.5/24
nmcli connection modify eth1 ipv4.method manual
nmcli connection modify eth1 ipv4.never-default yes
nmcli connection up eth1
```

### Method 2: Netplan (Ubuntu Server)

Edit `/etc/netplan/01-netcfg.yaml`:

```yaml
network:
  version: 2
  renderer: networkd
  ethernets:
    eth0:  # Internet interface
      dhcp4: yes

    eth1:  # Microscope interface
      dhcp4: no
      addresses:
        - 10.129.37.5/24
      # No gateway - isolated network
```

Apply configuration:
```bash
sudo netplan apply
```

### Method 3: /etc/network/interfaces (Debian/Older Systems)

Edit `/etc/network/interfaces`:

```
auto eth1
iface eth1 inet static
    address 10.129.37.5
    netmask 255.255.255.0
    # No gateway - isolated network
```

Restart networking:
```bash
sudo systemctl restart networking
```

### Verify Linux Network Setup

```bash
# Check interfaces
ip addr show

# Check routing
ip route show | grep 10.129.37

# Expected output:
# 10.129.37.0/24 dev eth1 proto kernel scope link src 10.129.37.5

# Test connectivity
ping -c 4 10.129.37.22

# Run diagnostic script
./check_network.sh
```

### Linux Firewall (UFW)

```bash
# Allow microscope subnet traffic
sudo ufw allow from 10.129.37.0/24
sudo ufw allow to 10.129.37.0/24

# Check status
sudo ufw status
```

---

## Network Configuration: macOS

### GUI Method

1. Open **System Preferences** → **Network**
2. Select your microscope network adapter
3. Click **Configure IPv4** → **Manually**
4. Enter settings:
   - **IP Address:** `10.129.37.5`
   - **Subnet Mask:** `255.255.255.0`
   - **Router:** Leave empty
5. Click **Apply**

### Command Line Method

```bash
# List interfaces
networksetup -listallnetworkservices

# Configure interface (replace 'Ethernet 2' with your service name)
sudo networksetup -setmanual "Ethernet 2" 10.129.37.5 255.255.255.0

# Verify
ifconfig | grep -A 3 "inet 10.129.37"
```

### Verify macOS Network Setup

```bash
# Check routing
netstat -rn | grep 10.129.37

# Test connectivity
ping -c 4 10.129.37.22

# Run diagnostic script
./check_network.sh
```

---

## Installation

### 1. Clone or Download Repository

```bash
git clone https://github.com/uw-loci/Flamingo_Control.git
cd Flamingo_Control
```

### 2. Set Up Python Environment

Follow [Python Environment Setup](#python-environment-setup) section above.

### 3. Verify Configuration Files

Check that you have microscope configuration files:

**Windows:**
```powershell
dir microscope_settings\*.txt
```

**Linux/macOS:**
```bash
ls -l microscope_settings/*.txt
```

You should see files like:
- `FlamingoMetaData.txt` (main configuration)
- `FlamingoMetaData_WID.txt`, etc. (additional microscopes)

### 4. Run Application

**Windows:**
```powershell
$env:PYTHONPATH="src"
python -m py2flamingo
```

**Linux/macOS:**
```bash
PYTHONPATH=src python -m py2flamingo
```

**With command-line options:**
```bash
# Specify IP and port directly
python -m py2flamingo --ip 10.129.37.22 --port 53717

# Enable debug logging
python -m py2flamingo --log-level DEBUG

# Legacy standalone mode (no Napari)
python -m py2flamingo --mode standalone
```

---

## Verification

### 1. Network Diagnostic

**Windows:**
```powershell
.\check_network.ps1
```

**Linux/macOS:**
```bash
./check_network.sh
```

**Expected result:** ✓✓✓ EXCELLENT

### 2. Test Connection in Application

1. Launch application
2. Select microscope from **Configuration** dropdown
3. Click **Test Connection** button
4. Should display: "✓ Connection test successful!"

### 3. Test Python Imports

```python
# Test core imports
python -c "from py2flamingo.services import ConfigurationManager; print('✓ Services OK')"

python -c "from py2flamingo.controllers import ConnectionController; print('✓ Controllers OK')"

python -c "from py2flamingo.core import TCPConnection; print('✓ Core OK')"
```

### 4. Run Test Suite (Optional)

```bash
# Install test dependencies
pip install pytest pytest-cov

# Run tests
PYTHONPATH=src pytest tests/ --ignore=tests/integration -v
```

---

## Troubleshooting

### Connection Issues

#### Issue: "Connection timeout"

**Symptoms:**
- "Connection timeout. Server at X.X.X.X:XXXX is not responding"
- Test connection fails

**Solutions:**

**Windows:**
```powershell
# Check interface is up
Get-NetAdapter | Where-Object {$_.Status -eq "Up"}

# Check IP is assigned
Get-NetIPAddress | Where-Object {$_.IPAddress -like "10.129.37.*"}

# Check routing
Get-NetRoute -DestinationPrefix "10.129.37.0/24"

# Test port connectivity
Test-NetConnection -ComputerName 10.129.37.22 -Port 53717
```

**Linux/macOS:**
```bash
# Check interface
ip addr show | grep 10.129.37

# Check routing
ip route get 10.129.37.22

# Test connectivity
ping -c 4 10.129.37.22
nc -zv 10.129.37.22 53717  # Test port
```

#### Issue: "Connection refused"

**Cause:** Firewall blocking connection or server not running

**Solutions:**
- Check firewall rules (see firewall sections above)
- Verify microscope server is running
- Temporarily disable firewall for testing

#### Issue: Traffic goes through wrong interface

**Diagnosis:**

**Windows:**
```powershell
Find-NetRoute -RemoteIPAddress 10.129.37.22
# Should show your microscope interface, not internet interface
```

**Linux:**
```bash
ip route get 10.129.37.22
# Should show: "10.129.37.22 dev eth1 src 10.129.37.5"
# NOT: "10.129.37.22 via 192.168.1.1 dev eth0"
```

**Solution:** Verify microscope interface has correct IP (10.129.37.x)

### Python Issues

#### Issue: "python: command not found"

**Windows:**
```powershell
# Check Python installation
where.exe python

# Add to PATH or use full path
C:\Users\YourName\AppData\Local\Programs\Python\Python39\python.exe
```

**Linux/macOS:**
```bash
# Try python3 instead
python3 --version

# Or install Python
# Ubuntu/Debian:
sudo apt install python3 python3-pip python3-venv

# Fedora:
sudo dnf install python3 python3-pip

# macOS:
brew install python@3.11
```

#### Issue: "ModuleNotFoundError"

**Solution:**
```bash
# Verify PYTHONPATH is set
echo $PYTHONPATH  # Linux/macOS
echo $env:PYTHONPATH  # Windows PowerShell

# Should show: src or /full/path/to/Flamingo_Control/src

# If not set:
export PYTHONPATH=src  # Linux/macOS
$env:PYTHONPATH="src"  # Windows PowerShell
```

#### Issue: "Permission denied" during installation

**Linux/macOS:**
```bash
# Don't use sudo with pip in virtual environment
# Deactivate, delete .venv, and recreate:
deactivate
rm -rf .venv
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-minimal.txt
```

### Platform-Specific Issues

#### Windows: "PowerShell execution policy"

```powershell
# Check current policy
Get-ExecutionPolicy

# Set policy to allow scripts
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser

# Or run script explicitly
powershell -ExecutionPolicy Bypass -File .\check_network.ps1
```

#### Linux: "NetworkManager not managing interface"

```bash
# Check if NetworkManager controls interface
nmcli device status

# If showing "unmanaged":
# Edit /etc/NetworkManager/NetworkManager.conf
# Remove interface from [keyfile] unmanaged-devices

# Restart NetworkManager
sudo systemctl restart NetworkManager
```

#### macOS: "Operation not permitted"

```bash
# macOS requires admin for network changes
sudo networksetup -setmanual "Ethernet 2" 10.129.37.5 255.255.255.0
```

---

## Configuration Files

The application discovers microscope configurations from `microscope_settings/` directory.

### Configuration File Format

```xml
<Instrument>
  <Type>
    Microscope name = n7
    Microscope address = 10.129.37.22 53717
  </Type>
</Instrument>
```

### Multiple Microscopes

Create one file per microscope:
- `microscope_settings/n7_config.txt`
- `microscope_settings/zion_config.txt`
- `microscope_settings/elsa_config.txt`

The application will auto-discover all configurations and display them in a dropdown menu.

---

## Command Reference

### Windows (PowerShell)

```powershell
# Network
Get-NetAdapter
Get-NetIPAddress
Get-NetRoute -DestinationPrefix "10.129.37.0/24"
Test-NetConnection -ComputerName 10.129.37.22 -Port 53717

# Python environment
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-minimal.txt

# Run application
$env:PYTHONPATH="src"
python -m py2flamingo
```

### Linux/macOS (Bash)

```bash
# Network
ip addr show
ip route show
ip route get 10.129.37.22
ping -c 4 10.129.37.22

# Python environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-minimal.txt

# Run application
PYTHONPATH=src python -m py2flamingo
```

---

## Getting Help

- **Network issues:** Run diagnostic script (`check_network.ps1` or `check_network.sh`)
- **Python issues:** Check Python version, virtual environment activation
- **Application issues:** Enable debug logging: `python -m py2flamingo --log-level DEBUG`
- **Bug reports:** https://github.com/uw-loci/Flamingo_Control/issues

---

**Last Updated:** 2025-10-14
**Supported Platforms:** Windows 10/11, Linux (Ubuntu 20.04+, Fedora 35+), macOS 10.15+
**Python Versions:** 3.8, 3.9, 3.10, 3.11
