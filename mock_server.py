#!/usr/bin/env python3
"""
Mock Flamingo microscope server for testing without hardware.

This server simulates the Flamingo microscope's TCP protocol,
allowing you to test the minimal interface without a real microscope.

Usage:
    python mock_server.py [--ip 127.0.0.1] [--port 53717]
"""

import socket
import struct
import threading
import time
import argparse
import logging
from pathlib import Path


class MockFlamingoServer:
    """Mock server that simulates Flamingo microscope responses."""

    # Protocol markers
    START_MARKER = 0xF321E654
    END_MARKER = 0xFEDC4321

    # Command structure
    COMMAND_STRUCT = struct.Struct("I I I I I I I I I I d I 72s I")

    def __init__(self, ip="127.0.0.1", port=53717):
        """
        Initialize mock server.

        Args:
            ip: IP address to bind to
            port: Command port to listen on (live port will be port+1)
        """
        self.ip = ip
        self.port = port
        self.live_port = port + 1

        self.running = False
        self.nuc_socket = None
        self.live_socket = None

        self.logger = logging.getLogger(__name__)
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )

    def start(self):
        """Start the mock server."""
        self.running = True

        # Start command port listener
        nuc_thread = threading.Thread(target=self._command_handler, daemon=True)
        nuc_thread.start()

        # Start live imaging port listener
        live_thread = threading.Thread(target=self._live_handler, daemon=True)
        live_thread.start()

        self.logger.info(f"Mock Flamingo server started on {self.ip}:{self.port}")
        self.logger.info(f"Command port: {self.port}, Live port: {self.live_port}")
        self.logger.info("Press Ctrl+C to stop")

    def stop(self):
        """Stop the mock server."""
        self.running = False
        if self.nuc_socket:
            self.nuc_socket.close()
        if self.live_socket:
            self.live_socket.close()
        self.logger.info("Server stopped")

    def _command_handler(self):
        """Handle command port connections."""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            server.bind((self.ip, self.port))
            server.listen(1)
            server.settimeout(1.0)  # Allow periodic checks for self.running

            self.logger.info(f"Listening for commands on {self.ip}:{self.port}")

            while self.running:
                try:
                    client, addr = server.accept()
                    self.logger.info(f"Command connection from {addr}")

                    # Handle this client
                    while self.running:
                        try:
                            # Receive command header
                            data = client.recv(self.COMMAND_STRUCT.size)
                            if not data:
                                break

                            if len(data) == self.COMMAND_STRUCT.size:
                                self._process_command(client, data)

                        except socket.timeout:
                            continue
                        except Exception as e:
                            self.logger.error(f"Error handling command: {e}")
                            break

                    client.close()
                    self.logger.info("Command connection closed")

                except socket.timeout:
                    continue
                except Exception as e:
                    if self.running:
                        self.logger.error(f"Command handler error: {e}")
        finally:
            server.close()

    def _process_command(self, client: socket.socket, data: bytes):
        """Process a received command."""
        try:
            # Unpack command structure
            unpacked = self.COMMAND_STRUCT.unpack(data)

            start_marker = unpacked[0]
            command_code = unpacked[1]
            status = unpacked[2]
            value = unpacked[10]
            data_bits = unpacked[11]
            data_field = unpacked[12]
            end_marker = unpacked[13]

            # Verify markers
            if start_marker != self.START_MARKER or end_marker != self.END_MARKER:
                self.logger.warning("Invalid command markers")
                return

            self.logger.info(f"Received command: {command_code}")

            # Handle specific commands
            if command_code == 12292:  # CAMERA_WORK_FLOW_START
                self._handle_workflow_start(client, data_bits, data_field)
            elif command_code == 12293:  # CAMERA_WORK_FLOW_STOP
                self._handle_workflow_stop(client)
            elif command_code == 24580:  # STAGE_POSITION_SET
                self._handle_position_set(client, value)
            elif command_code == 24584:  # STAGE_POSITION_GET
                self._handle_position_get(client)
            elif command_code == 40967:  # SYSTEM_STATE_GET
                self._handle_system_state(client)
            else:
                self.logger.warning(f"Unknown command: {command_code}")
                self._send_ack(client)

        except Exception as e:
            self.logger.error(f"Error processing command: {e}")

    def _handle_workflow_start(self, client: socket.socket, data_bits: int, data_field: bytes):
        """Handle workflow start command."""
        if data_bits == 1:
            # Workflow data follows - read file size
            file_size = struct.unpack("I", data_field[:4])[0]
            self.logger.info(f"Expecting workflow data: {file_size} bytes")

            # Receive workflow data
            workflow_data = b""
            remaining = file_size

            while remaining > 0:
                chunk = client.recv(min(4096, remaining))
                if not chunk:
                    break
                workflow_data += chunk
                remaining -= len(chunk)

            # Log workflow content
            try:
                workflow_text = workflow_data.decode('utf-8')
                self.logger.info(f"Received workflow ({len(workflow_data)} bytes):")
                self.logger.info("─" * 60)
                # Show first 500 chars
                preview = workflow_text[:500]
                if len(workflow_text) > 500:
                    preview += "\n... (truncated)"
                self.logger.info(preview)
                self.logger.info("─" * 60)

                # Save to file for inspection
                save_path = Path("received_workflow.txt")
                with open(save_path, 'w') as f:
                    f.write(workflow_text)
                self.logger.info(f"Saved workflow to: {save_path}")

            except Exception as e:
                self.logger.error(f"Error decoding workflow: {e}")

        self._send_ack(client)
        self.logger.info("Workflow started (simulated)")

    def _handle_workflow_stop(self, client: socket.socket):
        """Handle workflow stop command."""
        self.logger.info("Workflow stop requested")
        self._send_ack(client)

    def _handle_position_set(self, client: socket.socket, value: float):
        """Handle position set command."""
        self.logger.info(f"Position set to: {value}")
        self._send_ack(client)

    def _handle_position_get(self, client: socket.socket):
        """Handle position get command."""
        # Send mock position
        position = [10.5, 20.3, 5.1, 45.0]  # X, Y, Z, R
        self.logger.info(f"Sending position: {position}")
        self._send_ack(client)

    def _handle_system_state(self, client: socket.socket):
        """Handle system state query."""
        self.logger.info("System state: IDLE")
        self._send_ack(client)

    def _send_ack(self, client: socket.socket):
        """Send acknowledgment response."""
        # Simple ACK response
        try:
            ack = struct.pack("I", 0x4F4B4159)  # "OKAY" in hex
            client.send(ack)
        except Exception as e:
            self.logger.error(f"Error sending ACK: {e}")

    def _live_handler(self):
        """Handle live imaging port connections."""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            server.bind((self.ip, self.live_port))
            server.listen(1)
            server.settimeout(1.0)

            self.logger.info(f"Listening for live data on {self.ip}:{self.live_port}")

            while self.running:
                try:
                    client, addr = server.accept()
                    self.logger.info(f"Live data connection from {addr}")

                    # Simulate sending periodic image data
                    while self.running:
                        try:
                            # Send a simple "heartbeat" packet
                            # In real system, this would be image data
                            heartbeat = struct.pack("I", int(time.time()))
                            client.send(heartbeat)
                            time.sleep(1.0)
                        except:
                            break

                    client.close()
                    self.logger.info("Live data connection closed")

                except socket.timeout:
                    continue
                except Exception as e:
                    if self.running:
                        self.logger.error(f"Live handler error: {e}")
        finally:
            server.close()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Mock Flamingo microscope server for testing"
    )
    parser.add_argument(
        "--ip",
        default="127.0.0.1",
        help="IP address to bind to (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=53717,
        help="Command port to listen on (default: 53717)"
    )

    args = parser.parse_args()

    server = MockFlamingoServer(args.ip, args.port)

    try:
        server.start()

        # Keep running until interrupted
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nShutting down...")
        server.stop()


if __name__ == "__main__":
    main()
