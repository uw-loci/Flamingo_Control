# Additional calculations to be used elsewhere, like finding a maxima or focal plane
from typing import Sequence
from sklearn.linear_model import RANSACRegressor
import numpy as np
from scipy.optimize import minimize
from scipy.signal import find_peaks
import scipy.stats as stats
from scipy.ndimage import gaussian_filter1d
from itertools import combinations
import csv
import statistics
import math
def adjust_peak_bounds(bounds, data):
    """
    For small data sets, bump the bounds out by 1, except for edge cases - the literal edge, and TODO touching bounds
    """
    if len(data) < 256:
        for bound in bounds:
            if bound[0] != 0:
                bound[0] = bound[0]-1
            if bound[1] != len(data)-1:
                bound[1] = bound[1]+1
    return bounds

#TODO hardcoded 5 for prominence
def process_data(data, smoothing_sigma=None, background_pct=10):
    """
    Process a list of data points by applying Gaussian smoothing and subtracting the background level.

    Parameters
    ----------
    data : list
        The list of data points to process.
    smoothing_sigma : float, optional
        The standard deviation for Gaussian kernel. The default is None, which means no smoothing is applied.
    background_pct : int, optional
        The percentile of the data that should be considered as the background level. The default is 10, which means the lowest 10% of the data is considered as the background.

    Returns
    -------
    processed_data : list
        The processed data, where Gaussian smoothing has been applied and the background level has been subtracted.
    """
    #print(f"data: {data}")
    # Apply Gaussian smoothing if a sigma value is provided
    if smoothing_sigma:
        data = gaussian_filter1d(data, smoothing_sigma)
        # Uncomment the following line to print the smoothed data for debugging
        # print(data)

    # Round the smoothed data to the nearest integer for simplicity
    smoothed_data = [round(x) for x in data]
    #print(f"smoothed data: {smoothed_data}")
    # Calculate the background level as the given percentile of the smoothed data
    background_level = np.percentile(smoothed_data, background_pct)
    return smoothed_data - background_level



def find_peak_bounds(data, num_peaks=1, threshold_pct=10, sample_intensity_above_background = 10):
    """
    Find the bounds of peaks in a list of data points.

    The function first applies Gaussian smoothing and subtracts the background level from the data. It then finds all peaks in the data and selects the ones that maximize the average distance between peaks. The bounds of each peak are determined by moving to the left and right from the peak until the data value drops below a certain threshold.

    Parameters
    ----------
    data : list
        The list of data points to process.
    num_peaks : int, optional
        The number of expected peaks in the data. The default is 1.
    threshold_pct : int, optional
        The percentage of the peak value that should be used as the threshold for determining the bounds of the peak. The default is 5.

    Returns
    -------
    peak_bounds : list of tuples
        A list of tuples where each tuple contains the start and end indices of a peak. If no peaks are found, or if the start or end of the list is reached before a peak bound is found, the function returns None.
    """
    # Process the data by applying Gaussian smoothing and subtracting the background level
    smooth = 5 if len(data) > 1000 else None
    data = process_data(data, smooth)
    #print(f'data length {len(data)}')
    #print_list_summary(data)
    #print(data)
    all_peaks, _ = find_peaks(data, height = sample_intensity_above_background)

    #For help determining issues with large data sets
    # filtered_list = data[::10]
    # with open('floats.csv', 'w', newline='') as f:
    #     writer = csv.writer(f)
    #     writer.writerow(filtered_list)
    # If no peaks are found, return None

    if len(all_peaks) == 0:
        print('No peaks found')
        return [[None, None]]
    print(f'Peak found {all_peaks}, value {data[all_peaks]}')

    # If more peaks are found than expected, select the ones that maximize the average distance between peaks
    if len(all_peaks) > num_peaks:
        # If only one peak is expected, keep the highest peak
        if num_peaks == 1:
            peaks = [all_peaks[np.argmax(data[all_peaks])]]
        else:
            # Otherwise, select the num_peaks peaks that maximize the average distance between peaks
            peak_combinations = list(combinations(all_peaks, num_peaks))
            avg_distances = [np.mean(np.diff(peak_comb)) for peak_comb in peak_combinations]
            max_distance_index = np.argmax(avg_distances)
            peaks = list(peak_combinations[max_distance_index])
    else:
        peaks = all_peaks

    # Initialize list to store peak bounds
    peak_bounds = []
    #print(f'peaks {peaks}')
    # Loop over each peak
    for i, peak in enumerate(peaks):
        # Initialize start and end of peak
        start = peak
        end = peak

        # Calculate threshold value
        threshold_value = data[peak] * threshold_pct / 100

        # Move start to the left until it's no longer part of the peak
        while start > 0 and data[start-1] > threshold_value:
            start -= 1

        # If start of list is reached before bound is found, return None
        if start == 0 and data[start] > threshold_value:
            return [[None, None]]
        # If there's a previous peak and the current start is to the left of the previous peak's end, set the start to be the previous peak's end 
        if i > 0 and start < peak_bounds[i-1][1]:
            start = peak_bounds[i-1][1]

        # Move end to the right until it's no longer part of the peak
        while end < len(data)-1 and data[end+1] > threshold_value:
            end += 1

        # If end of list is reached before bound is found, return None
        if end == len(data)-1 and data[end] > threshold_value:
            return [[None, None]]

        # If there's another peak and the next peak is before the current 'end', use the lowest point between the two peaks as the end of this peak
        if i < len(peaks) - 1 and peaks[i+1] < end:
            next_peak = peaks[i+1]
            min_point = np.argmin(data[peak:next_peak]) + peak
            end = min_point


        # Append peak bounds to list
        peak_bounds.append((start, end))

    return peak_bounds

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

    return np.argmax(intensity_sums)


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

    #print(f'thresholds {thresholds}')

    # Find maxima that are above the threshold with values below the threshold on both sides
    # The list indices are iterated from 1 to len(lst) - 1 because a maxima, by our definition, has to have 
    # both a predecessor and a successor in the list
    maxima_above_threshold = []
    for i in range(1, len(lst) - 1):
        # A maxima is a point that is greater than its neighbors and greater than the threshold
        if lst[i] > thresholds[i] and lst[i] > lst[i - 1] and lst[i] > lst[i + 1]:
            left_below_thresh = any(lst[j] < thresholds[j] for j in range(i - 1, -1, -1))
            right_below_thresh = any(
                lst[j] < thresholds[j] for j in range(i + 1, len(lst))
            )
            # If values below the threshold were found on both sides, add this maxima's position to the list
            if left_below_thresh and right_below_thresh:
                maxima_above_threshold.append(i)

    if not maxima_above_threshold:
        # If no maxima were found, return False
        return False
    max_pos = max(maxima_above_threshold, key=lambda x: lst[x])
    print(f"Maxima {lst[max_pos]} found at position {max_pos} out of {len(lst)}")
    return max_pos


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
    # Convert params to float and extract the parameters
    h, k, a, b = [float(param) for param in params]
    
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
    Calculates the rolling average of the brightest quarter of line intensities in a single channel image 
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
        # Calculate the mean intensity of the brightest quarter of the lines in the current window
        # First, sort the window values
        window_values = np.sort(padded_image_data[i - half_window: i + half_window + 1].flatten())
        
        # Then, keep the brightest quarter or at least one pixel
        slice_index_window = max(len(window_values) // 4, 1)
        top_quarter_window = window_values[-slice_index_window:]
        
        # Calculate the mean of the brightest quarter
        mean_intensity = np.mean(top_quarter_window)

        # Add the line position and its corresponding rolling average intensity to the map
        y_intensity_map.append((i - half_window, mean_intensity))

    return mean_largest_quarter, y_intensity_map



def print_list_summary(data):
    if len(data) > 0:
        # Calculate summary statistics if the list is not empty
        mean = statistics.mean(data)
        median = statistics.median(data)
        min_value = min(data)
        max_value = max(data)

        # Handle standard deviation calculation
        stdev = statistics.stdev(data) if len(data) > 1 else math.nan
        # Print the summary statistics
        print("Mean:", mean)
        print("Median:", median)
        print("Standard Deviation:", stdev)
        print("Minimum Value:", min_value)
        print("Maximum Value:", max_value)
    else:
        # Handle empty list case
        print("The list is empty.")

def find_center(top_bounds_mm, bottom_bounds_mm, shift = None):
    """
    Find the center point between two points in 3D space.
    Input, two lists of [x,y,z,r], output, one list of the same
    Shift accounts for the frame shift needed to place the object in the center of the field of view.
    Otherwise, the top left corner is the center of the object.
    """

    centerpoint_mm = [
        (float(top) + float(bottom)) / 2
        for top, bottom in zip(top_bounds_mm, bottom_bounds_mm)
    ]
    if shift:
        centerpoint_mm = shift_frame(centerpoint_mm, shift)

    return centerpoint_mm

def shift_frame(point, frameshift):
    """
    Takes in a point [x,y,z,r] that generally represents the centroid of an object, and shifts the point so that the object will show up in the center of the screen by moving it half a screen towards the center X and Y
    Returns a modified xyzr list.
    """
    point[1] -= frameshift
    point[0] -= frameshift
    return point


def bounding_point_from_angle(points_list, angle):
    """
    Interpolate a point at a given angle from a list of bounding points. The function handles 
    the cyclic nature of angles, ensuring interpolation is always between two closest points 
    even if they wrap around the 0°/360° boundary.

    Parameters
    ----------
    points_list : list
        A list of lists where each sublist is a point [x, y, z, r].
    angle : float
        The target angle.

    Returns
    -------
    list
        A list containing the interpolated x, y, z, and r values for the target angle.
    """

    # Convert all elements in points_list to float
    points_list = [[float(x) for x in point] for point in points_list]
    angle = float(angle)

    # Sort the points by their angle (r value)
    sorted_points = sorted(points_list, key=lambda p: p[3])

    lower_point, upper_point = None, None

    # Search for two closest points such that one has an angle less than the target 
    # and the other has an angle more than the target.
    for i, point in enumerate(sorted_points):
        if point[3] > angle:
            lower_point = sorted_points[i-1]
            upper_point = point
            break

    # If not found, it means there's a wrap-around. The points with highest and lowest 
    # angles are chosen as the bounding points.
    if lower_point is None and upper_point is None:
        lower_point = sorted_points[-1]
        upper_point = sorted_points[0]

    # Perform linear interpolation for x, y, and z
    interpolated_point = []
    for i in range(3):  # Iterate over the indices for x, y, z
        lower_value = lower_point[i]
        upper_value = upper_point[i]
        if upper_point[3] != lower_point[3]:  # Prevent division by zero
            interpolated_value = lower_value + ((upper_value - lower_value) / (upper_point[3] - lower_point[3])) * (angle - lower_point[3])
        else:  # If the points have the same angle, just use the lower value
            interpolated_value = lower_value
        interpolated_point.append(interpolated_value)

    # Append the input angle to the list
    interpolated_point.append(angle)

    return interpolated_point

