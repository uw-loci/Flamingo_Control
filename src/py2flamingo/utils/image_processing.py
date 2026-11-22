# src/py2flamingo/utils/image_processing.py
"""
QImage helpers for the live and sample views.
"""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage, QPainter, QPen


def draw_center_crosshair(qimage: QImage, size: int = 20, width: int = 2) -> QImage:
    """
    Draw a red crosshair at the center of a QImage.

    Returns a copy with the crosshair drawn (the input is not modified).

    Args:
        qimage: Source QImage
        size: Half-length of each crosshair arm in pixels
        width: Pen width in pixels

    Returns:
        QImage with a centered crosshair overlay
    """
    # Convert to an RGB format so the red pen renders as red even when the
    # source is grayscale (drawing red onto Format_Grayscale8 yields gray).
    result = qimage.convertToFormat(QImage.Format_RGB888)

    painter = QPainter(result)
    pen = QPen(Qt.red)
    pen.setWidth(width)
    painter.setPen(pen)

    center_x = result.width() // 2
    center_y = result.height() // 2

    # Vertical and horizontal arms
    painter.drawLine(center_x, center_y - size, center_x, center_y + size)
    painter.drawLine(center_x - size, center_y, center_x + size, center_y)

    painter.end()

    return result
