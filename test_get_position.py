#!/usr/bin/env python3
"""
Test script to demonstrate what STAGE_POSITION_GET command returns.

This script connects to the microscope and sends the STAGE_POSITION_GET
command to show exactly what data the microscope returns.

The goal is to demonstrate to the maintainer what is currently returned
vs. what we need (actual current position).
"""

import sys
import logging
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from py2flamingo.core.tcp_connection import TCPConnection
from py2flamingo.core.tcp_protocol import ProtocolEncoder
from py2flamingo.models.command import Command
import struct

# Setup logging to see everything
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def parse_response(response_bytes: bytes) -> dict:
    """Parse the response bytes to show structure."""
    if len(response_bytes) < 128:
        return {"error": f"Response too short: {len(response_bytes)} bytes"}

    # Protocol structure from tcp_protocol.py:
    # START (4 bytes) + CODE (4 bytes) + STATUS (4 bytes) +
    # PARAMS (7 * 4 = 28 bytes) + VALUE (8 bytes) + DATA (80 bytes)

    try:
        start_marker = struct.unpack('<I', response_bytes[0:4])[0]
        command_code = struct.unpack('<I', response_bytes[4:8])[0]
        status_code = struct.unpack('<I', response_bytes[8:12])[0]

        # Unpack 7 parameters
        params = []
        for i in range(7):
            offset = 12 + (i * 4)
            param = struct.unpack('<i', response_bytes[offset:offset+4])[0]
            params.append(param)

        # Unpack value (double)
        value = struct.unpack('<d', response_bytes[40:48])[0]

        # Get data section
        data = response_bytes[48:128]

        # Try to decode data as string
        try:
            data_str = data.rstrip(b'\x00').decode('utf-8', errors='replace')
        except:
            data_str = f"<binary data: {data.hex()[:40]}...>"

        return {
            "start_marker": f"0x{start_marker:08X}",
            "command_code": command_code,
            "status_code": status_code,
            "params": params,
            "value": value,
            "data": data_str,
            "data_bytes": data[:40].hex() if len(data) > 0 else ""
        }
    except Exception as e:
        return {"parse_error": str(e)}

def main():
    """Test the STAGE_POSITION_GET command."""

    # Microscope connection details
    IP = "192.168.1.1"
    PORT = 53717

    logger.info("=" * 70)
    logger.info("STAGE_POSITION_GET Command Test")
    logger.info("=" * 70)
    logger.info("")
    logger.info(f"Connecting to microscope at {IP}:{PORT}...")

    # Create TCP connection
    tcp_conn = TCPConnection()
    encoder = ProtocolEncoder()

    try:
        # Connect
        command_socket, live_socket = tcp_conn.connect(IP, PORT, timeout=5.0)
        logger.info("✓ Connected successfully")
        logger.info("")

        # Create STAGE_POSITION_GET command
        COMMAND_CODES_STAGE_POSITION_GET = 24584

        logger.info(f"Sending command: STAGE_POSITION_GET (code: {COMMAND_CODES_STAGE_POSITION_GET})")
        logger.info("")

        cmd = Command(
            code=COMMAND_CODES_STAGE_POSITION_GET,
            parameters={'params': [0, 0, 0, 0, 0, 0, 0], 'value': 0.0}
        )

        # Encode and send
        cmd_bytes = encoder.encode_command(
            code=cmd.code,
            status=0,
            params=cmd.parameters.get('params'),
            value=cmd.parameters.get('value', 0.0),
            data=b''
        )

        command_socket.sendall(cmd_bytes)
        logger.info(f"✓ Command sent ({len(cmd_bytes)} bytes)")

        # Receive response
        response = command_socket.recv(128)
        logger.info(f"✓ Response received ({len(response)} bytes)")
        logger.info("")

        # Parse and display response
        logger.info("RESPONSE STRUCTURE:")
        logger.info("-" * 70)

        parsed = parse_response(response)
        for key, value in parsed.items():
            if key == "params":
                logger.info(f"  {key:20} = {value}")
            elif key == "data":
                logger.info(f"  {key:20} = {repr(value[:100])}")
            elif key == "data_bytes":
                if value:
                    logger.info(f"  {key:20} = {value}")
            else:
                logger.info(f"  {key:20} = {value}")

        logger.info("")
        logger.info("=" * 70)
        logger.info("INTERPRETATION:")
        logger.info("=" * 70)
        logger.info("")

        # Check what kind of data we got
        if parsed.get('data') and len(parsed['data']) > 10:
            logger.info("✓ Response contains text data (likely settings file path or content)")
            logger.info(f"  Data preview: {parsed['data'][:100]}")
            logger.info("")
            logger.info("⚠ NOTE: This does NOT contain actual stage position coordinates!")
            logger.info("⚠ The microscope does NOT report current position via this command.")
        else:
            logger.info("Response structure:")
            logger.info(f"  - Command code: {parsed.get('command_code')}")
            logger.info(f"  - Status: {parsed.get('status_code')}")
            logger.info(f"  - Value field: {parsed.get('value')}")
            logger.info(f"  - Params: {parsed.get('params')}")

        logger.info("")
        logger.info("=" * 70)
        logger.info("QUESTION FOR MAINTAINER:")
        logger.info("=" * 70)
        logger.info("")
        logger.info("Is there a command code that returns the CURRENT stage position?")
        logger.info("")
        logger.info("We need a command that returns:")
        logger.info("  - Current X position (mm)")
        logger.info("  - Current Y position (mm)")
        logger.info("  - Current Z position (mm)")
        logger.info("  - Current R angle (degrees)")
        logger.info("")
        logger.info("Without position feedback from hardware, the software must:")
        logger.info("  1. Track position locally (can become inaccurate)")
        logger.info("  2. Cannot detect if stage was manually moved")
        logger.info("  3. Cannot recover from partial movement failures")
        logger.info("  4. Cannot verify movements completed successfully")
        logger.info("")

        # Disconnect
        tcp_conn.disconnect()
        logger.info("✓ Disconnected")

    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        return 1

    return 0

if __name__ == "__main__":
    sys.exit(main())
