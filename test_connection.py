#!/usr/bin/env python3
"""Quick test of TCP connection and workflow sending."""

import sys
sys.path.insert(0, 'src')

from py2flamingo.tcp_client import TCPClient, parse_metadata_file
from pathlib import Path

def test_connection():
    """Test connection to mock server and workflow sending."""

    print("=" * 60)
    print("Testing Flamingo Minimal Interface")
    print("=" * 60)

    # Parse metadata
    print("\n1. Parsing metadata file...")
    try:
        ip, port = parse_metadata_file('microscope_settings/FlamingoMetaData_test.txt')
        print(f"   ✓ Found microscope at {ip}:{port}")
    except Exception as e:
        print(f"   ✗ Error: {e}")
        return False

    # Create client
    print("\n2. Creating TCP client...")
    client = TCPClient(ip, port)
    print(f"   ✓ Client created")

    # Connect
    print("\n3. Connecting to microscope...")
    try:
        nuc, live = client.connect()
        if nuc and live:
            print(f"   ✓ Connected successfully!")
            print(f"   ✓ Command socket: {nuc}")
            print(f"   ✓ Live socket: {live}")
        else:
            print(f"   ✗ Connection failed")
            return False
    except Exception as e:
        print(f"   ✗ Error: {e}")
        return False

    # Find workflow
    print("\n4. Finding workflow file...")
    workflow_file = Path('workflows/Snapshot.txt')
    if not workflow_file.exists():
        print(f"   ✗ Workflow not found: {workflow_file}")
        client.disconnect()
        return False
    print(f"   ✓ Found: {workflow_file}")

    # Send workflow
    print("\n5. Sending workflow...")
    try:
        client.send_workflow(str(workflow_file))
        print(f"   ✓ Workflow sent successfully!")
    except Exception as e:
        print(f"   ✗ Error: {e}")
        client.disconnect()
        return False

    # Send stop command
    print("\n6. Sending stop command...")
    try:
        client.send_command(TCPClient.CMD_WORKFLOW_STOP)
        print(f"   ✓ Stop command sent!")
    except Exception as e:
        print(f"   ✗ Error: {e}")
        client.disconnect()
        return False

    # Disconnect
    print("\n7. Disconnecting...")
    client.disconnect()
    print(f"   ✓ Disconnected")

    print("\n" + "=" * 60)
    print("✓ ALL TESTS PASSED!")
    print("=" * 60)

    return True

if __name__ == "__main__":
    success = test_connection()
    sys.exit(0 if success else 1)
