# Network Configuration Guide for Dual-Network Setup

**For systems with both internet and microscope subnet connectivity**

## Overview

Your setup involves two network interfaces:
1. **Internet Connection** - For general internet access, software updates, etc.
2. **Microscope Subnet** - Isolated local network for microscope communication

This guide ensures microscope traffic routes through the correct interface.

## Network Architecture

```
┌─────────────────┐
│  Your Computer  │
│                 │
│  ┌───────────┐  │         ┌──────────┐
│  │   eth0    │──┼────────→│ Internet │
│  │ (or WiFi) │  │         └──────────┘
│  └───────────┘  │
│                 │
│  ┌───────────┐  │         ┌─────────────────────┐
│  │   eth1    │──┼────────→│ Microscope Subnet   │
│  │(Dedicated)│  │         │ 10.129.37.x/24      │
│  └───────────┘  │         │                     │
└─────────────────┘         │ ┌─────────────────┐ │
                            │ │ Flamingo n7     │ │
                            │ │ 10.129.37.22    │ │
                            │ └─────────────────┘ │
                            │                     │
                            │ ┌─────────────────┐ │
                            │ │ Flamingo zion   │ │
                            │ │ 10.129.37.20    │ │
                            │ └─────────────────┘ │
                            └─────────────────────┘
```

## How Routing Works

### Automatic Routing (Default Behavior)

The operating system maintains a **routing table** that determines which interface to use:

```bash
# View routing table
ip route show
# or on older systems:
route -n
```

**Example routing table:**
```
default via 192.168.1.1 dev eth0       # Internet traffic
10.129.37.0/24 dev eth1 scope link     # Microscope subnet traffic
192.168.1.0/24 dev eth0 scope link     # Local network traffic
```

**Key Point:** When you connect to `10.129.37.22`, the OS automatically routes through `eth1` because that interface's subnet matches.

### Why This Works

1. **Subnet Matching**: OS compares destination IP (10.129.37.22) with routing table
2. **Best Match**: 10.129.37.0/24 is more specific than default route
3. **Interface Selection**: Traffic automatically uses eth1 (microscope interface)

**No manual intervention needed** - the OS handles routing automatically!

## Network Configuration

### Step 1: Verify Network Interfaces

```bash
# List all network interfaces
ip addr show

# Look for your microscope interface (e.g., eth1, enp3s0, etc.)
# It should have an IP in the 10.129.37.x range
```

**Expected Output:**
```
1: lo: <LOOPBACK,UP,LOWER_UP>
    inet 127.0.0.1/8

2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP>
    inet 192.168.1.100/24  # Internet connection

3: eth1: <BROADCAST,MULTICAST,UP,LOWER_UP>
    inet 10.129.37.5/24     # Microscope subnet ← This is what you need
```

### Step 2: Configure Static IP (if needed)

If your microscope interface doesn't have an IP in the correct subnet:

**Option A: NetworkManager (Ubuntu/Fedora Desktop)**
```bash
# Edit connection
nmcli connection modify eth1 ipv4.addresses 10.129.37.5/24
nmcli connection modify eth1 ipv4.method manual
nmcli connection up eth1
```

**Option B: Netplan (Ubuntu Server)**

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
        - 10.129.37.5/24  # Your computer's IP on microscope subnet
      # No gateway - this is an isolated network
```

Apply:
```bash
sudo netplan apply
```

**Option C: /etc/network/interfaces (Debian/Older systems)**

Edit `/etc/network/interfaces`:
```
auto eth1
iface eth1 inet static
    address 10.129.37.5
    netmask 255.255.255.0
    # No gateway - isolated network
```

Restart:
```bash
sudo systemctl restart networking
```

### Step 3: Verify Routing Table

```bash
ip route show | grep 10.129.37
```

**Expected Output:**
```
10.129.37.0/24 dev eth1 proto kernel scope link src 10.129.37.5
```

This confirms that all traffic to 10.129.37.x goes through eth1.

### Step 4: Test Connectivity

```bash
# Ping microscope (if it responds to ping)
ping -c 4 10.129.37.22

# Test specific interface (optional, for verification)
ping -I eth1 -c 4 10.129.37.22
```

## Using the Application

### Configuration Files

Your microscope configurations already specify the correct IPs:

**microscope_settings/FlamingoMetadata.txt:**
```xml
<Instrument>
  <Type>
    Microscope name = n7
    Microscope address = 10.129.37.22 53717
  </Type>
</Instrument>
```

When you select "n7" from the dropdown, the application will:
1. Load IP: 10.129.37.22
2. OS routing table directs traffic through eth1 (microscope interface)
3. Connection goes to the correct network ✓

### Test Connection Feature

Use the "Test Connection" button to verify routing:

1. Select microscope from dropdown
2. Click "Test Connection"
3. If successful: Routing is working correctly
4. If failed: Check network configuration below

## Troubleshooting

### Issue 1: Connection Timeout

**Symptom:** "Connection timeout. Server is not responding."

**Possible Causes:**
1. Microscope interface (eth1) is down
2. Wrong IP address in microscope subnet
3. Routing table not configured

**Solutions:**
```bash
# Check interface status
ip link show eth1

# If interface is down, bring it up
sudo ip link set eth1 up

# Check if IP is assigned
ip addr show eth1

# Verify routing
ip route get 10.129.37.22
```

**Expected routing output:**
```
10.129.37.22 dev eth1 src 10.129.37.5 uid 1000
```

### Issue 2: Wrong Network Interface

**Symptom:** Traffic attempts to go through internet connection instead of eth1

**Diagnosis:**
```bash
# Check which interface would be used
ip route get 10.129.37.22

# If it says "via 192.168.1.1 dev eth0" - WRONG INTERFACE
```

**Solution:**
```bash
# Add specific route for microscope subnet
sudo ip route add 10.129.37.0/24 dev eth1

# Make persistent (Ubuntu/Netplan)
# Edit /etc/netplan/01-netcfg.yaml and add to eth1:
#   routes:
#     - to: 10.129.37.0/24
#       via: 0.0.0.0  # Direct route, no gateway
```

### Issue 3: Firewall Blocking

**Symptom:** "Connection refused" even though microscope is on

**Solution:**
```bash
# Check firewall status
sudo ufw status

# Allow traffic on microscope subnet
sudo ufw allow from 10.129.37.0/24
sudo ufw allow to 10.129.37.0/24

# Or disable firewall for testing (not recommended for production)
sudo ufw disable
```

### Issue 4: DNS Resolution

**Symptom:** Can't resolve microscope hostname

**Note:** Always use IP addresses (10.129.37.22), not hostnames, for isolated networks.

## Diagnostic Tools

### Built-in Network Diagnostics

The application includes network diagnostic commands:

```bash
# Test connection with detailed output
PYTHONPATH=src python -c "
from py2flamingo.controllers import ConnectionController
from py2flamingo.services import MVCConnectionService
from py2flamingo.models import ConnectionModel
from py2flamingo.core import TCPConnection, ProtocolEncoder

tcp = TCPConnection()
encoder = ProtocolEncoder()
model = ConnectionModel()
service = MVCConnectionService(tcp, encoder)
controller = ConnectionController(service, model)

# Test connection
success, msg = controller.test_connection('10.129.37.22', 53717, timeout=3.0)
print(f'Result: {success}')
print(f'Message: {msg}')
"
```

### System Network Diagnostics

```bash
# Complete network diagnostic script
cat > /tmp/network_diag.sh << 'EOF'
#!/bin/bash
echo "=== Network Interfaces ==="
ip addr show | grep -A 3 "eth\|enp"

echo -e "\n=== Routing Table ==="
ip route show

echo -e "\n=== Microscope Subnet Route ==="
ip route get 10.129.37.22

echo -e "\n=== Test Microscope Connectivity ==="
ping -c 2 -W 1 10.129.37.22 || echo "Microscope not responding to ping (may be normal)"

echo -e "\n=== Active Connections ==="
ss -tunap | grep 53717 || echo "No connections on port 53717"

echo -e "\n=== Firewall Status ==="
sudo ufw status 2>/dev/null || echo "UFW not installed"
EOF

chmod +x /tmp/network_diag.sh
/tmp/network_diag.sh
```

## Best Practices

### 1. Use Static IPs on Microscope Subnet

Configure your computer's microscope interface with a static IP (e.g., 10.129.37.5) to ensure consistent connectivity.

### 2. Document Your Network Layout

Keep a record of:
- Interface names (eth0, eth1, etc.)
- IP addresses (both internet and microscope subnet)
- Microscope IPs and names

**Example:**
```
Computer Interfaces:
- eth0 (enp2s0): Internet, DHCP (192.168.1.x)
- eth1 (enp3s0): Microscopes, Static 10.129.37.5/24

Microscopes:
- n7: 10.129.37.22:53717
- zion: 10.129.37.20:53717
- elsa: 10.129.37.17:53717
```

### 3. Test After Network Changes

After any network configuration changes:
1. Run diagnostic script
2. Use "Test Connection" in application
3. Verify successful connection

### 4. Use Configuration Files

Store microscope configurations in `microscope_settings/`:
- Prevents manual entry errors
- Ensures correct IPs
- Easy to switch between microscopes

## Advanced: Binding to Specific Interface (Optional)

If automatic routing doesn't work, you can bind sockets to a specific interface:

**Note:** This is rarely needed - automatic routing should work.

```python
import socket

# Create socket
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

# Bind to specific interface (Linux only)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, b'eth1')

# Connect
sock.connect(('10.129.37.22', 53717))
```

**This feature is NOT currently implemented** but can be added if needed.

## Windows Configuration

### Check Network Adapters

```powershell
# PowerShell
Get-NetAdapter
Get-NetIPAddress

# Look for adapter with 10.129.37.x IP
```

### Set Static IP

```powershell
# PowerShell (run as Administrator)
New-NetIPAddress -InterfaceAlias "Ethernet 2" -IPAddress 10.129.37.5 -PrefixLength 24
```

### Check Routing

```powershell
# PowerShell
route print
# Look for 10.129.37.0 entry

# Or
Get-NetRoute -DestinationPrefix "10.129.37.0/24"
```

## macOS Configuration

### Check Interfaces

```bash
# Terminal
ifconfig
# Look for en0, en1, etc. with 10.129.37.x IP
```

### Set Static IP

```bash
# System Preferences > Network > Select Adapter > Configure IPv4 > Manually
# IP Address: 10.129.37.5
# Subnet Mask: 255.255.255.0
# (Leave Router/Gateway empty for isolated network)
```

### Check Routing

```bash
netstat -rn | grep 10.129.37
```

## Summary

### For Most Users

**TL;DR:** Configure your microscope network interface with a static IP in the 10.129.37.x subnet. The OS routing table will automatically direct microscope traffic through the correct interface. Use the application's "Test Connection" button to verify.

### Quick Checklist

- [ ] Microscope interface has static IP (e.g., 10.129.37.5)
- [ ] Routing table includes 10.129.37.0/24 route
- [ ] `ip route get 10.129.37.22` shows correct interface
- [ ] "Test Connection" in application succeeds
- [ ] Configuration files have correct IPs

### Need Help?

If routing still doesn't work:
1. Run diagnostic script (above)
2. Check `/var/log/syslog` or `dmesg` for network errors
3. Verify physical cable connection
4. Ensure microscope is powered on and connected to same switch/subnet

---

**Last Updated:** 2025-10-14
**Applies To:** Dual-network configurations with dedicated microscope subnet
