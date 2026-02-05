"""
Resources module for Flamingo Control application.

Provides access to application icons and other resources.
"""

from pathlib import Path
from PyQt5.QtGui import QIcon


def get_app_icon() -> QIcon:
    """Get the Flamingo application icon.

    Returns:
        QIcon: The flamingo icon for use in window title bars.
    """
    icon_path = Path(__file__).parent / "flamingo_icon.png"
    if icon_path.exists():
        return QIcon(str(icon_path))
    return QIcon()  # Fallback to empty icon
