import os
import time

import numpy as np
from PIL import Image
from PyQt5.QtGui import QImage
from skimage import io  # , transform

# TODO scipy.ndimage scikit image



def save_png(image_data, image_title):
    """
    Save a 16-bit 2D numpy array as a downsized PNG image.

    This function takes a 16-bit 2D numpy array 'image_data' and a string 'image_title',
    downsizes the image to 512x512, and saves it as a PNG file in the 'output_png' directory.
    The PNG file will have the name specified by 'image_title'.

    The image data is first normalized to the range [0, 1] by clipping to the 2.5th and 97.5th percentiles
    of the data and scaling accordingly. It is then converted to an 8-bit format and saved as a PNG.

    Parameters:
    image_data (numpy.array): A 2D, 16-bit numpy array representing the image data.
    image_title (str): The title to use when saving the image.

    Returns:
    None
    """
    # Ensure the image data is a numpy array
    image_data = np.array(image_data)

    # Calculate the lower and upper percentiles for display normalization
    lower_percentile = 2.5
    upper_percentile = 97.5
    lower_value = np.percentile(image_data, lower_percentile)
    upper_value = np.percentile(image_data, upper_percentile)

    # Clip the pixel values to the middle 95% range and normalize to [0, 1]
    clipped_image = np.clip(image_data, lower_value, upper_value)
    normalized_image = (clipped_image - lower_value) / (upper_value - lower_value)

    # Convert the normalized image to 8-bit format
    image_8bit = (normalized_image * 255).astype(np.uint8)

    # Create a PIL image object
    image = Image.fromarray(image_8bit)

    # Downsample the image to 512x512
    image_resized = image.resize((512, 512))

    # Define the output directory
    output_dir = "output_png"

    # Create the output directory if it doesn't exist
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # Save the image
    image_resized.save(os.path.join(output_dir, f"{image_title}.png"))


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
    # print(f'image data shape {image_data.shape}')
    # Convert the numpy array to a PIL Image
    image = Image.fromarray(image_data)

    # Convert the image to grayscale mode and resize it to the new dimensions using bilinear interpolation
    scaled_image = image.convert("L").resize(
        (new_width, new_height), resample=Image.BILINEAR
    )

    # Calculate the lower and upper percentiles for display normalization
    lower_percentile = 1
    upper_percentile = 99
    lower_value = np.percentile(scaled_image, lower_percentile)
    upper_value = np.percentile(scaled_image, upper_percentile)
    # print(f'Lower value: {lower_value}')
    # print(f'Upper value: {upper_value}')

    # Clip the pixel values to the middle 95% range and normalize to [0, 1]
    clipped_image = np.clip(scaled_image, lower_value, upper_value)

    epsilon = 1e-7
    normalized_image = (clipped_image - lower_value) / (
        upper_value - lower_value + epsilon
    )
    # print(np.unique(normalized_image))

    # Scale the normalized values to the full range of 8-bit grayscale values
    scaled_image = (normalized_image * 255).astype(np.uint8)

    # Create the QImage directly from the numpy array
    qimage = QImage(scaled_image.data, new_width, new_height, QImage.Format_Grayscale8)

    end_time = time.time()  # Record the end time
    elapsed_time = end_time - start_time  # Calculate the elapsed time

    # print("Image converted, time taken:", elapsed_time, "seconds")  # Print the elapsed time

    return qimage
