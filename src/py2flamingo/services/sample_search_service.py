# services/sample_search_service.py
"""
Service implementing sample detection and search algorithms.

This service encapsulates the image processing and analysis
algorithms used to detect samples within microscope images.
"""
import numpy as np
from typing import List, Optional, Tuple
from dataclasses import dataclass

@dataclass
class PeakInfo:
    """
    Information about a detected intensity peak.
    
    Attributes:
        position: Position index in the data array
        intensity: Peak intensity value
        bounds: Tuple of (start, end) indices for peak bounds
        prominence: Peak prominence (height above background)
    """
    position: int
    intensity: float
    bounds: Tuple[int, int]
    prominence: float

class SampleSearchService:
    """
    Service for detecting samples in microscope image data.
    
    This service implements various algorithms for finding samples
    based on fluorescence intensity patterns in image stacks.
    
    Attributes:
        smoothing_sigma: Gaussian smoothing parameter for noise reduction
        background_percentile: Percentile for background estimation
        peak_threshold_factor: Factor for peak detection threshold
    """
    
    def __init__(self,
                 smoothing_sigma: float = 5.0,
                 background_percentile: int = 10,
                 peak_threshold_factor: float = 15.0):
        """
        Initialize the sample search service.
        
        Args:
            smoothing_sigma: Standard deviation for Gaussian smoothing
            background_percentile: Percentile to use for background level
            peak_threshold_factor: Multiplier for threshold above background
        """
        self.smoothing_sigma = smoothing_sigma
        self.background_percentile = background_percentile
        self.peak_threshold_factor = peak_threshold_factor
        
    def find_sample_bounds_in_stack(self, 
                                   image_stack: np.ndarray,
                                   axis: str = 'y',
                                   num_samples: int = 1) -> List[PeakInfo]:
        """
        Find sample boundaries in an image stack along specified axis.
        
        This method analyzes intensity projections along the specified
        axis to detect fluorescent samples based on intensity peaks.
        
        Args:
            image_stack: 3D numpy array of images (z, y, x)
            axis: Axis to analyze ('x', 'y', or 'z')
            num_samples: Expected number of samples to find
            
        Returns:
            List[PeakInfo]: Information about detected peaks
            
        Raises:
            ValueError: If axis is invalid or image_stack has wrong dimensions
        """
        # Validate inputs
        if image_stack.ndim != 3:
            raise ValueError(f"Expected 3D array, got {image_stack.ndim}D")
            
        if axis not in ['x', 'y', 'z']:
            raise ValueError(f"Invalid axis '{axis}', must be 'x', 'y', or 'z'")
            
        # Calculate intensity projection
        intensity_profile = self._calculate_intensity_profile(image_stack, axis)
        
        # Preprocess data
        processed_data = self._preprocess_intensity_data(intensity_profile)
        
        # Find peaks
        peaks = self._find_peaks_with_bounds(processed_data, num_samples)
        
        return peaks
        
    def _calculate_intensity_profile(self, 
                                   image_stack: np.ndarray, 
                                   axis: str) -> np.ndarray:
        """
        Calculate intensity profile along specified axis.
        
        Args:
            image_stack: 3D image array
            axis: Axis to project along
            
        Returns:
            np.ndarray: 1D intensity profile
        """
        axis_map = {'x': 2, 'y': 1, 'z': 0}
        axis_idx = axis_map[axis]
        
        # Take maximum projection along other axes
        other_axes = tuple(i for i in range(3) if i != axis_idx)
        max_projection = np.max(image_stack, axis=other_axes)
        
        return max_projection
        
    def _preprocess_intensity_data(self, data: np.ndarray) -> np.ndarray:
        """
        Preprocess intensity data with smoothing and background subtraction.
        
        Args:
            data: Raw intensity data
            
        Returns:
            np.ndarray: Processed data ready for peak detection
        """
        from scipy.ndimage import gaussian_filter1d
        
        # Apply Gaussian smoothing if data is large enough
        if len(data) > 256 and self.smoothing_sigma > 0:
            smoothed = gaussian_filter1d(data, self.smoothing_sigma)
        else:
            smoothed = data.copy()
            
        # Subtract background
        background = np.percentile(smoothed, self.background_percentile)
        processed = smoothed - background
        
        # Ensure non-negative
        processed[processed < 0] = 0
        
        return processed