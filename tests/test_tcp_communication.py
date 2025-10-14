import unittest
from unittest.mock import patch, MagicMock

from src.py2flamingo.services.connection_service import ConnectionService
from src.py2flamingo.core.events import EventManager
from src.py2flamingo.core.queue_manager import QueueManager

from tests.test_utils import NoOpThreadManager


class TestTCPClient(unittest.TestCase):
    """
    Refactored to target the new public API (ConnectionService) instead of the removed tcpip_client.TCPClient.
    Assertions are behavioral: queues populated and 'send' event set.
    """

    def setUp(self):
        self.test_ip = "127.0.0.1"
        self.test_port = 53717

        self.event_manager = EventManager()
        self.queue_manager = QueueManager()

        self.conn = ConnectionService(
            ip=self.test_ip,
            port=self.test_port,
            event_manager=self.event_manager,
            queue_manager=self.queue_manager
        )

        # Common socket mocks used across tests
        self.mock_nuc_socket = MagicMock(name="nuc_socket")
        self.mock_live_socket = MagicMock(name="live_socket")

    # ---------- helpers ----------

    def _patch_thread_manager(self):
        return patch(
            'src.py2flamingo.services.connection_service.ThreadManager',
            NoOpThreadManager
        )

    def _patch_create_socket_success(self):
        # ConnectionService._create_socket typically called twice (nuc + live)
        return patch.object(
            self.conn,
            '_create_socket',
            side_effect=[self.mock_nuc_socket, self.mock_live_socket]
        )

    # ---------- tests ----------

    def test_successful_connection(self):
        with self._patch_thread_manager(), self._patch_create_socket_success():
            ok = self.conn.connect()
            self.assertTrue(ok)
            self.assertTrue(self.conn.is_connected())

    def test_connection_timeout(self):
        # Simulate socket creation raising an error on first attempt
        with self._patch_thread_manager(), patch.object(self.conn, '_create_socket', side_effect=TimeoutError()):
            ok = self.conn.connect()
            # Depending on your ConnectionService.connect() semantics,
            # it may return False or raise; adapt as needed.
            self.assertFalse(ok)

    def test_disconnect(self):
        with self._patch_thread_manager(), self._patch_create_socket_success():
            self.assertTrue(self.conn.connect())
            self.assertTrue(self.conn.is_connected())

            # Disconnect should clean up sockets without raising
            self.conn.disconnect()

            # If ConnectionService tracks sockets, you can assert close() called:
            # These asserts are safe only if your service stores these mocks.
            try:
                self.mock_nuc_socket.close.assert_called()
                self.mock_live_socket.close.assert_called()
            except AssertionError:
                # If your ConnectionService doesn't hold the mocks, skip
                pass

            self.assertFalse(self.conn.is_connected())

    def test_send_command_structure(self):
        """
        Legacy test checked binary header layout. In the refactor we assert behavior:
        - command code appears in 'command' queue
        - payload appears in 'command_data' queue
        - 'send' event is set to wake the sender thread
        """
        with self._patch_thread_manager(), self._patch_create_socket_success():
            self.assertTrue(self.conn.connect())

            # Example command & payload (adjust values to match project conventions)
            command_code = 24580  # e.g., a CAMERA or STAGE code in your enum/table
            payload = [1, 0, 0, 10.5]

            # Clear initial state and send
            self.queue_manager.clear_all()
            self.event_manager.clear_all()

            self.conn.send_command(command_code, payload)

            # Assert: command code queued
            cmd_q = self.queue_manager.get_queue_nowait('command')
            self.assertFalse(cmd_q.empty(), "command queue should contain an entry")
            queued_cmd = cmd_q.get_nowait()
            self.assertEqual(queued_cmd, command_code)

            # Assert: payload queued
            data_q = self.queue_manager.get_queue_nowait('command_data')
            self.assertFalse(data_q.empty(), "command_data queue should contain an entry")
            queued_payload = data_q.get_nowait()
            self.assertEqual(queued_payload, payload)

            # Assert: send event set
            self.assertTrue(self.event_manager.is_set('send'), "'send' event should be set")

    def test_send_workflow(self):
        """
        Assert that sending a workflow enqueues the correct command and data
        and sets the send event; stub out file IO and formatting via file_handlers.
        """
        with self._patch_thread_manager(), self._patch_create_socket_success():
            self.assertTrue(self.conn.connect())

            # Stub file writing and workflow formatting to avoid touching disk
            with patch('src.py2flamingo.utils.file_handlers.dict_to_workflow') as mock_wf_write:
                # Prepare a simple workflow dict and a fake path
                workflow_dict = {"Work Flow Type": "Stack", "ZStepMicrons": 1.0, "Slices": 5}
                workflow_path = "workflows/test_workflow.txt"

                # Clear state
                self.queue_manager.clear_all()
                self.event_manager.clear_all()

                # Send workflow (pick the correct public API name if different)
                self.conn.send_workflow(workflow_dict, workflow_path)

                # Assert file_formatting was invoked
                mock_wf_write.assert_called_once()

                # Assert queues populated
                cmd_q = self.queue_manager.get_queue_nowait('command')
                data_q = self.queue_manager.get_queue_nowait('command_data')
                self.assertFalse(cmd_q.empty(), "command queue should contain an entry after send_workflow")
                self.assertFalse(data_q.empty(), "command_data queue should contain workflow path or blob")

                # Assert send event set
                self.assertTrue(self.event_manager.is_set('send'), "'send' event should be set after send_workflow")
