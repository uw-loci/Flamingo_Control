import numpy as np
from PyQt5.QtCore import QTimer
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import QApplication, QMainWindow, QLabel, QLineEdit, QVBoxLayout, QFormLayout, QPushButton, QHBoxLayout, QRadioButton, QWidget
from PyQt5.QtCore import Qt, QSize

def update_display(self):
    # Check if visualization event is set
    if visualization_event.is_set():
        # Check if there are images in the queue
        if not image_queue.empty():
            # Get the latest image from the queue
            image = image_queue.get()

            # Update the display with the image
            self.display_image(image)

def display_image(self, image):
    # Convert the numpy array image to QImage
    height, width = image.shape
    image = np.require(image, np.uint8, 'C')
    q_image = QImage(image.data, width, height, QImage.Format_Grayscale8)

    # Create a pixmap and set it as the label's pixmap
    pixmap = QPixmap.fromImage(q_image)
    pixmap = pixmap.scaled(QSize(400, 300), Qt.KeepAspectRatio)
    self.image_label.setPixmap(pixmap)