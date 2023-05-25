import numpy as np
from PyQt5.QtGui import QImage, QPixmap, QColor
from PyQt5.QtWidgets import QApplication, QMainWindow, QLabel, QLineEdit, QVBoxLayout, QFormLayout, QPushButton, QHBoxLayout, QRadioButton, QWidget
from PyQt5.QtCore import Qt, QSize

from PIL import Image

def convert_to_qimage(image_data):
    """
    Convert a 16-bit grayscale image to QImage, resizing the image to 512x512 using bilinear interpolation.
    """
    # Resize the image data to 512x512 using bilinear interpolation
    print('Converting to QImage')
    image = Image.fromarray(image_data)

    # Convert the image to grayscale mode and resize
    resized_image = image.convert('L').resize((512, 512), resample=Image.BILINEAR)

    # Normalize the image data to the range [0, 1]
    normalized_image = (resized_image - np.min(resized_image)) / (np.max(resized_image) - np.min(resized_image))

    # Scale the normalized values to the full range of 8-bit RGB values and convert to uint8
    scaled_image = (normalized_image * 255).astype(np.uint8)

    # Create the QImage directly from the numpy array
    width, height = resized_image.size
    qimage = QImage(scaled_image.data, width, height, QImage.Format_Grayscale8)

    return qimage

# def convert_to_qimage(image_data):
#     """
#     Convert a 16-bit grayscale image to QImage, scaling the values to the full range of 8-bit RGB values,
#     and resizing the image to 512x512 using bilinear interpolation.
#     """
#     # Resize the image data to 512x512 using bilinear interpolation
#     print('converting to qimage')
#     image = Image.fromarray(image_data)

#     resized_image = image.resize((512, 512), resample=Image.BILINEAR)

#     width, height = resized_image.size
#     qimage = QImage(width, height, QImage.Format_RGB32)

#     min_value = np.min(resized_image)
#     max_value = np.max(resized_image)

#     # Calculate the scaling factors
#     scale = 255 / (max_value - min_value)
#     offset = -min_value * scale

#     for y in range(height):
#         for x in range(width):
#             value = int(resized_image.getpixel((x, y)) * scale + offset)
#             color = QColor(value, value, value)  # Grayscale color
#             qimage.setPixel(x, y, color.rgb())

#     return qimage