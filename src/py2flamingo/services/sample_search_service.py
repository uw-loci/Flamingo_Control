# ============================================================================
# src/py2flamingo/services/sample_search_service.py
"""
Service for sample detection and analysis.
"""

import numpy as np
from typing import List, Tuple, Optional
import logging
from scipy import ndimage
from scipy.signal import find_peaks


class SampleSearchService:
    """
    Service for finding and analyzing samples in images.
    
    Provides image analysis algorithms for sample detection,
    intensity analysis, and peak finding.
    """
    
    def __init__(self):
        """Initialize sample search service."""
        self.logger = logging.getLogger(__name__)
    
    def analyze_image_intensity(self, 
                              image_data: np.ndarray,
                              threshold: float = 50.0) -> float:
        """
        Analyze image intensity for sample presence.
        
        Args:
            image_data: Image array
            threshold: Intensity threshold
            
        Returns:
            Mean intensity above threshold
        """
        # Apply threshold
        above_threshold = image_data > threshold
        
        if np.any(above_threshold):
            return np.mean(image_data[above_threshold])
        else:
            return 0.0
    
    def find_intensity_peaks(self,
                           intensities: List[float],
                           expected_count: int = 1) -> List[int]:
        """
        Find intensity peaks in a list of values.
        
        Args:
            intensities: List of intensity values
            expected_count: Expected number of peaks
            
        Returns:
            List of peak indices
        """
        if not intensities:
            return []
        
        # Convert to numpy array
        intensity_array = np.array(intensities)
        
        # Find peaks
        peaks, properties = find_peaks(
            intensity_array,
            height=np.mean(intensity_array),
            distance=len(intensities) // (expected_count * 2)
        )
        
        # Sort by height and return top N
        if len(peaks) > expected_count:
            peak_heights = properties['peak_heights']
            sorted_indices = np.argsort(peak_heights)[::-1]
            peaks = peaks[sorted_indices[:expected_count]]
        
        return peaks.tolist()
    
    def detect_sample_regions(self,
                            image_data: np.ndarray,
                            threshold: float = 50.0,
                            min_size: int = 100) -> List[Tuple[int, int, int, int]]:
        """
        Detect sample regions in image.
        
        Args:
            image_data: Image array
            threshold: Intensity threshold
            min_size: Minimum region size in pixels
            
        Returns:
            List of (x, y, width, height) bounding boxes
        """
        # Threshold image
        binary = image_data > threshold
        
        # Remove small objects
        binary = ndimage.binary_opening(binary, iterations=2)
        
        # Label connected components
        labeled, num_features = ndimage.label(binary)
        
        regions = []
        for i in range(1, num_features + 1):
            # Get region mask
            mask = labeled == i
            
            # Check size
            if np.sum(mask) < min_size:
                continue
            
            # Get bounding box
            coords = np.where(mask)
            y_min, y_max = coords[0].min(), coords[0].max()
            x_min, x_max = coords[1].min(), coords[1].max()
            
            regions.append((
                int(x_min),
                int(y_min),
                int(x_max - x_min),
                int(y_max - y_min)
            ))
        
        return regions
    
    def calculate_focus_metric(self, image_data: np.ndarray) -> float:
        """
        Calculate focus quality metric.
        
        Args:
            image_data: Image array
            
        Returns:
            Focus metric (higher is better focus)
        """
        # Use variance of Laplacian as focus metric
        laplacian = ndimage.laplace(image_data.astype(float))
        return np.var(laplacian)
    
    def find_best_focus_position(self,
                               z_positions: List[float],
                               focus_metrics: List[float]) -> Optional[float]:
        """
        Find best focus position from metrics.
        
        Args:
            z_positions: List of Z positions
            focus_metrics: Corresponding focus metrics
            
        Returns:
            Best focus Z position or None
        """
        if not z_positions or not focus_metrics:
            return None
        
        # Find maximum focus metric
        best_idx = np.argmax(focus_metrics)
        
        # Try to fit a parabola around the peak for sub-resolution
        if 0 < best_idx < len(z_positions) - 1:
            # Get three points around peak
            x = np.array([z_positions[best_idx - 1],
                         z_positions[best_idx],
                         z_positions[best_idx + 1]])
            y = np.array([focus_metrics[best_idx - 1],
                         focus_metrics[best_idx],
                         focus_metrics[best_idx + 1]])
            
            # Fit parabola
            coeffs = np.polyfit(x, y, 2)
            
            # Find vertex (maximum)
            if coeffs[0] < 0:  # Ensure it's a maximum
                vertex_x = -coeffs[1] / (2 * coeffs[0])
                
                # Check if vertex is within bounds
                if x[0] <= vertex_x <= x[2]:
                    return float(vertex_x)
        
        return float(z_positions[best_idx])
    
    def analyze_z_stack_for_bounds(self,
                                 z_positions: List[float],
                                 images: List[np.ndarray],
                                 threshold: float = 50.0) -> Tuple[Optional[float], Optional[float]]:
        """
        Analyze Z-stack to find sample top and bottom.
        
        Args:
            z_positions: List of Z positions
            images: Corresponding images
            threshold: Intensity threshold
            
        Returns:
            Tuple of (top_z, bottom_z) or (None, None)
        """
        if not z_positions or not images:
            return None, None
        
        # Calculate mean intensity for each image
        intensities = []
        for img in images:
            intensity = self.analyze_image_intensity(img, threshold)
            intensities.append(intensity)
        
        # Find where sample starts and ends
        above_threshold = np.array(intensities) > threshold
        
        if not np.any(above_threshold):
            return None, None
        
        # Find first and last positions above threshold
        indices = np.where(above_threshold)[0]
        
        bottom_z = z_positions[indices[0]]
        top_z = z_positions[indices[-1]]
        
        return top_z, bottom_z
    
    def estimate_sample_volume(self,
                             images: List[np.ndarray],
                             z_spacing_mm: float,
                             pixel_size_mm: float,
                             threshold: float = 50.0) -> float:
        """
        Estimate sample volume from image stack.
        
        Args:
            images: List of images in Z-stack
            z_spacing_mm: Spacing between images
            pixel_size_mm: Pixel size in mm
            threshold: Intensity threshold
            
        Returns:
            Estimated volume in mm³
        """
        total_pixels = 0
        
        for img in images:
            binary = img > threshold
            total_pixels += np.sum(binary)
        
        # Convert to volume
        pixel_volume = pixel_size_mm * pixel_size_mm * z_spacing_mm
        total_volume = total_pixels * pixel_volume
        
        return total_volume