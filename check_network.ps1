# Network Diagnostic Tool for Flamingo Microscope Connectivity (Windows)
# Usage: .\check_network.ps1 [microscope_ip]
# Run in PowerShell

param(
    [string]$MicroscopeIP = "10.129.37.22"
)

$MicroscopePort = 53717
$MicroscopeSubnet = "10.129.37.0/24"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Flamingo Microscope Network Diagnostics" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "Target Microscope: $MicroscopeIP`:$MicroscopePort"
Write-Host ""

# Check 1: Network Adapters
Write-Host "1. Network Adapters with IPs:" -ForegroundColor Yellow
Write-Host "--------------------------------"
$adapters = Get-NetAdapter | Where-Object { $_.Status -eq "Up" }
foreach ($adapter in $adapters) {
    $ipConfig = Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue
    if ($ipConfig) {
        Write-Host "  $($adapter.Name): $($ipConfig.IPAddress)/$($ipConfig.PrefixLength) [UP]" -ForegroundColor Green
    }
}
Write-Host ""

# Check if adapter with microscope subnet exists
Write-Host "2. Microscope Subnet Interface:" -ForegroundColor Yellow
Write-Host "--------------------------------"
$microscopeAdapter = Get-NetIPAddress -AddressFamily IPv4 | Where-Object {
    $_.IPAddress -like "10.129.37.*"
}

if ($microscopeAdapter) {
    $adapterName = (Get-NetAdapter -InterfaceIndex $microscopeAdapter.InterfaceIndex).Name
    Write-Host "✓ Found microscope interface: $adapterName" -ForegroundColor Green
    Write-Host "  IP: $($microscopeAdapter.IPAddress)/$($microscopeAdapter.PrefixLength)"
    $HasInterface = $true
} else {
    Write-Host "✗ No interface found with IP in $MicroscopeSubnet" -ForegroundColor Red
    Write-Host "  ACTION: Configure a network adapter with an IP in this subnet"
    Write-Host "  Example (PowerShell as Admin):"
    Write-Host "    New-NetIPAddress -InterfaceAlias 'Ethernet 2' -IPAddress 10.129.37.5 -PrefixLength 24"
    $HasInterface = $false
}
Write-Host ""

# Check 2: Routing Table
Write-Host "3. Routing Table:" -ForegroundColor Yellow
Write-Host "--------------------------------"
Write-Host "Routes to microscope subnet:"
$routes = Get-NetRoute -DestinationPrefix "10.129.37.0/24" -ErrorAction SilentlyContinue
if ($routes) {
    foreach ($route in $routes) {
        $adapter = Get-NetAdapter -InterfaceIndex $route.InterfaceIndex
        Write-Host "  Destination: $($route.DestinationPrefix) via $($adapter.Name)" -ForegroundColor Green
    }
    $HasRoute = $true
} else {
    Write-Host "  No specific route found for $MicroscopeSubnet" -ForegroundColor Yellow
    Write-Host "  Checking if default routing works..."

    # Check if microscope IP is reachable through any adapter
    $testRoute = Find-NetRoute -RemoteIPAddress $MicroscopeIP -ErrorAction SilentlyContinue
    if ($testRoute) {
        $adapter = Get-NetAdapter -InterfaceIndex $testRoute.InterfaceIndex
        Write-Host "  Would route to $MicroscopeIP via: $($adapter.Name)" -ForegroundColor Cyan
        $HasRoute = $true
    } else {
        Write-Host "✗ No route to $MicroscopeIP" -ForegroundColor Red
        $HasRoute = $false
    }
}
Write-Host ""

# Check 3: Ping Test
Write-Host "4. Connectivity Test (Ping):" -ForegroundColor Yellow
Write-Host "--------------------------------"
$pingResult = Test-Connection -ComputerName $MicroscopeIP -Count 2 -Quiet -ErrorAction SilentlyContinue
if ($pingResult) {
    Write-Host "✓ Microscope responds to ping at $MicroscopeIP" -ForegroundColor Green
} else {
    Write-Host "⚠ Microscope does not respond to ping (may be normal - some systems disable ping)" -ForegroundColor Yellow
    Write-Host "  This doesn't necessarily mean connection will fail"
}
Write-Host ""

# Check 4: Port Test
Write-Host "5. Port Connectivity Test:" -ForegroundColor Yellow
Write-Host "--------------------------------"
try {
    $tcpClient = New-Object System.Net.Sockets.TcpClient
    $connection = $tcpClient.BeginConnect($MicroscopeIP, $MicroscopePort, $null, $null)
    $wait = $connection.AsyncWaitHandle.WaitOne(2000, $false)

    if ($wait) {
        $tcpClient.EndConnect($connection)
        $tcpClient.Close()
        Write-Host "✓ Port $MicroscopePort is open on $MicroscopeIP" -ForegroundColor Green
        Write-Host "  Microscope server is listening and reachable!"
        $HasConnectivity = $true
    } else {
        $tcpClient.Close()
        Write-Host "✗ Cannot connect to port $MicroscopePort on $MicroscopeIP" -ForegroundColor Red
        Write-Host "  Possible causes:"
        Write-Host "  - Microscope server not running"
        Write-Host "  - Firewall blocking connection"
        Write-Host "  - Wrong IP address"
        Write-Host "  - Network routing issue"
        $HasConnectivity = $false
    }
} catch {
    Write-Host "✗ Cannot connect to port $MicroscopePort on $MicroscopeIP" -ForegroundColor Red
    Write-Host "  Error: $($_.Exception.Message)"
    $HasConnectivity = $false
}
Write-Host ""

# Check 5: Firewall Status
Write-Host "6. Firewall Status:" -ForegroundColor Yellow
Write-Host "--------------------------------"
try {
    $firewallProfile = Get-NetFirewallProfile -Profile Domain,Public,Private
    foreach ($profile in $firewallProfile) {
        $status = if ($profile.Enabled) { "Enabled" } else { "Disabled" }
        Write-Host "  $($profile.Name): $status"
    }
    Write-Host ""
    Write-Host "⚠ If firewall is enabled, ensure microscope subnet is allowed" -ForegroundColor Yellow
    Write-Host "  To add firewall rule (PowerShell as Admin):"
    Write-Host "    New-NetFirewallRule -DisplayName 'Flamingo Microscope' -Direction Inbound -LocalAddress 10.129.37.0/24 -Action Allow"
    Write-Host "    New-NetFirewallRule -DisplayName 'Flamingo Microscope' -Direction Outbound -RemoteAddress 10.129.37.0/24 -Action Allow"
} catch {
    Write-Host "Cannot check firewall (need Administrator privileges)" -ForegroundColor Yellow
}
Write-Host ""

# Check 6: Active Connections
Write-Host "7. Active Connections to Microscope:" -ForegroundColor Yellow
Write-Host "--------------------------------"
$connections = Get-NetTCPConnection -RemotePort $MicroscopePort -ErrorAction SilentlyContinue |
               Where-Object { $_.RemoteAddress -eq $MicroscopeIP }
if ($connections) {
    Write-Host "✓ Active connection(s) found:" -ForegroundColor Green
    foreach ($conn in $connections) {
        Write-Host "  $($conn.LocalAddress):$($conn.LocalPort) -> $($conn.RemoteAddress):$($conn.RemotePort) [$($conn.State)]"
    }
} else {
    Write-Host "⚠ No active connections to $MicroscopeIP`:$MicroscopePort" -ForegroundColor Yellow
    Write-Host "  (This is normal if not currently connected)"
}
Write-Host ""

# Summary
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Summary & Recommendations:" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Determine overall status
if ($HasInterface -and $HasRoute -and $HasConnectivity) {
    Write-Host "✓✓✓ EXCELLENT: Network configuration looks good!" -ForegroundColor Green
    Write-Host "    You should be able to connect to the microscope."
    Write-Host ""
    Write-Host "Next steps:"
    Write-Host "  1. Launch application: python -m py2flamingo"
    Write-Host "  2. Select microscope from dropdown or enter $MicroscopeIP"
    Write-Host "  3. Click 'Test Connection' button"
    Write-Host "  4. Click 'Connect' if test passes"
} elseif ($HasInterface -and $HasRoute) {
    Write-Host "⚠⚠ PARTIAL: Network is configured but microscope not reachable" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Possible issues:"
    Write-Host "  - Microscope server not running (check microscope power/software)"
    Write-Host "  - Firewall blocking connection"
    Write-Host "  - Network cable disconnected"
    Write-Host ""
    Write-Host "Try:"
    Write-Host "  - Verify microscope is powered on"
    Write-Host "  - Check network cable to microscope"
    Write-Host "  - Use application's 'Test Connection' for more details"
} elseif ($HasInterface) {
    Write-Host "✗✗ WARNING: Interface found but routing may be incorrect" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "ACTION REQUIRED:"
    Write-Host "  The adapter should automatically route to the microscope subnet."
    Write-Host "  If not, check Windows routing table with: route print"
} else {
    Write-Host "✗✗✗ PROBLEM: No interface configured for microscope subnet" -ForegroundColor Red
    Write-Host ""
    Write-Host "ACTION REQUIRED:"
    Write-Host "  Configure network adapter with IP in $MicroscopeSubnet"
    Write-Host "  Steps:"
    Write-Host "    1. Open Network & Internet Settings"
    Write-Host "    2. Select the adapter connected to microscope"
    Write-Host "    3. Click 'Edit' under IP assignment"
    Write-Host "    4. Select 'Manual' and enable IPv4"
    Write-Host "    5. Enter IP: 10.129.37.5, Subnet: 255.255.255.0"
    Write-Host "    6. Leave Gateway empty (isolated network)"
    Write-Host ""
    Write-Host "  Or use PowerShell (as Administrator):"
    Write-Host "    New-NetIPAddress -InterfaceAlias 'Ethernet 2' -IPAddress 10.129.37.5 -PrefixLength 24"
}

Write-Host ""
Write-Host "For detailed network configuration help, see:"
Write-Host "  NETWORK_CONFIGURATION.md"
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
