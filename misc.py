import numpy as np
import cv2

def find_most_in_focus_plane(z_stack):
    """
    Finds the most in-focus plane in a Z-stack of 16-bit grayscale images using the Laplacian operator.
    
    Parameters
    ----------
    z_stack : numpy.ndarray
        A 3D numpy array representing the Z-stack of images, where the first dimension corresponds to
        the position in the Z-stack, and the second and third dimensions represent the pixel values of
        the image at that position. The pixel values should be represented as 16-bit integers.
    
    Returns
    -------
    int
        The index of the most in-focus plane in the Z-stack.
    """
    
    # Initialize an empty list to hold the sharpness measures for each image
    sharpness_measures = []
    
    # Calculate the Laplacian for each image in the Z-stack and append the result to the list
    for i in range(z_stack.shape[0]):
        laplacian = cv2.Laplacian(z_stack[i,:,:], cv2.CV_16S)
        sharpness_measure = np.mean(np.abs(laplacian))
        sharpness_measures.append(sharpness_measure)
    
    # Find the index of the image with the highest sharpness measure
    most_in_focus_plane_index = np.argmax(sharpness_measures)
    
    return most_in_focus_plane_index
    
    return most_in_focus_plane_index
def check_maxima(lst):
    """
    This function takes a list of numbers as input and returns the maximum value 
    of any maxima in the list above a certain threshold determined by the mean 
    and standard deviation of the list. A maxima is defined as a value in the 
    list that is greater than its neighboring values and above the threshold.
    If the length of the input list is less than or equal to 2, the function 
    returns False.
    
    Parameters:
    lst (list): A list of numbers
    
    Returns:
    max_value (float): The maximum value of any maxima in the list above the threshold, 
                       or False if the input list is too short.
    """
    
    # Check for edge case where the length of the input list is less than or equal to 2
    if len(lst) <= 3:
        return False
    
    # Calculate the mean and standard deviation of the input list
    lst_mean = np.mean(lst)
    lst_std = np.std(lst)
    
    # Determine the threshold for a maxima
    threshold = lst_mean + 4 * lst_std
    
    # Find maxima above the threshold
    maxima_above_threshold = []
    for i in range(1, len(lst)-1):
        if lst[i] > threshold and lst[i] > lst[i-1] and lst[i] > lst[i+1]:
            maxima_above_threshold.append(lst[i])
    
    # Return the maximum value of any maxima above the threshold
    if len(maxima_above_threshold) > 0:
        return np.max(maxima_above_threshold)
    else:
        return False