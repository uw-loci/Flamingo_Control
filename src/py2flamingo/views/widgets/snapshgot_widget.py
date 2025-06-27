# views/widgets/snapshot_widget.py
from PyQt5.QtWidgets import QWidget, QPushButton, QVBoxLayout
from controllers.snapshot_controller import SnapshotController

class SnapshotWidget(QWidget):
    def __init__(self, snapshot_controller: SnapshotController):
        super().__init__()
        self.controller = snapshot_controller
        self._setup_ui()
        
    def _setup_ui(self):
        layout = QVBoxLayout()
        
        self.snapshot_button = QPushButton("Take IF Snapshot")
        self.snapshot_button.clicked.connect(self._on_snapshot_clicked)
        
        layout.addWidget(self.snapshot_button)
        self.setLayout(layout)
        
    def _on_snapshot_clicked(self):
        """Handle snapshot button click - no business logic here"""
        self.controller.take_snapshot()