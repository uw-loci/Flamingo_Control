# Additional calculations to be used elsewhere, like finding a maxima or focal plane
from typing import Sequence
from sklearn.linear_model import RANSACRegressor
import numpy as np
from scipy.optimize import minimize
from scipy.signal import find_peaks
import scipy.stats as stats

#TODO hard code warning /4
def calculate_background_threshold(data, method="mode_std", percentile_value=10):
    """
    Function to calculate the background threshold using different methods.
    :param data: (list or numpy array) The data from which to calculate the background threshold.
    :param method: (str) The method used to calculate the background. Currently supports "percentile", "mode_std".
    :param percentile_value: (int) The percentile to use if method="percentile". Default is 10.
    :return: background_threshold: (float) The calculated background threshold.
    """
    data = np.array(data)
    if method == "percentile":
        background_threshold = np.percentile(data, percentile_value)
    elif method == "mode_std":
        rounded_data = np.round(data / 5) * 5
        mode = stats.mode(rounded_data)[0][0]
        background_threshold = mode +  np.std(data)/4
    else:
        raise ValueError(f"Unsupported background calculation method: {method}")
    print(background_threshold, mode, np.std(data))
    return background_threshold

def find_peak_bounds(data, method="mode_std", background_percentage=10):
    """
    Finds the indices of the tallest peak in the data that returns to the background level.
    
    Parameters:
    data (list of float): The input data, a list of floating point numbers.
    background_percentage (float, optional): The percentage of the max peak intensity considered as the background level. Default is 10.
    
    Returns:
    tuple of int: The start and end indices of the peak. If no peaks are found or if the peak doesn't return to the background level, 
                  the function returns the start and end of the data list.
    """
    # Convert list to numpy array for efficient operations
    data = np.array(data)

    # Use the scipy function find_peaks to find the indices of the peaks
    peaks, _ = find_peaks(data)
    
    # If no peaks are found, return the start and end of the data list
    if len(peaks) == 0:
        start_index = 0
        end_index = len(data) - 1
    else:
        # Find the tallest peak
        tallest_peak_index = peaks[np.argmax([data[i] for i in peaks])]
        
        # Calculate the background threshold as a percentage of the max intensity
        # Calculate the background threshold
        background_threshold = calculate_background_threshold(data, method, background_percentage)
        if method == "mode_std":
            data = np.round(data / 5) * 5

        #background_threshold = max_intensity * (background_percentage / 100)

        try:
            # Find the index where the data drops below the background threshold before the tallest peak
            start_index = np.where(data[:tallest_peak_index] <= background_threshold)[0][-1]
            # Buffer for imaging
            if start_index > 0:
                start_index = start_index-1
        except IndexError:
            # If there's no data point below the threshold before the tallest peak, use the start of the data list
            start_index = 0
        
        try:
            # Find the index where the data drops below the background threshold after the tallest peak
            end_index = np.where(data[tallest_peak_index:] <= background_threshold)[0][0] + tallest_peak_index
            # Buffer for imaging
            if end_index < len(data) - 1:
                end_index = end_index+1
        except IndexError:
            # If there's no data point below the threshold after the tallest peak, use the end of the data list
            end_index = len(data) - 1

    return start_index, end_index


def find_most_in_focus_plane(z_stack: np.ndarray[np.uint16, Sequence[int]]):
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


def check_maxima(lst: Sequence[int], window_size: int = 5, threshold_factor: int = 15):
    """
    This function takes a list of numbers and returns the position of the maximum value of any maxima
    in the list that are above a certain dynamically calculated threshold.

    The threshold is calculated using a rolling window (default size 5) to determine the mean and standard 
    deviation of the list. The threshold at each position is then determined to be the mean plus a multiple 
    (default factor 5) of the standard deviation.

    A maxima is defined as a value in the list that is greater than both its neighboring values and 
    the threshold. If there are no such maxima, the function returns False.

    Parameters:
    lst (Sequence[int]): A list of numbers
    window_size (int, optional): Size of the rolling window used for calculating the mean and std. dev. Default is 5.
    threshold_factor (int, optional): The multiple of the standard deviation added to the mean to calculate the threshold. Default is 5.

    Returns:
    max_pos (int or False): The position of the maximum value of any maxima in the list above the threshold,
                            or False if there are no such maxima.
    """
    # Handle edge case where the length of the list is less than the window size
    if len(lst) < window_size:
        return False

    # Create an array of thresholds, where each threshold is based on a rolling window of the list
    # For the first few elements where a complete window isn't available, use the mean and standard deviation
    # of the available elements
    initial_thresholds = np.array([np.mean(lst[:i+1]) + threshold_factor * np.std(lst[:i+1]) 
                                   for i in range(window_size)])
    thresholds = np.array([np.mean(lst[i-window_size+1:i+1]) + threshold_factor * np.std(lst[i-window_size+1:i+1]) 
                            for i in range(window_size-1, len(lst))])
    thresholds = np.concatenate((initial_thresholds, thresholds))
    
    print(f'thresholds {thresholds}')

    # Find maxima that are above the threshold with values below the threshold on both sides
    # The list indices are iterated from 1 to len(lst) - 1 because a maxima, by our definition, has to have 
    # both a predecessor and a successor in the list
    maxima_above_threshold = []
    for i in range(1, len(lst) - 1):
        # A maxima is a point that is greater than its neighbors and greater than the threshold
        if lst[i] > thresholds[i] and lst[i] > lst[i - 1] and lst[i] > lst[i + 1]:
            # Check if there are not values above the threshold on both sides of the maxima
            left_below_thresh = False
            right_below_thresh = False
            # Iterate to the left until a value below the threshold is found
            for j in range(i - 1, -1, -1):
                if lst[j] < thresholds[j]:
                    left_below_thresh = True
                    break
            # Iterate to the right until a value below the threshold is found
            for j in range(i + 1, len(lst)):
                if lst[j] < thresholds[j]:
                    right_below_thresh = True
                    break
            # If values below the threshold were found on both sides, add this maxima's position to the list
            if left_below_thresh and right_below_thresh:
                maxima_above_threshold.append(i)

    # If any maxima were found, return the position of the maxima with the greatest value
    if len(maxima_above_threshold) > 0:
        max_pos = max(maxima_above_threshold, key=lambda x: lst[x])
        print(f"Maxima found at position {max_pos}")
        return max_pos
    else:
        # If no maxima were found, return False
        return False


def fit_ellipse(points):
    """
    Fit an ellipse to a given set of float points [[x,y,z,r]] in a plane using a least squares approach.
    
    The ellipse is defined by the equation ((x-h)**2/a**2) + ((z-k)**2/b**2) = 1, where (h, k) is the center 
    of the ellipse, and a and b are the semi-major and semi-minor axes respectively.
    
    Parameters
    ----------
    points : list of tuples
        Each tuple represents a point in the form (x,y,z,r).

    Returns
    -------
    tuple
        A tuple containing the optimized parameters (h, k, a, b).
    """
    # Extract x and z coordinates from the points
    x = np.array([point[0] for point in points])
    z = np.array([point[2] for point in points])
    
    # Define the equation of an ellipse in 2D space
    def ellipse(h, k, a, b, x, z):
        return ((x-h)**2/a**2) + ((z-k)**2/b**2) - 1

    # Define the objective function for least squares minimization
    # This is the function we want to minimize.
    def objective(params):
        h, k, a, b = params
        return np.sum(ellipse(h, k, a, b, x, z)**2)

    # Initial guess for parameters h, k, a, and b
    # h and k are set to the mean of x and z coordinates respectively
    # a and b are set to half of the range of x and z coordinates respectively
    p0 = (np.mean(x), np.mean(z), np.ptp(x)/2, np.ptp(z)/2)

    # Perform the minimization using the 'minimize' function from scipy.optimize
    # The function to minimize is 'objective' and the initial guess is 'p0'
    # Bounds are set so that a and b are non-negative
    result = minimize(objective, p0, bounds=((None, None), (None, None), (0, None), (0, None)))
    
    # Return the optimized parameters
    return result.x


def fit_ellipse_with_ransac(points):
    # Convert points to a numpy array
    points = np.array(points)

    # Extract x and z coordinates from the points
    x = points[:, 0]
    z = points[:, 2]

    # Create a RANSAC regressor
    ransac = RANSACRegressor(min_samples=0.85)  # Here, 0.85 indicates that 85% of the data should be inliers
    
    # Fit the RANSAC regressor to the data
    ransac.fit(x.reshape(-1, 1), z)

    # Obtain the inlier mask, i.e., a boolean array indicating whether each data point is an inlier
    inlier_mask = ransac.inlier_mask_

    # Extract inlier points
    inlier_points = points[inlier_mask]

    # Use the inlier points to fit the ellipse
    return fit_ellipse(inlier_points)


def point_on_ellipse(params, angle):
    """
    Given the parameters of an ellipse and an angle, compute the x, z coordinates of the point on the ellipse 
    corresponding to the provided angle.
    
    Parameters
    ----------
    params : tuple
        A tuple containing the parameters of the ellipse (h, k, a, b).
    angle : float
        The angle in degrees.

    Returns
    -------
    tuple
        A tuple containing the x, z coordinates of the point.
    """
    # Extract the parameters
    h, k, a, b = params
    
    # Convert the angle to radians
    angle_rad = np.deg2rad(angle)
    
    # Compute the x, z coordinates
    x = h + a * np.cos(angle_rad)
    z = k + b * np.sin(angle_rad)
    
    return x, z

def calculate_rolling_x_intensity(image_data, n_lines: int):
    """
    Calculates the rolling average of line intensities in a single channel image 
    and also computes the mean of the largest quarter of the pixel intensities.

    Parameters:
    image_data (np.array): A 2D numpy array representing the single channel image.
    n_lines (int): Odd integer. The number of lines to consider for the rolling average.

    Returns:
    mean_largest_quarter (float): The mean of the largest quarter of the pixel intensities.
    x_intensity_map (list of tuples): A list of tuples where each tuple consists of the line 
                                      position along the X axis and the corresponding rolling 
                                      average intensity.
    """
    # Flatten the image data to a 1D array
    flattened = image_data.flatten()
    
    # Sort the flattened array
    sorted_array = np.sort(flattened)
    
    # Determine the index to slice the array to keep the largest quarter of values
    slice_index = len(sorted_array) // 4
    
    # Calculate the mean of the largest quarter of the pixel intensities
    mean_largest_quarter = np.mean(sorted_array[-slice_index:])

    # Initialize the list that will hold the rolling average intensities
    x_intensity_map = []

    # Calculate the size of the half window for the rolling average
    half_window = n_lines // 2

    # Add padding to the image data beyond the edge along the X direction
    padded_image_data = np.pad(image_data, ((0, 0), (half_window, half_window)), mode='edge')

    # Iterate over each line in the image data
    for i in range(half_window, image_data.shape[1] + half_window):
        # Calculate the mean intensity of the lines in the current window
        mean_intensity = np.mean(padded_image_data[:, i - half_window: i + half_window + 1])
        
        # Add the line position and its corresponding rolling average intensity to the map
        x_intensity_map.append((i - half_window, mean_intensity))

    return mean_largest_quarter, x_intensity_map


def calculate_rolling_y_intensity(image_data, n_lines: int):
    """
    Calculates the rolling average of line intensities in a single channel image 
    and also computes the mean of the largest quarter of the pixel intensities.

    Parameters:
    image_data (np.array): A 2D numpy array representing the single channel image.
    n_lines (int): Odd integer. The number of lines to consider for the rolling average.

    Returns:
    mean_largest_quarter (float): The mean of the largest quarter of the pixel intensities.
    y_intensity_map (list of tuples): A list of tuples where each tuple consists of the line 
                                      position along the Y axis and the corresponding rolling 
                                      average intensity.
    """
    # Flatten the image data to a 1D array
    flattened = image_data.flatten()
    
    # Sort the flattened array
    sorted_array = np.sort(flattened)
    
    # Determine the index to slice the array to keep the largest quarter of values
    slice_index = len(sorted_array) // 4
    
    # Calculate the mean of the largest quarter of the pixel intensities
    mean_largest_quarter = np.mean(sorted_array[-slice_index:])

    # Initialize the list that will hold the rolling average intensities
    y_intensity_map = []

    # Calculate the size of the half window for the rolling average
    half_window = n_lines // 2

    # Add padding to the image data beyond the edge along the Y direction
    padded_image_data = np.pad(image_data, ((half_window, half_window), (0, 0)), mode='edge')

    # Iterate over each line in the image data
    for i in range(half_window, image_data.shape[0] + half_window):
        # Calculate the mean intensity of the lines in the current window
        mean_intensity = np.mean(padded_image_data[i - half_window: i + half_window + 1])
        
        # Add the line position and its corresponding rolling average intensity to the map
        y_intensity_map.append((i - half_window, mean_intensity))

    return mean_largest_quarter, y_intensity_map






# def calculate_rolling_y_intensity(image_data, n_lines):
    # Flatten the array to a 1D array
    flattened = image_data.flatten()

    # Sort the flattened array in descending order
    sorted_array = np.sort(flattened)[::-1]

    # Determine the index to slice the array to keep the largest quarter of values
    slice_index = len(sorted_array) // 4

    # Slice the sorted array to keep only the largest quarter of values
    largest_quarter = sorted_array[:slice_index]

    # Calculate the mean of the largest quarter
    mean_largest_quarter = np.mean(largest_quarter)

    # Calculate the rolling average of n_lines lines along the Y axis
    y_intensity_map = []

    # Mirror the intensities for values beyond the edge of the image in the Y direction only
    padded_image_data = np.pad(image_data, ((n_lines // 2, n_lines - n_lines // 2 - 1), (0, 0)), mode='reflect')
    #print(padded_image_data)
   # Iterate over each line in the image
    for i in range(image_data.shape[0]):
        # Calculate the start and end indices for the rolling average
        start_index = i
        end_index = i+(n_lines)

        # Slice the padded image data to get the current set of lines
        lines = padded_image_data[start_index:end_index, :]
        #print(lines)
        # Calculate the mean intensity of the current set of lines
        mean_intensity = np.mean(lines)

        # Calculate the corresponding y position for the center line of the rolling average
        y_position = i

        # Append the pair of values to the y_intensity_map
        y_intensity_map.append((y_position, mean_intensity))

    return mean_largest_quarter, y_intensity_map