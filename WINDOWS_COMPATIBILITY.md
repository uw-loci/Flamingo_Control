# Windows Compatibility Summary

**Platform:** Windows 11 (also compatible with Windows 10)
**Status:** ✅ Fully Compatible
**Last Updated:** 2025-10-14

---

## Windows-Specific Files Created

### 1. check_network.ps1
**Type:** PowerShell diagnostic script
**Purpose:** Network configuration verification
**Usage:**
```powershell
.\check_network.ps1
.\check_network.ps1 10.129.37.22
```

**Features:**
- Checks network adapters and IPs
- Verifies routing table
- Tests connectivity (ping + TCP port)
- Checks firewall status
- Provides actionable recommendations
- **Windows-native:** Uses PowerShell cmdlets

### 2. WINDOWS_QUICKSTART.md
**Type:** Documentation
**Purpose:** Complete Windows setup guide
**Contents:**
- Network configuration (GUI + PowerShell methods)
- Python environment setup
- Application launch commands
- Troubleshooting for Windows-specific issues
- PowerShell command reference

---

## Command Format Compatibility

### All Commands Have Windows Equivalents

| Task | Linux/macOS | Windows PowerShell | Windows CMD |
|------|-------------|-------------------|-------------|
| **Network Diagnostic** | `./check_network.sh` | `.\check_network.ps1` | N/A |
| **Set Python Path** | `PYTHONPATH=src` | `$env:PYTHONPATH="src"` | `set PYTHONPATH=src` |
| **Run Application** | `python -m py2flamingo` | `python -m py2flamingo` | `python -m py2flamingo` |
| **Activate venv** | `source .venv/bin/activate` | `.venv\Scripts\activate` | `.venv\Scripts\activate` |
| **Check Adapters** | `ip addr show` | `Get-NetAdapter` | `ipconfig` |
| **Check Routes** | `ip route show` | `Get-NetRoute` | `route print` |
| **Test Connection** | `nc -z IP PORT` | `Test-NetConnection` | `telnet IP PORT` |

### File Path Compatibility

Python and PowerShell both accept forward slashes:
```powershell
# Both work in PowerShell
python src/py2flamingo/__main__.py   # ✓
python src\py2flamingo\__main__.py   # ✓
```

---

## Network Configuration Methods

### Method 1: GUI (Easiest for Windows Users)

**Steps:**
1. Settings → Network & Internet
2. Select network adapter
3. Edit IP assignment → Manual
4. Enter: IP=10.129.37.5, Subnet=255.255.255.0
5. Save

**Pros:** No command line needed, visual confirmation
**Cons:** Slower for experienced users

### Method 2: PowerShell (Power Users)

```powershell
# Run as Administrator
New-NetIPAddress -InterfaceAlias "Ethernet 2" -IPAddress 10.129.37.5 -PrefixLength 24
```

**Pros:** Fast, scriptable, precise
**Cons:** Requires Administrator privileges

---

## Platform-Specific Considerations

### Routing

**Windows Behavior:**
- Automatic routing based on IP address
- More specific routes take precedence
- `route print` shows routing table
- Same behavior as Linux/macOS

**Example:**
```
Network Destination    Netmask          Gateway       Interface
10.129.37.0           255.255.255.0    On-link       10.129.37.5
0.0.0.0               0.0.0.0          192.168.1.1   192.168.1.100
```

**Traffic to 10.129.37.x automatically uses 10.129.37.5 interface** ✓

### Firewall

**Windows Firewall:**
- May block connections by default
- Easy to configure via PowerShell
- Profile-based (Domain, Public, Private)

**Configuration:**
```powershell
# Add firewall rules (Administrator)
New-NetFirewallRule -DisplayName "Flamingo Microscope" `
    -Direction Inbound -LocalAddress 10.129.37.0/24 -Action Allow
```

### Python

**Python on Windows:**
- Uses `python` command (not `python3`)
- Virtual environment: `.venv\Scripts\activate`
- Same Python code works on all platforms

---

## Documentation Coverage

### Windows-Specific Documentation

1. **WINDOWS_QUICKSTART.md** (Primary)
   - Complete Windows setup
   - PowerShell commands
   - GUI instructions
   - Troubleshooting

2. **NETWORK_CONFIGURATION.md** (Windows Section)
   - Detailed network setup
   - Routing explanation
   - Advanced configuration

3. **README.md** (Platform-Agnostic)
   - Shows both Linux and Windows commands
   - Links to platform-specific guides

4. **check_network.ps1** (Diagnostic Tool)
   - Native PowerShell implementation
   - Same functionality as bash version

### Cross-Platform Documentation

All other documentation is platform-agnostic:
- CONFIGURATION_MANAGEMENT.md
- README_MVC.md
- MVC_QUICKSTART.md
- FEATURE_REPORT_2025-10-14.md

---

## Testing on Windows

### Manual Testing

```powershell
# 1. Network diagnostic
.\check_network.ps1

# 2. Python environment
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-minimal.txt

# 3. Import test
$env:PYTHONPATH="src"
python -c "from py2flamingo.services import ConfigurationManager; print('✓ Import successful')"

# 4. Configuration discovery
python -c "from py2flamingo.services import ConfigurationManager; manager = ConfigurationManager(); configs = manager.discover_configurations(); print(f'Found {len(configs)} configs')"

# 5. Connection test
python -c "from py2flamingo.controllers import ConnectionController; from py2flamingo.services import MVCConnectionService; from py2flamingo.models import ConnectionModel; from py2flamingo.core import TCPConnection, ProtocolEncoder; tcp = TCPConnection(); encoder = ProtocolEncoder(); model = ConnectionModel(); service = MVCConnectionService(tcp, encoder); controller = ConnectionController(service, model); success, msg = controller.test_connection('127.0.0.1', 53717); print(f'Success: {success}')"
```

### Unit Tests

```powershell
# Run all tests
$env:PYTHONPATH="src"
python -m pytest tests/ -v

# Run specific test
python -m pytest tests/test_controllers.py -v
```

---

## Common Windows Issues & Solutions

### Issue 1: "python: command not found"

**Cause:** Python not in PATH

**Solution:**
```powershell
# Check Python location
where.exe python

# Add to PATH or use full path
C:\Users\YourName\AppData\Local\Programs\Python\Python39\python.exe
```

### Issue 2: PowerShell execution policy

**Cause:** Script execution disabled

**Solution:**
```powershell
# Check current policy
Get-ExecutionPolicy

# Set policy to allow scripts (Administrator)
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser

# Or run script explicitly
powershell -ExecutionPolicy Bypass -File .\check_network.ps1
```

### Issue 3: "Access denied" for network configuration

**Cause:** Need Administrator privileges

**Solution:**
- Right-click PowerShell
- Select "Run as Administrator"
- Re-run network commands

### Issue 4: Firewall blocking connections

**Cause:** Windows Firewall blocking microscope traffic

**Solution:**
```powershell
# Temporarily disable for testing (Administrator)
Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled False

# Test connection
# If works, re-enable and add specific rules
Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled True
New-NetFirewallRule -DisplayName "Flamingo" -Direction Inbound -LocalAddress 10.129.37.0/24 -Action Allow
```

---

## Windows-Specific Features

### PowerShell Cmdlets Used

- `Get-NetAdapter` - List network adapters
- `Get-NetIPAddress` - Get IP addresses
- `New-NetIPAddress` - Set static IP
- `Get-NetRoute` - Check routing table
- `Find-NetRoute` - Find route to destination
- `Test-Connection` - Ping test
- `Test-NetConnection` - TCP port test
- `Get-NetFirewallProfile` - Firewall status
- `New-NetFirewallRule` - Add firewall rule

### Advantages on Windows

1. **Native Tools:** PowerShell cmdlets are built-in
2. **GUI Options:** Network settings accessible via Settings app
3. **Firewall Integration:** Easy to configure Windows Firewall
4. **No Additional Software:** No need for WSL or Git Bash

---

## Deployment Checklist for Windows 11

- [ ] Python 3.8-3.11 installed
- [ ] Two network adapters available
- [ ] Microscope adapter configured (10.129.37.5/24)
- [ ] `check_network.ps1` shows "EXCELLENT"
- [ ] Firewall rules added (if needed)
- [ ] Virtual environment created
- [ ] Dependencies installed
- [ ] Configuration files present
- [ ] Application launches successfully
- [ ] "Test Connection" succeeds

---

## Quick Reference Card (Windows)

**Network Setup:**
```powershell
New-NetIPAddress -InterfaceAlias "Ethernet 2" -IPAddress 10.129.37.5 -PrefixLength 24
```

**Network Check:**
```powershell
.\check_network.ps1
Get-NetRoute -DestinationPrefix "10.129.37.0/24"
```

**Python Setup:**
```powershell
python -m venv .venv
.venv\Scripts\activate
pip install PyQt5 numpy
```

**Run Application:**
```powershell
$env:PYTHONPATH="src"
python -m py2flamingo
```

**Test Connection:**
```powershell
Test-NetConnection -ComputerName 10.129.37.22 -Port 53717
```

---

## Summary

✅ **Fully Windows Compatible**
- All features work on Windows 11
- Native PowerShell diagnostic tool
- Complete Windows documentation
- GUI and command-line options
- Same functionality as Linux/macOS

✅ **No Workarounds Needed**
- No WSL required
- No Git Bash required
- No Cygwin required
- Pure Windows native

✅ **Well Documented**
- Windows-specific quickstart guide
- PowerShell command reference
- GUI configuration instructions
- Troubleshooting for Windows issues

---

**Recommendation:** Use Windows 11 with confidence. All features, diagnostics, and documentation are fully compatible.

---

**Last Updated:** 2025-10-14
**Tested On:** Windows 11 (compatible with Windows 10)
**Python Versions:** 3.8, 3.9, 3.10, 3.11
