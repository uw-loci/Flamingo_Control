import numpy as np
from PyQt5.QtGui import QImage
from PIL import Image
import time

def convert_to_qimage(image_data):
    """
    Convert a 16-bit grayscale image to QImage, resizing the image to 512x512 using bilinear interpolation.
    """
    start_time = time.time()  # Record the start time

    # Resize the image data to 512x512 using bilinear interpolation
    #print('Converting to QImage')
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

    end_time = time.time()  # Record the end time
    elapsed_time = end_time - start_time  # Calculate the elapsed time
    #print('Image converted, time taken:', elapsed_time, 'seconds')  # Print the elapsed time, usually a few hundreths of a second or less

    return qimage


# import numpy as np
# from PyQt5.QtGui import QImage

# from PIL import Image

# def convert_to_qimage(image_data):
#     """
#     Convert a 16-bit grayscale image to QImage, resizing the image to 512x512 using bilinear interpolation.
#     """
#     # Resize the image data to 512x512 using bilinear interpolation
#     print('Converting to QImage')
#     image = Image.fromarray(image_data)

#     # Convert the image to grayscale mode and resize
#     resized_image = image.convert('L').resize((512, 512), resample=Image.BILINEAR)

#     # Normalize the image data to the range [0, 1]
#     normalized_image = (resized_image - np.min(resized_image)) / (np.max(resized_image) - np.min(resized_image))

#     # Scale the normalized values to the full range of 8-bit RGB values and convert to uint8
#     scaled_image = (normalized_image * 255).astype(np.uint8)

#     # Create the QImage directly from the numpy array
#     width, height = resized_image.size
#     qimage = QImage(scaled_image.data, width, height, QImage.Format_Grayscale8)
#     print('Image converted')
#     return qimage

