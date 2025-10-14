#!/bin/bash
# Network Diagnostic Tool for Flamingo Microscope Connectivity
# Usage: ./check_network.sh [microscope_ip]

echo "========================================"
echo "Flamingo Microscope Network Diagnostics"
echo "========================================"
echo ""

# Default microscope IP (can override with argument)
MICROSCOPE_IP="${1:-10.129.37.22}"
MICROSCOPE_PORT=53717
MICROSCOPE_SUBNET="10.129.37.0/24"

echo "Target Microscope: $MICROSCOPE_IP:$MICROSCOPE_PORT"
echo ""

# Check 1: Network Interfaces
echo "1. Network Interfaces with IPs:"
echo "--------------------------------"
if command -v ip &> /dev/null; then
    ip -br addr show | grep -v "127.0.0.1" | grep "UP"
else
    ifconfig | grep -A 1 "flags=.*UP" | grep "inet " | grep -v "127.0.0.1"
fi
echo ""

# Check if interface with microscope subnet exists
echo "2. Microscope Subnet Interface:"
echo "--------------------------------"
MICROSCOPE_INTERFACE=$(ip addr show | grep "inet ${MICROSCOPE_SUBNET%/*}" | awk '{print $NF}')
if [ -n "$MICROSCOPE_INTERFACE" ]; then
    echo "✓ Found microscope interface: $MICROSCOPE_INTERFACE"
    ip addr show "$MICROSCOPE_INTERFACE" | grep "inet "
else
    echo "✗ No interface found with IP in $MICROSCOPE_SUBNET"
    echo "  ACTION: Configure a network interface with an IP in this subnet"
    echo "  Example: sudo ip addr add 10.129.37.5/24 dev eth1"
fi
echo ""

# Check 2: Routing Table
echo "3. Routing Table:"
echo "--------------------------------"
if command -v ip &> /dev/null; then
    echo "All routes:"
    ip route show | head -10
    echo ""
    echo "Route to microscope ($MICROSCOPE_IP):"
    ROUTE_OUTPUT=$(ip route get $MICROSCOPE_IP 2>&1)
    echo "$ROUTE_OUTPUT"

    # Check if route goes through correct interface
    if echo "$ROUTE_OUTPUT" | grep -q "10.129.37"; then
        echo "✓ Route appears correct (uses microscope subnet)"
    else
        echo "✗ Route may be incorrect - not using microscope subnet"
        echo "  ACTION: Add route: sudo ip route add $MICROSCOPE_SUBNET dev eth1"
    fi
else
    route -n | head -15
fi
echo ""

# Check 3: Ping Test
echo "4. Connectivity Test (Ping):"
echo "--------------------------------"
if ping -c 2 -W 1 $MICROSCOPE_IP &> /dev/null; then
    echo "✓ Microscope responds to ping at $MICROSCOPE_IP"
else
    echo "⚠ Microscope does not respond to ping (may be normal - some systems disable ping)"
    echo "  This doesn't necessarily mean connection will fail"
fi
echo ""

# Check 4: Port Test (if nc available)
echo "5. Port Connectivity Test:"
echo "--------------------------------"
if command -v nc &> /dev/null; then
    if timeout 2 nc -z $MICROSCOPE_IP $MICROSCOPE_PORT 2>/dev/null; then
        echo "✓ Port $MICROSCOPE_PORT is open on $MICROSCOPE_IP"
        echo "  Microscope server is listening and reachable!"
    else
        echo "✗ Cannot connect to port $MICROSCOPE_PORT on $MICROSCOPE_IP"
        echo "  Possible causes:"
        echo "  - Microscope server not running"
        echo "  - Firewall blocking connection"
        echo "  - Wrong IP address"
        echo "  - Network routing issue"
    fi
elif command -v telnet &> /dev/null; then
    if timeout 2 telnet $MICROSCOPE_IP $MICROSCOPE_PORT </dev/null 2>&1 | grep -q "Connected"; then
        echo "✓ Port $MICROSCOPE_PORT is open on $MICROSCOPE_IP"
    else
        echo "✗ Cannot connect to port $MICROSCOPE_PORT"
    fi
else
    echo "⚠ nc or telnet not available - cannot test port connectivity"
    echo "  Install: sudo apt-get install netcat"
fi
echo ""

# Check 5: Firewall Status
echo "6. Firewall Status:"
echo "--------------------------------"
if command -v ufw &> /dev/null; then
    UFW_STATUS=$(sudo ufw status 2>/dev/null | head -5)
    if [ $? -eq 0 ]; then
        echo "$UFW_STATUS"
        if echo "$UFW_STATUS" | grep -q "Status: active"; then
            echo ""
            echo "⚠ Firewall is active - ensure microscope subnet is allowed"
            echo "  To allow: sudo ufw allow from $MICROSCOPE_SUBNET"
        fi
    else
        echo "Cannot check firewall (need sudo)"
    fi
elif command -v firewall-cmd &> /dev/null; then
    echo "Firewalld status:"
    sudo firewall-cmd --state 2>/dev/null || echo "Cannot check (need sudo)"
else
    echo "No firewall detected (ufw/firewalld)"
fi
echo ""

# Check 6: Active Connections
echo "7. Active Connections to Microscope:"
echo "--------------------------------"
ACTIVE_CONN=$(ss -tn 2>/dev/null | grep ":$MICROSCOPE_PORT" || netstat -tn 2>/dev/null | grep ":$MICROSCOPE_PORT")
if [ -n "$ACTIVE_CONN" ]; then
    echo "✓ Active connection(s) found:"
    echo "$ACTIVE_CONN"
else
    echo "⚠ No active connections to port $MICROSCOPE_PORT"
    echo "  (This is normal if not currently connected)"
fi
echo ""

# Summary
echo "========================================"
echo "Summary & Recommendations:"
echo "========================================"
echo ""

# Determine overall status
HAS_INTERFACE=false
HAS_ROUTE=false
HAS_CONNECTIVITY=false

if [ -n "$MICROSCOPE_INTERFACE" ]; then
    HAS_INTERFACE=true
fi

if ip route get $MICROSCOPE_IP 2>&1 | grep -q "10.129.37"; then
    HAS_ROUTE=true
fi

if command -v nc &> /dev/null; then
    if timeout 2 nc -z $MICROSCOPE_IP $MICROSCOPE_PORT 2>/dev/null; then
        HAS_CONNECTIVITY=true
    fi
fi

# Print status
if $HAS_INTERFACE && $HAS_ROUTE && $HAS_CONNECTIVITY; then
    echo "✓✓✓ EXCELLENT: Network configuration looks good!"
    echo "    You should be able to connect to the microscope."
    echo ""
    echo "Next steps:"
    echo "  1. Launch application: PYTHONPATH=src python -m py2flamingo"
    echo "  2. Select microscope from dropdown or enter $MICROSCOPE_IP"
    echo "  3. Click 'Test Connection' button"
    echo "  4. Click 'Connect' if test passes"
elif $HAS_INTERFACE && $HAS_ROUTE; then
    echo "⚠⚠ PARTIAL: Network is configured but microscope not reachable"
    echo ""
    echo "Possible issues:"
    echo "  - Microscope server not running (check microscope power/software)"
    echo "  - Firewall blocking connection"
    echo "  - Network cable disconnected"
    echo ""
    echo "Try:"
    echo "  - Verify microscope is powered on"
    echo "  - Check network cable to microscope"
    echo "  - Use application's 'Test Connection' for more details"
elif $HAS_INTERFACE; then
    echo "✗✗ WARNING: Interface found but routing may be incorrect"
    echo ""
    echo "ACTION REQUIRED:"
    echo "  Add route to microscope subnet:"
    echo "  sudo ip route add $MICROSCOPE_SUBNET dev $MICROSCOPE_INTERFACE"
else
    echo "✗✗✗ PROBLEM: No interface configured for microscope subnet"
    echo ""
    echo "ACTION REQUIRED:"
    echo "  Configure network interface with IP in $MICROSCOPE_SUBNET"
    echo "  Example:"
    echo "    sudo ip addr add 10.129.37.5/24 dev eth1"
    echo "    sudo ip link set eth1 up"
    echo ""
    echo "  See NETWORK_CONFIGURATION.md for detailed instructions"
fi

echo ""
echo "For detailed network configuration help, see:"
echo "  NETWORK_CONFIGURATION.md"
echo ""
echo "========================================"
