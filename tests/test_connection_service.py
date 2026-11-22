# tests/test_connection_service.py
"""
Queue/event hand-off between threads (QueueManager, EventManager) and
reading a settings text file with text_to_dict.

The tests of the legacy ConnectionService class went with it; the live
MVCConnectionService is tested in test_services.py, test_light_off_is_honest.py
and the synthetic-microscope tests.
"""

import os
import tempfile
import threading
import time
import unittest

from py2flamingo.core.events import EventManager
from py2flamingo.core.queue_manager import QueueManager


class TestThreadIntegration(unittest.TestCase):
    """Test thread management and communication."""

    def setUp(self):
        """Set up test fixtures."""
        self.event_manager = EventManager()
        self.queue_manager = QueueManager()

    def test_command_send_flow(self):
        """Test the flow of sending a command through threads."""

        # Simulate send thread behavior
        def mock_send_thread():
            """Simplified send thread."""
            while True:
                if self.event_manager.wait_for_event("send", timeout=0.1):
                    # Get command
                    command = self.queue_manager.get_nowait("command")
                    if command:
                        # Get data if available
                        data = self.queue_manager.get_nowait("command_data")

                        # Simulate sending
                        self.queue_manager.put_nowait(
                            "other_data",
                            {"command": command, "data": data, "sent": True},
                        )

                        # Clear send event
                        self.event_manager.clear_event("send")

                        # Set system idle
                        self.event_manager.set_event("system_idle")

                if self.event_manager.is_set("terminate"):
                    break

        # Start mock thread
        thread = threading.Thread(target=mock_send_thread)
        thread.daemon = True
        thread.start()

        try:
            # Send a command
            self.queue_manager.put_nowait("command", 24580)
            self.queue_manager.put_nowait("command_data", [1, 0, 0, 5.0])
            self.event_manager.set_event("send")

            # Wait for processing
            time.sleep(0.2)

            # Verify command was processed
            result = self.queue_manager.get_nowait("other_data")
            self.assertIsNotNone(result)
            self.assertEqual(result["command"], 24580)
            self.assertEqual(result["data"], [1, 0, 0, 5.0])
            self.assertTrue(result["sent"])

            # Verify system is idle
            self.assertTrue(self.event_manager.is_set("system_idle"))

        finally:
            # Clean up
            self.event_manager.set_event("terminate")
            thread.join(timeout=1.0)

    def test_image_receive_flow(self):
        """Test the flow of receiving image data."""
        # Simulate image data
        test_image = b"\x00" * 1024  # 1KB of zeros

        def mock_live_thread():
            """Simplified live thread."""
            # Simulate receiving image
            self.queue_manager.put_nowait("image", test_image)
            self.queue_manager.put_nowait("visualize", test_image)

        # Run mock thread
        thread = threading.Thread(target=mock_live_thread)
        thread.start()
        thread.join()

        # Verify images were queued
        image_data = self.queue_manager.get_nowait("image")
        viz_data = self.queue_manager.get_nowait("visualize")

        self.assertEqual(image_data, test_image)
        self.assertEqual(viz_data, test_image)


class TestConfigurationFiles(unittest.TestCase):
    """Test configuration file handling."""

    def test_metadata_parsing(self):
        """Test parsing FlamingoMetaData.txt."""
        metadata_content = """<Instrument>
<Type>
Microscope name = TestScope
Microscope address = 192.168.1.100 53717
Microscope type = Flamingo
Objective lens magnification = 16
Tube lens length (mm) = 200
</Type>
</Instrument>"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(metadata_content)
            temp_file = f.name

        try:
            from py2flamingo.utils.file_handlers import text_to_dict

            result = text_to_dict(temp_file)

            # Verify parsing
            instrument = result["Instrument"]["Type"]
            self.assertEqual(instrument["Microscope name"], "TestScope")
            self.assertEqual(instrument["Microscope address"], "192.168.1.100 53717")
            self.assertEqual(float(instrument["Objective lens magnification"]), 16)

        finally:
            os.unlink(temp_file)


if __name__ == "__main__":
    unittest.main()
