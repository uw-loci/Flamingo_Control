#!/usr/bin/env python3
"""
Test script for Status Indicator System.

This script tests the status indicator service and widget independently
without requiring a full application setup or microscope connection.

Usage:
    python test_status_indicator.py
"""

import sys
import logging
from PyQt5.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget, QPushButton
from PyQt5.QtCore import QTimer

from py2flamingo.services.status_indicator_service import (
    StatusIndicatorService, GlobalStatus
)
from py2flamingo.views.widgets.status_indicator_widget import (
    StatusIndicatorWidget, StatusIndicatorBar
)


# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


class StatusIndicatorTestWindow(QMainWindow):
    """Test window for status indicator system."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Status Indicator System Test")
        self.resize(600, 400)

        # Create status indicator service
        self.status_service = StatusIndicatorService()

        # Create status indicator widgets (both variants)
        self.status_widget = StatusIndicatorWidget()
        self.status_bar_widget = StatusIndicatorBar()

        # Connect service to widgets
        self.status_service.status_changed.connect(self.status_widget.update_status)
        self.status_service.status_changed.connect(self.status_bar_widget.update_status)

        # Also log status changes
        self.status_service.status_changed.connect(self._on_status_changed)

        self._setup_ui()

        # Auto-test sequence
        self._start_auto_test()

    def _setup_ui(self):
        """Create UI with test buttons."""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        layout = QVBoxLayout()
        central_widget.setLayout(layout)

        # Add status indicator widgets
        layout.addWidget(QWidget())  # Spacer
        layout.addWidget(self.status_widget)
        layout.addWidget(self.status_bar_widget)
        layout.addWidget(QWidget())  # Spacer

        # Add test buttons
        btn_connect = QPushButton("Test: Connect")
        btn_connect.clicked.connect(self._test_connect)
        layout.addWidget(btn_connect)

        btn_motion = QPushButton("Test: Motion Start/Stop")
        btn_motion.clicked.connect(self._test_motion)
        layout.addWidget(btn_motion)

        btn_workflow = QPushButton("Test: Workflow Start/Stop")
        btn_workflow.clicked.connect(self._test_workflow)
        layout.addWidget(btn_workflow)

        btn_disconnect = QPushButton("Test: Disconnect")
        btn_disconnect.clicked.connect(self._test_disconnect)
        layout.addWidget(btn_disconnect)

        btn_auto = QPushButton("Run Auto Test Sequence")
        btn_auto.clicked.connect(self._start_auto_test)
        layout.addWidget(btn_auto)

        # Add status bar
        self.statusBar().showMessage("Ready to test")

    def _on_status_changed(self, status: GlobalStatus, description: str):
        """Log status changes."""
        logging.info(f"Status changed: {status.value} - {description}")
        self.statusBar().showMessage(f"Status: {description}")

    def _test_connect(self):
        """Test connection established."""
        logging.info("Testing: Connection Established")
        self.status_service.on_connection_established()

    def _test_disconnect(self):
        """Test connection closed."""
        logging.info("Testing: Connection Closed")
        self.status_service.on_connection_closed()

    def _test_motion(self):
        """Test motion start/stop."""
        logging.info("Testing: Motion Start")
        self.status_service.on_motion_started()

        # Auto stop after 2 seconds
        QTimer.singleShot(2000, lambda: (
            logging.info("Testing: Motion Stop"),
            self.status_service.on_motion_stopped()
        ))

    def _test_workflow(self):
        """Test workflow start/stop."""
        logging.info("Testing: Workflow Start")
        self.status_service.on_workflow_started()

        # Auto stop after 3 seconds
        QTimer.singleShot(3000, lambda: (
            logging.info("Testing: Workflow Stop"),
            self.status_service.on_workflow_stopped()
        ))

    def _start_auto_test(self):
        """Run automatic test sequence."""
        logging.info("=== Starting Auto Test Sequence ===")

        # Schedule test events
        test_sequence = [
            (0, "Initial state (disconnected)", lambda: None),
            (1000, "Connect", self.status_service.on_connection_established),
            (2000, "Motion start", self.status_service.on_motion_started),
            (3500, "Motion stop", self.status_service.on_motion_stopped),
            (4500, "Workflow start", self.status_service.on_workflow_started),
            (6000, "Workflow stop", self.status_service.on_workflow_stopped),
            (7000, "Motion during idle", self.status_service.on_motion_started),
            (8000, "Workflow during motion", self.status_service.on_workflow_started),
            (9000, "Motion stop (workflow still running)", self.status_service.on_motion_stopped),
            (10000, "Workflow stop", self.status_service.on_workflow_stopped),
            (11000, "Disconnect", self.status_service.on_connection_closed),
            (12000, "Test complete", lambda: logging.info("=== Auto Test Complete ==="))
        ]

        for delay_ms, description, action in test_sequence:
            QTimer.singleShot(delay_ms, lambda d=description, a=action: (
                logging.info(f"Auto test: {d}"),
                a()
            ))


def test_service_only():
    """Test status indicator service without GUI."""
    print("\n=== Testing StatusIndicatorService ===\n")

    service = StatusIndicatorService()

    # Track changes
    changes = []

    def track_change(status, description):
        changes.append((status, description))
        print(f"Status: {status.value:20s} - {description}")

    service.status_changed.connect(track_change)

    # Test sequence
    print("1. Initial state:")
    print(f"   Current: {service.get_current_status().value} - {service.get_status_description()}")
    print(f"   Is busy: {service.is_busy()}")

    print("\n2. Connect:")
    service.on_connection_established()

    print("\n3. Motion start:")
    service.on_motion_started()

    print("\n4. Workflow start (should override motion):")
    service.on_workflow_started()

    print("\n5. Motion stop (workflow still running):")
    service.on_motion_stopped()

    print("\n6. Workflow stop:")
    service.on_workflow_stopped()

    print("\n7. Disconnect:")
    service.on_connection_closed()

    print(f"\n=== Test Complete ===")
    print(f"Total status changes: {len(changes)}")

    # Verify expected changes
    expected_statuses = [
        GlobalStatus.IDLE,           # Connect
        GlobalStatus.MOVING,         # Motion start
        GlobalStatus.WORKFLOW_RUNNING,  # Workflow start (overrides motion)
        GlobalStatus.WORKFLOW_RUNNING,  # Motion stop (workflow still active)
        GlobalStatus.IDLE,           # Workflow stop
        GlobalStatus.DISCONNECTED    # Disconnect
    ]

    actual_statuses = [status for status, _ in changes]

    if actual_statuses == expected_statuses:
        print("\nAll tests PASSED!")
        return True
    else:
        print("\nTests FAILED!")
        print(f"Expected: {[s.value for s in expected_statuses]}")
        print(f"Actual:   {[s.value for s in actual_statuses]}")
        return False


def main():
    """Run tests."""
    # First test service without GUI
    service_test_passed = test_service_only()

    if not service_test_passed:
        print("\nService tests failed! Skipping GUI tests.")
        return 1

    # Then test with GUI
    print("\n\n=== Starting GUI Tests ===\n")
    print("The GUI window will open with test buttons.")
    print("An automatic test sequence will run on startup.")
    print("You can also click the buttons to test manually.")
    print("\nClose the window when done.\n")

    app = QApplication(sys.argv)
    window = StatusIndicatorTestWindow()
    window.show()

    return app.exec_()


if __name__ == '__main__':
    sys.exit(main())
