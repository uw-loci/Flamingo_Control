#Additional calculations to be used elsewhere, like finding a maxima or focal plane
import numpy as np
#import cv2

def find_most_in_focus_plane(z_stack):
    """
    Finds the most in-focus plane in a Z-stack of 16-bit grayscale images using the sum of intensities.
    
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
    # Calculate the sum of intensities for each plane in the Z-stack
    intensity_sums = np.sum(z_stack, axis=(1, 2))
    
    # Find the index of the plane with the highest intensity sum
    most_in_focus_plane_index = np.argmax(intensity_sums)
    
    return most_in_focus_plane_index

def check_maxima(lst):
    """
    This function takes a list of numbers as input and returns the position of the 
    maximum value of any maxima in the list above a certain threshold determined by 
    the mean and standard deviation of the three smallest values in the list. A maxima 
    is defined as a value in the list that is greater than its neighboring values 
    and above the threshold. If the length of the input list is less than or equal 
    to 2, the function returns False.
    
    Parameters:
    lst (list): A list of numbers
    
    Returns:
    max_pos (int): The position of the maximum value of any maxima in the list above 
                   the threshold, or False if the input list is too short or there are 
                   no maxima meeting the additional requirement.
    """
    
    # Check for edge case where the length of the input list is less than or equal to 2
    if len(lst) < 4:
        return False
    
    # Calculate the threshold for a maxima based on the mean and standard deviation 
    # of the three smallest values in the list
    lst_sorted = sorted(lst)[:4]
    lst_mean = np.mean(lst_sorted)
    lst_std = np.std(lst_sorted)
    #print(f'mean of intensities is {lst_mean}')
    #print(f'std of intensities is {lst_std}')
    threshold = lst_mean + 20 * lst_std
    
    # Find maxima above the threshold with below threshold values on both sides of it
    maxima_above_threshold = []
    for i in range(1, len(lst)-1):
        if lst[i] > threshold and lst[i] > lst[i-1] and lst[i] > lst[i+1]:
            # Check if there are not values above the threshold on both sides of the maxima
            left_below_thresh = False
            right_below_thresh = False
            for j in range(i-1, -1, -1):
                if lst[j] < threshold:
                    left_below_thresh = True
                    break
            for j in range(i+1, len(lst)):
                if lst[j] < threshold:
                    right_below_thresh = True
                    break
            if left_below_thresh and right_below_thresh:
                maxima_above_threshold.append(i)
    
    # Return the position of the maximum value of any maxima above the threshold
    if len(maxima_above_threshold) > 0:
        max_pos = max(maxima_above_threshold, key=lambda x: lst[x])
        print(f'Maxima found {max_pos}')        
        return max_pos
    else:
        return False

# def find_most_in_focus_plane(z_stack):
#     """
#     Finds the most in-focus plane in a Z-stack of 16-bit grayscale images using the Laplacian operator.
    
#     Parameters
#     ----------
#     z_stack : numpy.ndarray
#         A 3D numpy array representing the Z-stack of images, where the first dimension corresponds to
#         the position in the Z-stack, and the second and third dimensions represent the pixel values of
#         the image at that position. The pixel values should be represented as 16-bit integers.
    
#     Returns
#     -------
#     int
#         The index of the most in-focus plane in the Z-stack.
#     """
    
#     # Initialize an empty list to hold the sharpness measures for each image
#     sharpness_measures = []
#     z_stack=z_stack.astype(np.float32)
#     # Calculate the Laplacian for each image in the Z-stack and append the result to the list
#     for i in range(z_stack.shape[0]):
#         dst = np.zeros_like(z_stack[i,:,:], dtype=np.float32) 
#         var_laplacian = cv2.Laplacian(z_stack[i,:,:], cv2.CV_32F, dst).var()
#         sharpness_measures.append(var_laplacian)
    
#     # Find the index of the image with the highest sharpness measure
#     most_in_focus_plane_index = np.argmax(sharpness_measures)
#     print(f'most in focus plane is: {most_in_focus_plane_index}')
#     print(sharpness_measures)
#     return most_in_focus_plane_index


