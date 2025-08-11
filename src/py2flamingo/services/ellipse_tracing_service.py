# ============================================================================
# src/py2flamingo/services/ellipse_tracing_service.py
"""
Service for ellipse fitting and trajectory prediction.
"""

import numpy as np
from typing import List, Tuple, Optional
import logging
from scipy.optimize import least_squares
from sklearn.decomposition import PCA

from ..models.ellipse import EllipseParameters


class EllipseTracingService:
    """
    Service for ellipse-based sample tracking.
    
    Provides algorithms for fitting ellipses to sample boundaries
    and predicting sample positions at different angles.
    """
    
    def __init__(self):
        """Initialize ellipse tracing service."""
        self.logger = logging.getLogger(__name__)
    
    def fit_ellipse_to_points(self, 
                             points: List[Tuple[float, float]]) -> Optional[EllipseParameters]:
        """
        Fit ellipse to a set of 2D points.
        
        Args:
            points: List of (x, y) coordinates
            
        Returns:
            Fitted ellipse parameters or None if fit fails
        """
        if len(points) < 5:
            self.logger.warning("Need at least 5 points for ellipse fitting")
            return None
        
        try:
            # Convert to numpy array
            points_array = np.array(points)
            
            # Initial guess using PCA
            initial_params = self._get_initial_ellipse_guess(points_array)
            
            # Optimize ellipse parameters
            result = least_squares(
                self._ellipse_residuals,
                initial_params,
                args=(points_array,),
                method='lm'
            )
            
            if result.success:
                # Extract parameters
                cx, cy, a, b, theta = result.x
                
                # Ensure semi-major axis is larger
                if b > a:
                    a, b = b, a
                    theta += np.pi / 2
                
                # Normalize angle to [0, 2π]
                theta = theta % (2 * np.pi)
                
                return EllipseParameters(
                    center_x=cx,
                    center_y=cy,
                    semi_major=abs(a),
                    semi_minor=abs(b),
                    rotation=np.degrees(theta)
                )
            else:
                self.logger.warning("Ellipse fitting failed to converge")
                return None
                
        except Exception as e:
            self.logger.error(f"Error fitting ellipse: {e}")
            return None
    
    def _get_initial_ellipse_guess(self, points: np.ndarray) -> np.ndarray:
        """Get initial ellipse parameters using PCA."""
        # Center points
        center = np.mean(points, axis=0)
        centered_points = points - center
        
        # PCA to find principal axes
        pca = PCA(n_components=2)
        pca.fit(centered_points)
        
        # Project points onto principal axes
        transformed = pca.transform(centered_points)
        
        # Estimate semi-axes from ranges
        a = np.max(np.abs(transformed[:, 0]))
        b = np.max(np.abs(transformed[:, 1]))
        
        # Rotation angle from first principal component
        theta = np.arctan2(pca.components_[0, 1], pca.components_[0, 0])
        
        return np.array([center[0], center[1], a, b, theta])
    
    def _ellipse_residuals(self, params: np.ndarray, points: np.ndarray) -> np.ndarray:
        """Calculate residuals for ellipse fitting."""
        cx, cy, a, b, theta = params
        
        # Rotate points to ellipse coordinate system
        cos_t = np.cos(theta)
        sin_t = np.sin(theta)
        
        # Translate to origin
        dx = points[:, 0] - cx
        dy = points[:, 1] - cy
        
        # Rotate
        x_rot = dx * cos_t + dy * sin_t
        y_rot = -dx * sin_t + dy * cos_t
        
        # Calculate ellipse equation residuals
        residuals = (x_rot / a) ** 2 + (y_rot / b) ** 2 - 1
        
        return residuals
    
    def predict_point_on_ellipse(self,
                               ellipse: EllipseParameters,
                               angle_deg: float) -> Tuple[float, float]:
        """
        Predict point on ellipse at given angle.
        
        Args:
            ellipse: Ellipse parameters
            angle_deg: Angle in degrees
            
        Returns:
            (x, y) coordinates on ellipse
        """
        return ellipse.point_at_angle(angle_deg)
    
    def interpolate_ellipse_trajectory(self,
                                     ellipse: EllipseParameters,
                                     start_angle: float,
                                     end_angle: float,
                                     num_points: int) -> List[Tuple[float, float]]:
        """
        Interpolate trajectory along ellipse.
        
        Args:
            ellipse: Ellipse parameters
            start_angle: Start angle in degrees
            end_angle: End angle in degrees
            num_points: Number of points to interpolate
            
        Returns:
            List of (x, y) coordinates
        """
        # Handle angle wraparound
        if end_angle < start_angle:
            end_angle += 360
        
        # Generate angles
        angles = np.linspace(start_angle, end_angle, num_points)
        
        # Calculate points
        trajectory = []
        for angle in angles:
            point = self.predict_point_on_ellipse(ellipse, angle % 360)
            trajectory.append(point)
        
        return trajectory
    
    def evaluate_ellipse_fit_quality(self,
                                   ellipse: EllipseParameters,
                                   points: List[Tuple[float, float]]) -> float:
        """
        Evaluate quality of ellipse fit.
        
        Args:
            ellipse: Fitted ellipse parameters
            points: Original points
            
        Returns:
            Quality metric (0-1, higher is better)
        """
        if not points:
            return 0.0
        
        # Calculate distances from points to ellipse
        distances = []
        
        for x, y in points:
            # Find closest point on ellipse
            # This is approximate - uses angle from center
            dx = x - ellipse.center_x
            dy = y - ellipse.center_y
            angle = np.degrees(np.arctan2(dy, dx))
            
            # Get point on ellipse at this angle
            ellipse_x, ellipse_y = ellipse.point_at_angle(angle)
            
            # Calculate distance
            dist = np.sqrt((x - ellipse_x)**2 + (y - ellipse_y)**2)
            distances.append(dist)
        
        # Convert to quality metric
        mean_dist = np.mean(distances)
        
        # Normalize by ellipse size
        ellipse_size = (ellipse.semi_major + ellipse.semi_minor) / 2
        normalized_error = mean_dist / ellipse_size if ellipse_size > 0 else 1.0
        
        # Convert to 0-1 quality (exponential decay)
        quality = np.exp(-normalized_error * 5)
        
        return float(quality)
    
    def merge_ellipse_data(self,
                         ellipses: List[EllipseParameters],
                         weights: Optional[List[float]] = None) -> Optional[EllipseParameters]:
        """
        Merge multiple ellipse fits into one.
        
        Args:
            ellipses: List of ellipse parameters
            weights: Optional weights for each ellipse
            
        Returns:
            Merged ellipse parameters
        """
        if not ellipses:
            return None
        
        if len(ellipses) == 1:
            return ellipses[0]
        
        # Default equal weights
        if weights is None:
            weights = [1.0] * len(ellipses)
        
        # Normalize weights
        total_weight = sum(weights)
        weights = [w / total_weight for w in weights]
        
        # Weighted average of parameters
        center_x = sum(e.center_x * w for e, w in zip(ellipses, weights))
        center_y = sum(e.center_y * w for e, w in zip(ellipses, weights))
        semi_major = sum(e.semi_major * w for e, w in zip(ellipses, weights))
        semi_minor = sum(e.semi_minor * w for e, w in zip(ellipses, weights))
        
        # Average rotation (careful with angle wraparound)
        sin_sum = sum(np.sin(np.radians(e.rotation)) * w 
                     for e, w in zip(ellipses, weights))
        cos_sum = sum(np.cos(np.radians(e.rotation)) * w 
                     for e, w in zip(ellipses, weights))
        rotation = np.degrees(np.arctan2(sin_sum, cos_sum))
        
        return EllipseParameters(
            center_x=center_x,
            center_y=center_y,
            semi_major=semi_major,
            semi_minor=semi_minor,
            rotation=rotation
        )
    
    def detect_outlier_points(self,
                            ellipse: EllipseParameters,
                            points: List[Tuple[float, float]],
                            threshold_sigma: float = 3.0) -> List[int]:
        """
        Detect outlier points that don't fit the ellipse well.
        
        Args:
            ellipse: Fitted ellipse
            points: Points to check
            threshold_sigma: Number of standard deviations for outlier threshold
            
        Returns:
            List of outlier indices
        """
        if not points:
            return []
        
        # Calculate distances
        distances = []
        for x, y in points:
            # Approximate distance to ellipse
            dx = x - ellipse.center_x
            dy = y - ellipse.center_y
            angle = np.degrees(np.arctan2(dy, dx))
            
            ellipse_x, ellipse_y = ellipse.point_at_angle(angle)
            dist = np.sqrt((x - ellipse_x)**2 + (y - ellipse_y)**2)
            distances.append(dist)
        
        distances = np.array(distances)
        
        # Calculate outlier threshold
        mean_dist = np.mean(distances)
        std_dist = np.std(distances)
        threshold = mean_dist + threshold_sigma * std_dist
        
        # Find outliers
        outliers = np.where(distances > threshold)[0]
        
        return outliers.tolist()
    
    def refine_ellipse_fit(self,
                          ellipse: EllipseParameters,
                          points: List[Tuple[float, float]],
                          iterations: int = 3) -> EllipseParameters:
        """
        Refine ellipse fit by iteratively removing outliers.
        
        Args:
            ellipse: Initial ellipse fit
            points: Points to fit
            iterations: Number of refinement iterations
            
        Returns:
            Refined ellipse parameters
        """
        current_points = points.copy()
        current_ellipse = ellipse
        
        for i in range(iterations):
            # Detect outliers
            outliers = self.detect_outlier_points(
                current_ellipse, 
                current_points,
                threshold_sigma=3.0 - i * 0.5  # Decrease threshold each iteration
            )
            
            if not outliers:
                break
            
            # Remove outliers
            current_points = [p for i, p in enumerate(current_points) 
                            if i not in outliers]
            
            # Refit
            if len(current_points) >= 5:
                new_ellipse = self.fit_ellipse_to_points(current_points)
                if new_ellipse:
                    current_ellipse = new_ellipse
                else:
                    break
            else:
                break
        
        return current_ellipse
