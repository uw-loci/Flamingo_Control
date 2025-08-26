# src/py2flamingo/views/widgets/snapshgot_widget.py
"""
Widget for handling snapshot actions and display.
"""

from PyQt5.QtWidgets import QWidget, QVBoxLayout, QPushButton, QLabel

class SnapshotWidget(QWidget):
    """
    A widget providing a button to take snapshot and display snapshot info.
    """
    def __init__(self, snapshot_controller):
        super().__init__()
        self.snapshot_controller = snapshot_controller
        self.layout = QVBoxLayout()
        self.setLayout(self.layout)
        
        self.snap_button = QPushButton("Take Snapshot")
        self.snap_button.clicked.connect(self.take_snapshot)
        self.layout.addWidget(self.snap_button)
        
        self.status_label = QLabel("No snapshot taken yet.")
        self.layout.addWidget(self.status_label)
    
    def take_snapshot(self):
        """Trigger the snapshot and update status."""
        try:
            self.snapshot_controller.take_snapshot()
            self.status_label.setText("Snapshot taken successfully.")
        except Exception as e:
            self.status_label.setText(f"Snapshot failed: {e}")
