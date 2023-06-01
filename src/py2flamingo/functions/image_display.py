import time

import numpy as np
from PIL import Image
from PyQt5.QtGui import QImage
from skimage import io  # , transform
# TODO scipy.ndimage scikit image

def convert_to_qimage(image_data):
    """
    This function converts a 16-bit grayscale image to a QImage, resizing the image to 512x512 pixels using bilinear interpolation.
    It first resizes the image, then calculates the lower and upper percentiles for display normalization. The pixel values are then
    clipped to the middle 95% range and normalized to [0, 1]. The normalized values are scaled to the full range of 8-bit grayscale values.
    Finally, a QImage is created directly from the numpy array.

    Parameters
    ----------
    image_data : numpy.ndarray
        The image to be converted and displayed, represented as a numpy array.

    Returns
    -------
    qimage : QImage
        The converted image, represented as a QImage.
    """
    start_time = time.time()  # Record the start time
    new_height, new_width = 512, 512

    # Convert the numpy array to a PIL Image
    image = Image.fromarray(image_data)

    # Convert the image to grayscale mode and resize it to the new dimensions using bilinear interpolation
    scaled_image = image.convert("L").resize((new_width, new_height), resample=Image.BILINEAR)

    # Calculate the lower and upper percentiles for display normalization
    lower_percentile = 2.5
    upper_percentile = 97.5
    lower_value = np.percentile(scaled_image, lower_percentile)
    upper_value = np.percentile(scaled_image, upper_percentile)

    # Clip the pixel values to the middle 95% range and normalize to [0, 1]
    clipped_image = np.clip(scaled_image, lower_value, upper_value)
    normalized_image = (clipped_image - lower_value) / (upper_value - lower_value)

    # Scale the normalized values to the full range of 8-bit grayscale values
    scaled_image = (normalized_image * 255).astype(np.uint8)

    # Create the QImage directly from the numpy array
    qimage = QImage(scaled_image.data, new_width, new_height, QImage.Format_Grayscale8)

    end_time = time.time()  # Record the end time
    elapsed_time = end_time - start_time  # Calculate the elapsed time

    print("Image converted, time taken:", elapsed_time, "seconds")  # Print the elapsed time

    return qimage


# def convert_to_qimage(image_data):
#     """
#     Convert a 16-bit grayscale image to QImage, resizing the image to 512x512 using bilinear interpolation.
#     """
#     start_time = time.time()  # Record the start time

#     # Resize the image data to 512x512 using bilinear interpolation
#     new_height, new_width = 512, 512

#     resized_image = transform.resize(image_data, (new_height, new_width), mode='reflect', anti_aliasing=True)

#     # Calculate the lower and upper percentiles for display normalization
#     lower_percentile = 2.5
#     upper_percentile = 97.5
#     lower_value = np.percentile(resized_image, lower_percentile)
#     upper_value = np.percentile(resized_image, upper_percentile)

#     # Clip the pixel values to the middle 95% range and normalize to [0, 1]
#     clipped_image = np.clip(resized_image, lower_value, upper_value)
#     normalized_image = (clipped_image - lower_value) / (upper_value - lower_value)

#     # Scale the normalized values to the full range of 8-bit grayscale values and convert to uint8
#     scaled_image = (normalized_image * 255).astype(np.uint8)

#     # Save scaled_image as PNG using scikit-image
#     io.imsave("output.png", scaled_image)

#     # Create the QImage directly from the numpy array
#     width, height = new_width, new_height
#     qimage = QImage(scaled_image.data, width, height, QImage.Format_Grayscale8)

#     end_time = time.time()  # Record the end time
#     elapsed_time = end_time - start_time  # Calculate the elapsed time
#     # print('Image converted, time taken:', elapsed_time, 'seconds')  # Print the elapsed time, usually a few hundredths of a second or less

#     return qimage
