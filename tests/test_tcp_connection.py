"""
Unit tests for TCP connection management.

Tests the TCPConnection class for socket management, connection handling,
and data transmission with the Flamingo microscope.
"""

import unittest
import socket
import threading
import time
from unittest.mock import Mock, patch, MagicMock
from py2flamingo.core.tcp_connection import TCPConnection


class TestTCPConnectionBasic(unittest.TestCase):
    """Test basic TCPConnection functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.connection = TCPConnection()

    def test_init_creates_disconnected_connection(self):
        """Test that new connection starts disconnected."""
        self.assertFalse(self.connection.is_connected())

    def test_get_connection_info_when_disconnected(self):
        """Test that connection info is None when disconnected."""
        ip, port = self.connection.get_connection_info()
        self.assertIsNone(ip)
        self.assertIsNone(port)

    def test_is_connected_returns_false_initially(self):
        """Test that is_connected returns False initially."""
        self.assertFalse(self.connection.is_connected())

    def test_disconnect_when_not_connected_is_safe(self):
        """Test that disconnect can be called when not connected."""
        # Should not raise an exception
        self.connection.disconnect()
        self.assertFalse(self.connection.is_connected())


class TestTCPConnectionValidation(unittest.TestCase):
    """Test input validation."""

    def setUp(self):
        """Set up test fixtures."""
        self.connection = TCPConnection()

    def test_connect_validates_ip_format(self):
        """Test that invalid IP address raises ValueError."""
        invalid_ips = [
            "invalid",
            "999.999.999.999",
            "10.0.0",
            "10.0.0.0.0",
            "",
            "10.0.0.256"
        ]

        for invalid_ip in invalid_ips:
            with self.assertRaises(ValueError):
                self.connection.connect(invalid_ip, 53717)

    def test_connect_validates_port_range(self):
        """Test that invalid port raises ValueError."""
        invalid_ports = [0, -1, 65536, 70000]

        for invalid_port in invalid_ports:
            with self.assertRaises(ValueError):
                self.connection.connect("127.0.0.1", invalid_port)

    def test_connect_validates_port_type(self):
        """Test that non-integer port raises ValueError."""
        with self.assertRaises(ValueError):
            self.connection.connect("127.0.0.1", "53717")

    def test_connect_rejects_port_65535(self):
        """Test that port 65535 is rejected (live port would overflow)."""
        with self.assertRaises(ValueError):
            self.connection.connect("127.0.0.1", 65535)

    def test_send_bytes_validates_data_type(self):
        """Test that non-bytes data raises ValueError."""
        with self.assertRaises(ValueError):
            self.connection.send_bytes("string data")

        with self.assertRaises(ValueError):
            self.connection.send_bytes(12345)

    def test_send_bytes_validates_socket_type(self):
        """Test that invalid socket_type raises ValueError."""
        with patch.object(self.connection, '_connected', True):
            with self.assertRaises(ValueError):
                self.connection.send_bytes(b"data", socket_type="invalid")

    def test_receive_bytes_validates_size(self):
        """Test that invalid size raises ValueError."""
        with self.assertRaises(ValueError):
            self.connection.receive_bytes(0)

        with self.assertRaises(ValueError):
            self.connection.receive_bytes(-1)

    def test_receive_bytes_validates_socket_type(self):
        """Test that invalid socket_type raises ValueError."""
        with patch.object(self.connection, '_connected', True):
            with self.assertRaises(ValueError):
                self.connection.receive_bytes(128, socket_type="invalid")


class TestTCPConnectionWithMockServer(unittest.TestCase):
    """Test connection with mock server on port 53717."""

    def setUp(self):
        """Set up test fixtures."""
        self.connection = TCPConnection()
        # Mock server should be running on 127.0.0.1:53717

    def tearDown(self):
        """Clean up after tests."""
        self.connection.disconnect()

    def test_connect_to_mock_server(self):
        """Test connection to mock server."""
        try:
            cmd_sock, live_sock = self.connection.connect("127.0.0.1", 53717, timeout=2.0)

            self.assertIsNotNone(cmd_sock)
            self.assertIsNotNone(live_sock)
            self.assertTrue(self.connection.is_connected())

        except (socket.timeout, ConnectionRefusedError) as e:
            self.skipTest(f"Mock server not available: {e}")

    def test_connect_creates_dual_sockets(self):
        """Test that connect creates both command and live sockets."""
        try:
            cmd_sock, live_sock = self.connection.connect("127.0.0.1", 53717)

            self.assertIsInstance(cmd_sock, socket.socket)
            self.assertIsInstance(live_sock, socket.socket)
            self.assertIsNot(cmd_sock, live_sock)

        except (socket.timeout, ConnectionRefusedError):
            self.skipTest("Mock server not available")

    def test_get_connection_info_after_connect(self):
        """Test that connection info is correct after connect."""
        try:
            self.connection.connect("127.0.0.1", 53717)

            ip, port = self.connection.get_connection_info()
            self.assertEqual(ip, "127.0.0.1")
            self.assertEqual(port, 53717)

        except (socket.timeout, ConnectionRefusedError):
            self.skipTest("Mock server not available")

    def test_disconnect_clears_connection_info(self):
        """Test that disconnect clears connection info."""
        try:
            self.connection.connect("127.0.0.1", 53717)
            self.assertTrue(self.connection.is_connected())

            self.connection.disconnect()

            self.assertFalse(self.connection.is_connected())
            ip, port = self.connection.get_connection_info()
            self.assertIsNone(ip)
            self.assertIsNone(port)

        except (socket.timeout, ConnectionRefusedError):
            self.skipTest("Mock server not available")

    def test_disconnect_is_idempotent(self):
        """Test that disconnect can be called multiple times safely."""
        try:
            self.connection.connect("127.0.0.1", 53717)
            self.connection.disconnect()
            self.connection.disconnect()  # Should not raise
            self.connection.disconnect()  # Should not raise

            self.assertFalse(self.connection.is_connected())

        except (socket.timeout, ConnectionRefusedError):
            self.skipTest("Mock server not available")


class TestTCPConnectionErrors(unittest.TestCase):
    """Test error handling."""

    def setUp(self):
        """Set up test fixtures."""
        self.connection = TCPConnection()

    def tearDown(self):
        """Clean up."""
        self.connection.disconnect()

    def test_connect_timeout_raises_exception(self):
        """Test that connection timeout raises socket.timeout."""
        # Use non-routable IP to force timeout
        with self.assertRaises(socket.timeout):
            self.connection.connect("10.255.255.1", 53717, timeout=0.1)

    def test_connect_refused_raises_exception(self):
        """Test that connection refused raises ConnectionRefusedError."""
        # Connect to closed port
        with self.assertRaises(ConnectionRefusedError):
            self.connection.connect("127.0.0.1", 9999, timeout=1.0)

    def test_send_bytes_when_not_connected_raises_error(self):
        """Test that send_bytes raises ConnectionError when not connected."""
        with self.assertRaises(ConnectionError):
            self.connection.send_bytes(b"data")

    def test_receive_bytes_when_not_connected_raises_error(self):
        """Test that receive_bytes raises ConnectionError when not connected."""
        with self.assertRaises(ConnectionError):
            self.connection.receive_bytes(128)

    def test_connect_failure_leaves_disconnected_state(self):
        """Test that failed connection leaves object in clean state."""
        try:
            self.connection.connect("10.255.255.1", 53717, timeout=0.1)
        except socket.timeout:
            pass

        self.assertFalse(self.connection.is_connected())
        ip, port = self.connection.get_connection_info()
        self.assertIsNone(ip)
        self.assertIsNone(port)


class TestTCPConnectionThreadSafety(unittest.TestCase):
    """Test thread-safety of TCPConnection."""

    def setUp(self):
        """Set up test fixtures."""
        self.connection = TCPConnection()

    def tearDown(self):
        """Clean up."""
        self.connection.disconnect()

    def test_concurrent_is_connected_calls(self):
        """Test that is_connected is thread-safe."""
        results = []

        def check_connection():
            for _ in range(100):
                results.append(self.connection.is_connected())

        threads = [threading.Thread(target=check_connection) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All results should be consistent (all False)
        self.assertTrue(all(r == False for r in results))

    def test_concurrent_get_connection_info(self):
        """Test that get_connection_info is thread-safe."""
        results = []

        def get_info():
            for _ in range(100):
                results.append(self.connection.get_connection_info())

        threads = [threading.Thread(target=get_info) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All results should be consistent
        self.assertTrue(all(r == (None, None) for r in results))


class TestTCPConnectionWithMocks(unittest.TestCase):
    """Test connection behavior with mocked sockets."""

    def setUp(self):
        """Set up test fixtures."""
        self.connection = TCPConnection()

    @patch('socket.socket')
    def test_connect_clears_timeout_after_connection(self, mock_socket_class):
        """Test that timeout is cleared after successful connection."""
        mock_cmd_socket = MagicMock()
        mock_live_socket = MagicMock()

        # Return different sockets for command and live
        mock_socket_class.side_effect = [mock_cmd_socket, mock_live_socket]

        self.connection.connect("127.0.0.1", 53717, timeout=2.0)

        # Verify timeout was set and then cleared for command socket
        mock_cmd_socket.settimeout.assert_any_call(2.0)
        mock_cmd_socket.settimeout.assert_any_call(None)

        # Verify timeout was set and then cleared for live socket
        mock_live_socket.settimeout.assert_any_call(2.0)
        mock_live_socket.settimeout.assert_any_call(None)

    @patch('socket.socket')
    def test_connect_connects_to_correct_ports(self, mock_socket_class):
        """Test that connect uses correct ports."""
        mock_cmd_socket = MagicMock()
        mock_live_socket = MagicMock()

        mock_socket_class.side_effect = [mock_cmd_socket, mock_live_socket]

        self.connection.connect("127.0.0.1", 53717)

        # Verify command port connection
        mock_cmd_socket.connect.assert_called_once_with(("127.0.0.1", 53717))

        # Verify live port connection (command port + 1)
        mock_live_socket.connect.assert_called_once_with(("127.0.0.1", 53718))

    @patch('socket.socket')
    def test_send_bytes_uses_sendall(self, mock_socket_class):
        """Test that send_bytes uses sendall."""
        mock_cmd_socket = MagicMock()
        mock_live_socket = MagicMock()

        mock_socket_class.side_effect = [mock_cmd_socket, mock_live_socket]

        self.connection.connect("127.0.0.1", 53717)

        data = b"test data"
        self.connection.send_bytes(data, socket_type="command")

        mock_cmd_socket.sendall.assert_called_once_with(data)

    @patch('socket.socket')
    def test_send_bytes_to_live_socket(self, mock_socket_class):
        """Test that send_bytes can use live socket."""
        mock_cmd_socket = MagicMock()
        mock_live_socket = MagicMock()

        mock_socket_class.side_effect = [mock_cmd_socket, mock_live_socket]

        self.connection.connect("127.0.0.1", 53717)

        data = b"test data"
        self.connection.send_bytes(data, socket_type="live")

        mock_live_socket.sendall.assert_called_once_with(data)

    @patch('socket.socket')
    def test_receive_bytes_uses_recv(self, mock_socket_class):
        """Test that receive_bytes uses recv."""
        mock_cmd_socket = MagicMock()
        mock_live_socket = MagicMock()

        mock_socket_class.side_effect = [mock_cmd_socket, mock_live_socket]

        mock_cmd_socket.recv.return_value = b"response data"

        self.connection.connect("127.0.0.1", 53717)

        result = self.connection.receive_bytes(128, socket_type="command")

        mock_cmd_socket.recv.assert_called_once_with(128)
        self.assertEqual(result, b"response data")

    @patch('socket.socket')
    def test_receive_bytes_from_live_socket(self, mock_socket_class):
        """Test that receive_bytes can use live socket."""
        mock_cmd_socket = MagicMock()
        mock_live_socket = MagicMock()

        mock_socket_class.side_effect = [mock_cmd_socket, mock_live_socket]

        mock_live_socket.recv.return_value = b"live data"

        self.connection.connect("127.0.0.1", 53717)

        result = self.connection.receive_bytes(128, socket_type="live")

        mock_live_socket.recv.assert_called_once_with(128)
        self.assertEqual(result, b"live data")

    @patch('socket.socket')
    def test_receive_bytes_with_timeout(self, mock_socket_class):
        """Test that receive_bytes sets and clears timeout."""
        mock_cmd_socket = MagicMock()
        mock_live_socket = MagicMock()

        mock_socket_class.side_effect = [mock_cmd_socket, mock_live_socket]

        mock_cmd_socket.recv.return_value = b"data"

        self.connection.connect("127.0.0.1", 53717)

        self.connection.receive_bytes(128, timeout=1.5)

        # Verify timeout was set and cleared
        mock_cmd_socket.settimeout.assert_any_call(1.5)
        mock_cmd_socket.settimeout.assert_any_call(None)

    @patch('socket.socket')
    def test_receive_bytes_timeout_clears_timeout(self, mock_socket_class):
        """Test that timeout is cleared even when recv times out."""
        mock_cmd_socket = MagicMock()
        mock_live_socket = MagicMock()

        mock_socket_class.side_effect = [mock_cmd_socket, mock_live_socket]

        mock_cmd_socket.recv.side_effect = socket.timeout()

        self.connection.connect("127.0.0.1", 53717)

        with self.assertRaises(socket.timeout):
            self.connection.receive_bytes(128, timeout=1.0)

        # Verify timeout was cleared
        mock_cmd_socket.settimeout.assert_any_call(None)

    @patch('socket.socket')
    def test_disconnect_closes_both_sockets(self, mock_socket_class):
        """Test that disconnect closes both sockets."""
        mock_cmd_socket = MagicMock()
        mock_live_socket = MagicMock()

        mock_socket_class.side_effect = [mock_cmd_socket, mock_live_socket]

        self.connection.connect("127.0.0.1", 53717)
        self.connection.disconnect()

        mock_cmd_socket.close.assert_called_once()
        mock_live_socket.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
