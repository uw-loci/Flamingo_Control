"""
Status Indicator Widget for displaying global system state.

This widget provides a visual indicator of the microscope system status
with color-coded states and smooth transitions.
"""

from PyQt5.QtWidgets import QWidget, QHBoxLayout, QLabel
from PyQt5.QtCore import Qt, QPropertyAnimation, QEasingCurve, pyqtProperty
from PyQt5.QtGui import QColor, QPalette

from py2flamingo.services.status_indicator_service import GlobalStatus
from py2flamingo.views.colors import SUCCESS_COLOR, ERROR_COLOR, WARNING_COLOR, NEUTRAL_COLOR


class StatusIndicatorWidget(QWidget):
    """
    Visual status indicator widget.

    Displays system status with:
    - Color-coded indicator (Blue=Ready, Amber=Moving, Purple=Workflow, Grey=Disconnected)
    - Status text label
    - Smooth color transitions
    - Tooltip with detailed status

    The widget consists of a small colored square (15x15px) next to a status label.
    Colors are optimized for colorblind accessibility.
    """

    # Color mapping for each status (colorblind-friendly)
    STATUS_COLORS = {
        GlobalStatus.DISCONNECTED: QColor(NEUTRAL_COLOR),      # Grey
        GlobalStatus.IDLE: QColor(SUCCESS_COLOR),              # Blue
        GlobalStatus.MOVING: QColor(WARNING_COLOR),            # Amber/Gold
        GlobalStatus.WORKFLOW_RUNNING: QColor("#a56eff")       # Purple
    }

    def __init__(self, parent=None):
        """
        Initialize status indicator widget.

        Args:
            parent: Optional parent widget
        """
        super().__init__(parent)

        self._current_color = self.STATUS_COLORS[GlobalStatus.DISCONNECTED]
        self._setup_ui()

    def _setup_ui(self):
        """Create and layout UI components."""
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Color indicator (small square)
        self.color_indicator = QLabel()
        self.color_indicator.setFixedSize(15, 15)
        self.color_indicator.setAutoFillBackground(True)
        self._update_indicator_color(self._current_color)

        # Status text label
        self.status_label = QLabel("Disconnected")
        self.status_label.setStyleSheet("font-weight: bold;")

        # Add widgets to layout
        layout.addWidget(self.color_indicator)
        layout.addWidget(self.status_label)
        layout.addStretch()

        self.setLayout(layout)

        # Set initial tooltip
        self.setToolTip("System Status: Disconnected")

    def update_status(self, status: GlobalStatus, description: str):
        """
        Update displayed status with smooth transition.

        Args:
            status: New GlobalStatus
            description: Human-readable status description
        """
        # Update color with animation
        target_color = self.STATUS_COLORS.get(
            status,
            self.STATUS_COLORS[GlobalStatus.DISCONNECTED]
        )
        self._animate_color_change(target_color)

        # Update text
        self.status_label.setText(description)

        # Update tooltip
        self.setToolTip(f"System Status: {description}")

    def _animate_color_change(self, target_color: QColor):
        """
        Smoothly animate color transition.

        Args:
            target_color: Target QColor to transition to
        """
        # Create color animation
        self.color_animation = QPropertyAnimation(self, b"indicatorColor")
        self.color_animation.setDuration(300)  # 300ms transition
        self.color_animation.setStartValue(self._current_color)
        self.color_animation.setEndValue(target_color)
        self.color_animation.setEasingCurve(QEasingCurve.InOutQuad)
        self.color_animation.start()

        # Update current color
        self._current_color = target_color

    def _update_indicator_color(self, color: QColor):
        """
        Update the indicator square's background color.

        Args:
            color: QColor to set
        """
        palette = self.color_indicator.palette()
        palette.setColor(QPalette.Window, color)
        self.color_indicator.setPalette(palette)

    # Property for color animation
    def _get_indicator_color(self):
        """Get current indicator color (for animation)."""
        return self._current_color

    def _set_indicator_color(self, color):
        """Set indicator color (for animation)."""
        self._current_color = color
        self._update_indicator_color(color)

    indicatorColor = pyqtProperty(QColor, fget=_get_indicator_color, fset=_set_indicator_color)


class StatusIndicatorBar(QWidget):
    """
    Alternative status indicator as a colored bar.

    This variant displays as a thin colored bar instead of a small square,
    which may be more noticeable in the UI.
    """

    STATUS_COLORS = StatusIndicatorWidget.STATUS_COLORS

    def __init__(self, parent=None):
        """
        Initialize status indicator bar.

        Args:
            parent: Optional parent widget
        """
        super().__init__(parent)

        self._current_color = self.STATUS_COLORS[GlobalStatus.DISCONNECTED]
        self._setup_ui()

    def _setup_ui(self):
        """Create and layout UI components."""
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Color bar (wider, thinner)
        self.color_bar = QLabel()
        self.color_bar.setFixedSize(4, 20)  # Thin vertical bar
        self.color_bar.setAutoFillBackground(True)
        self._update_bar_color(self._current_color)

        # Status text label
        self.status_label = QLabel("Disconnected")
        self.status_label.setStyleSheet("font-weight: bold;")

        # Add widgets to layout
        layout.addWidget(self.color_bar)
        layout.addWidget(self.status_label)
        layout.addStretch()

        self.setLayout(layout)

        # Set initial tooltip
        self.setToolTip("System Status: Disconnected")

    def update_status(self, status: GlobalStatus, description: str):
        """
        Update displayed status with smooth transition.

        Args:
            status: New GlobalStatus
            description: Human-readable status description
        """
        # Update color with animation
        target_color = self.STATUS_COLORS.get(
            status,
            self.STATUS_COLORS[GlobalStatus.DISCONNECTED]
        )
        self._animate_color_change(target_color)

        # Update text
        self.status_label.setText(description)

        # Update tooltip
        self.setToolTip(f"System Status: {description}")

    def _animate_color_change(self, target_color: QColor):
        """
        Smoothly animate color transition.

        Args:
            target_color: Target QColor to transition to
        """
        # Create color animation
        self.color_animation = QPropertyAnimation(self, b"barColor")
        self.color_animation.setDuration(300)  # 300ms transition
        self.color_animation.setStartValue(self._current_color)
        self.color_animation.setEndValue(target_color)
        self.color_animation.setEasingCurve(QEasingCurve.InOutQuad)
        self.color_animation.start()

        # Update current color
        self._current_color = target_color

    def _update_bar_color(self, color: QColor):
        """
        Update the bar's background color.

        Args:
            color: QColor to set
        """
        palette = self.color_bar.palette()
        palette.setColor(QPalette.Window, color)
        self.color_bar.setPalette(palette)

    # Property for color animation
    def _get_bar_color(self):
        """Get current bar color (for animation)."""
        return self._current_color

    def _set_bar_color(self, color):
        """Set bar color (for animation)."""
        self._current_color = color
        self._update_bar_color(color)

    barColor = pyqtProperty(QColor, fget=_get_bar_color, fset=_set_bar_color)
