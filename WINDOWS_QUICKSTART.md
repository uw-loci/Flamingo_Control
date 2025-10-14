# Windows 11 Quick Start Guide

**For Flamingo Microscope Control on Windows 11**

---

## Prerequisites

- Windows 11 (also works on Windows 10)
- Python 3.8-3.11 installed
- Two network adapters (one for internet, one for microscope)
- Administrator access (for network configuration)

---

## Step 1: Network Configuration

### Check Your Network Adapters

```powershell
# Open PowerShell
Get-NetAdapter

# Check IP addresses
Get-NetIPAddress -AddressFamily IPv4
```

**Expected:** You should see two adapters:
- One with internet IP (e.g., 192.168.1.x)
- One for microscope (needs 10.129.37.x)

### Configure Microscope Network Adapter

**Option A: GUI Method (Easiest)**

1. Open **Settings** → **Network & Internet**
2. Click **Ethernet** or **Wi-Fi** (whichever connects to microscope)
3. Click **Edit** next to "IP assignment"
4. Select **Manual**
5. Enable **IPv4**
6. Enter:
   - **IP address:** `10.129.37.5`
   - **Subnet mask:** `255.255.255.0`
   - **Gateway:** Leave empty (isolated network)
   - **DNS:** Leave empty
7. Click **Save**

**Option B: PowerShell Method**

```powershell
# Run PowerShell as Administrator

# List adapters to find the correct name
Get-NetAdapter

# Set static IP (replace 'Ethernet 2' with your adapter name)
New-NetIPAddress -InterfaceAlias "Ethernet 2" -IPAddress 10.129.37.5 -PrefixLength 24

# Verify
Get-NetIPAddress -InterfaceAlias "Ethernet 2"
```

### Verify Routing

```powershell
# Check routing table
route print | findstr "10.129.37"

# Or use PowerShell
Get-NetRoute -DestinationPrefix "10.129.37.0/24"
```

**Expected output:**
```
Network Destination    Netmask          Gateway       Interface
10.129.37.0           255.255.255.0    On-link       10.129.37.5
```

---

## Step 2: Run Network Diagnostic

```powershell
# Navigate to project directory
cd C:\path\to\Flamingo_Control

# Run diagnostic script
.\check_network.ps1

# Or test specific microscope IP
.\check_network.ps1 10.129.37.22
```

**Expected:**
```
✓✓✓ EXCELLENT: Network configuration looks good!
```

---

## Step 3: Install Python Dependencies

```powershell
# Create virtual environment
python -m venv .venv

# Activate virtual environment
.venv\Scripts\activate

# Install dependencies
pip install -r requirements-minimal.txt
# Or manually:
pip install PyQt5 numpy
```

---

## Step 4: Launch Application

```powershell
# Make sure virtual environment is activated
.venv\Scripts\activate

# Set Python path and run (PowerShell)
$env:PYTHONPATH="src"
python -m py2flamingo

# Or specify IP/port directly
python -m py2flamingo --ip 10.129.37.22 --port 53717
```

**Alternative (Command Prompt):**
```cmd
REM Activate virtual environment
.venv\Scripts\activate

REM Set Python path
set PYTHONPATH=src

REM Run application
python -m py2flamingo
```

---

## Step 5: Test Connection

1. Application window opens
2. **Configuration section** shows available microscopes
3. Select microscope from dropdown (e.g., "n7")
4. IP and port auto-fill
5. Click **"Test Connection"** button
6. Should see: "✓ Connection test successful!"
7. Click **"Connect"**

---

## Common Commands (Windows)

### Network Diagnostics

```powershell
# Check network adapters
Get-NetAdapter | Where-Object {$_.Status -eq "Up"}

# Check IP addresses
Get-NetIPAddress -AddressFamily IPv4

# Check routing to microscope
Find-NetRoute -RemoteIPAddress 10.129.37.22

# Test connectivity
Test-Connection -ComputerName 10.129.37.22 -Count 4

# Check if port is open
Test-NetConnection -ComputerName 10.129.37.22 -Port 53717
```

### Firewall Configuration

```powershell
# Run as Administrator

# Add firewall rules for microscope subnet
New-NetFirewallRule -DisplayName "Flamingo Microscope In" `
    -Direction Inbound -LocalAddress 10.129.37.0/24 -Action Allow

New-NetFirewallRule -DisplayName "Flamingo Microscope Out" `
    -Direction Outbound -RemoteAddress 10.129.37.0/24 -Action Allow

# Check firewall status
Get-NetFirewallProfile
```

### Python Application

```powershell
# Run with configuration discovery
$env:PYTHONPATH="src"
python -m py2flamingo

# Run with specific settings
python -m py2flamingo --ip 10.129.37.22 --port 53717 --log-level DEBUG

# Test connection programmatically
python -c "from py2flamingo.controllers import ConnectionController; from py2flamingo.services import MVCConnectionService; from py2flamingo.models import ConnectionModel; from py2flamingo.core import TCPConnection, ProtocolEncoder; tcp = TCPConnection(); encoder = ProtocolEncoder(); model = ConnectionModel(); service = MVCConnectionService(tcp, encoder); controller = ConnectionController(service, model); success, msg = controller.test_connection('10.129.37.22', 53717); print(f'Success: {success}'); print(f'Message: {msg}')"
```

---

## Troubleshooting

### Issue 1: "Cannot connect to microscope"

**Check:**
```powershell
# 1. Verify adapter has correct IP
Get-NetIPAddress | Where-Object {$_.IPAddress -like "10.129.37.*"}

# 2. Verify routing
Get-NetRoute -DestinationPrefix "10.129.37.0/24"

# 3. Test connectivity
Test-NetConnection -ComputerName 10.129.37.22 -Port 53717

# 4. Run diagnostic
.\check_network.ps1 10.129.37.22
```

### Issue 2: "Permission denied" when configuring network

**Solution:** Run PowerShell as Administrator
- Right-click PowerShell
- Select "Run as Administrator"

### Issue 3: Firewall blocking connection

**Solution:**
```powershell
# Temporarily disable firewall for testing (Administrator)
Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled False

# Test connection
# If works, re-enable and add specific rules
Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled True
.\check_network.ps1
```

### Issue 4: Python not found

**Solution:**
```powershell
# Verify Python installation
python --version

# Check Python is in PATH
$env:PATH -split ";" | Select-String "Python"

# If not found, add to PATH or use full path
C:\Users\YourName\AppData\Local\Programs\Python\Python39\python.exe -m py2flamingo
```

---

## File Paths (Windows Style)

All documentation uses forward slashes `/` but Windows uses backslashes `\`:

**Convert:**
- `microscope_settings/FlamingoMetaData.txt` → `microscope_settings\FlamingoMetaData.txt`
- `src/py2flamingo` → `src\py2flamingo`

**PowerShell is flexible** - Both work:
```powershell
python src/py2flamingo/__main__.py  # Works
python src\py2flamingo\__main__.py  # Also works
```

---

## Configuration Files Location

```
C:\path\to\Flamingo_Control\
├── microscope_settings\
│   ├── FlamingoMetaData.txt        ← Main config
│   ├── FlamingoMetaData_WID.txt    ← Alternative config
│   └── FlamingoMetaData_test.txt   ← Test config
├── src\
│   └── py2flamingo\
└── check_network.ps1               ← Diagnostic script
```

---

## Summary Checklist

- [ ] Two network adapters available
- [ ] Microscope adapter configured with 10.129.37.5/24
- [ ] Routing table includes 10.129.37.0/24 route
- [ ] `check_network.ps1` shows "EXCELLENT"
- [ ] Python 3.8-3.11 installed
- [ ] Virtual environment created and activated
- [ ] Dependencies installed (PyQt5, numpy)
- [ ] Configuration files in `microscope_settings\`
- [ ] Application launches successfully
- [ ] "Test Connection" succeeds

---

## Quick Command Reference

```powershell
# Network setup (Administrator)
New-NetIPAddress -InterfaceAlias "Ethernet 2" -IPAddress 10.129.37.5 -PrefixLength 24

# Network check
.\check_network.ps1

# Python environment
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-minimal.txt

# Run application
$env:PYTHONPATH="src"
python -m py2flamingo

# Test connectivity
Test-NetConnection -ComputerName 10.129.37.22 -Port 53717
```

---

## Getting Help

1. **Network issues:** See `NETWORK_CONFIGURATION.md` (Windows section)
2. **Application issues:** See `README_MVC.md`
3. **Quick reference:** See `MVC_QUICKSTART.md`

---

**Last Updated:** 2025-10-14
**Platform:** Windows 11 (compatible with Windows 10)
**Python:** 3.8-3.11
