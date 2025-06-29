# ============================================================================
# src/py2flamingo/controllers/ellipse_controller.py
"""
Controller for ellipse tracing and sample tracking by angle.
"""

import logging
from typing import Optional, List, Tuple
import numpy as np
from pathlib import Path

from models.microscope import Position
from models.ellipse import EllipseModel, EllipseParameters
from controllers.microscope_controller import MicroscopeController
from controllers.sample_controller import SampleController
from services.communication.connection_manager import ConnectionManager
from services.ellipse_tracing_service import EllipseTracingService
import py2flamingo.functions.calculations as calc
import py2flamingo.functions.text_file_parsing as txt


class EllipseController:
    """
    Controller for ellipse-based sample tracking.
    
    Handles sample tracking through rotation and ellipse fitting.
    """
    
    def __init__(self,
                 microscope_controller: MicroscopeController,
                 sample_controller: SampleController,
                 connection_manager: ConnectionManager):
        """
        Initialize ellipse controller.
        
        Args:
            microscope_controller: Main microscope controller
            sample_controller: Sample controller
            connection_manager: Connection manager
        """
        self.microscope = microscope_controller
        self.sample_controller = sample_controller
        self.connection = connection_manager
        self.tracing_service = EllipseTracingService()
        self.logger = logging.getLogger(__name__)
        
        # Current ellipse model
        self.current_ellipse: Optional[EllipseModel] = None
    
    def trace_ellipse(self,
                     sample_name: str,
                     laser_channel: str = "Laser 3 488 nm",
                     laser_power: float = 5.0,
                     angle_increment: float = 45.0,
                     z_search_depth: float = 1.0):
        """
        Trace sample bounds through full rotation.
        
        Args:
            sample_name: Name of sample
            laser_channel: Laser channel to use
            laser_power: Laser power percentage
            angle_increment: Angle increment in degrees
            z_search_depth: Z search depth in mm
        """
        self.logger.info(f"Starting ellipse trace for sample {sample_name}")
        
        try:
            # Initialize tracking points
            top_points = []
            bottom_points = []
            
            # Get starting position
            start_position = self.microscope.get_current_position()
            
            # Trace through full rotation
            angles = np.arange(0, 360, angle_increment)
            
            for angle in angles:
                self.logger.info(f"Tracing at angle {angle}")
                
                # Rotate to angle
                position = Position(
                    x=start_position.x,
                    y=start_position.y,
                    z=start_position.z,
                    r=angle
                )
                self.microscope.move_to_position(position)
                
                # Find sample bounds at this angle
                bounds = self._find_bounds_at_angle(
                    position,
                    z_search_depth,
                    laser_channel,
                    laser_power
                )
                
                if bounds:
                    top_points.append((angle, bounds[0]))
                    bottom_points.append((angle, bounds[1]))
                    
                    # Save bounds
                    self.sample_controller.set_sample_bounds(
                        sample_name,
                        bounds[0],
                        bounds[1],
                        angle
                    )
            
            # Fit ellipses to points
            if len(top_points) >= 5:  # Need at least 5 points for ellipse
                # Create ellipse model
                self.current_ellipse = EllipseModel(sample_name=sample_name)
                
                # Fit top ellipse
                top_params = self.tracing_service.fit_ellipse_to_points(
                    [(p[1].x, p[1].z) for p in top_points]
                )
                self.current_ellipse.top_ellipse = top_params
                
                # Fit bottom ellipse
                bottom_params = self.tracing_service.fit_ellipse_to_points(
                    [(p[1].x, p[1].z) for p in bottom_points]
                )
                self.current_ellipse.bottom_ellipse = bottom_params
                
                # Save ellipse parameters
                self._save_ellipse_params(sample_name)
                
                self.logger.info("Ellipse tracing complete")
            else:
                self.logger.warning("Insufficient points for ellipse fitting")
                
        except Exception as e:
            self.logger.error(f"Ellipse tracing failed: {e}")
            raise
    
    def _find_bounds_at_angle(self,
                             position: Position,
                             z_search_depth: float,
                             laser_channel: str,
                             laser_power: float) -> Optional[Tuple[Position, Position]]:
        """
        Find top and bottom bounds at current angle.
        
        Returns:
            Tuple of (top_position, bottom_position) or None
        """
        # Use sample controller to find sample
        found_pos = self.sample_controller.locate_sample(
            position,
            z_search_depth,
            sample_count=1,
            laser_channel=laser_channel,
            laser_power=laser_power
        )
        
        if not found_pos:
            return None
        
        # Search up for top bound
        top_pos = self._search_bound(
            found_pos,
            direction='up',
            laser_channel=laser_channel,
            laser_power=laser_power
        )
        
        # Search down for bottom bound
        bottom_pos = self._search_bound(
            found_pos,
            direction='down',
            laser_channel=laser_channel,
            laser_power=laser_power
        )
        
        return (top_pos, bottom_pos)
    
    def _search_bound(self,
                     start_pos: Position,
                     direction: str,
                     laser_channel: str,
                     laser_power: float,
                     step_size: float = 0.05,
                     max_steps: int = 40) -> Position:
        """
        Search for sample boundary in given direction.
        
        Args:
            start_pos: Starting position
            direction: 'up' or 'down'
            laser_channel: Laser channel
            laser_power: Laser power
            step_size: Step size in mm
            max_steps: Maximum steps to search
            
        Returns:
            Position of boundary
        """
        z_step = step_size if direction == 'up' else -step_size
        threshold = 50  # Intensity threshold
        
        last_valid_pos = start_pos
        
        for i in range(max_steps):
            # Move to next position
            test_pos = Position(
                x=start_pos.x,
                y=start_pos.y,
                z=start_pos.z + (i + 1) * z_step,
                r=start_pos.r
            )
            
            self.microscope.move_to_position(test_pos)
            
            # Capture and analyze image
            image_data = self.connection.capture_single_image(
                laser_channel,
                laser_power
            )
            
            # Check if sample still visible
            intensity = np.mean(image_data)
            
            if intensity < threshold:
                # Sample no longer visible, return last valid position
                return last_valid_pos
            
            last_valid_pos = test_pos
        
        # Reached max steps
        return last_valid_pos
    
    def predict_position_at_angle(self,
                                 sample_name: str,
                                 angle: float) -> Optional[Position]:
        """
        Predict sample position at given angle using ellipse model.
        
        Args:
            sample_name: Name of sample
            angle: Angle in degrees
            
        Returns:
            Predicted position or None
        """
        # Load ellipse model if not current
        if not self.current_ellipse or self.current_ellipse.sample_name != sample_name:
            self.current_ellipse = self._load_ellipse_model(sample_name)
        
        if not self.current_ellipse:
            self.logger.warning(f"No ellipse model for sample {sample_name}")
            return None
        
        # Load sample bounds for Y position
        sample = self.sample_controller.load_sample_bounds(sample_name)
        if not sample or not sample.bounds_list:
            self.logger.warning(f"No bounds found for sample {sample_name}")
            return None
        
        # Use first bounds for Y reference
        y_pos = sample.bounds_list[0].top.y
        
        # Predict from ellipse
        top_point = self.tracing_service.predict_point_on_ellipse(
            self.current_ellipse.top_ellipse,
            angle
        )
        
        bottom_point = self.tracing_service.predict_point_on_ellipse(
            self.current_ellipse.bottom_ellipse,
            angle
        )
        
        # Calculate center
        x_center = (top_point[0] + bottom_point[0]) / 2
        z_center = (top_point[1] + bottom_point[1]) / 2
        
        return Position(
            x=x_center,
            y=y_pos,
            z=z_center,
            r=angle
        )
    
    def _save_ellipse_params(self, sample_name: str):
        """Save ellipse parameters to file."""
        if not self.current_ellipse:
            return
        
        sample_dir = Path("sample_txt") / sample_name
        sample_dir.mkdir(parents=True, exist_ok=True)
        
        # Save top ellipse
        if self.current_ellipse.top_ellipse:
            top_file = sample_dir / f"top_ellipse_{sample_name}.txt"
            self._write_ellipse_params(top_file, self.current_ellipse.top_ellipse)
        
        # Save bottom ellipse
        if self.current_ellipse.bottom_ellipse:
            bottom_file = sample_dir / f"bottom_ellipse_{sample_name}.txt"
            self._write_ellipse_params(bottom_file, self.current_ellipse.bottom_ellipse)
    
    def _write_ellipse_params(self, filepath: Path, params: EllipseParameters):
        """Write ellipse parameters to file."""
        with open(filepath, 'w') as f:
            f.write(f"Center X: {params.center_x}\n")
            f.write(f"Center Y: {params.center_y}\n")
            f.write(f"Semi-major axis: {params.semi_major}\n")
            f.write(f"Semi-minor axis: {params.semi_minor}\n")
            f.write(f"Rotation: {params.rotation}\n")
    
    def _load_ellipse_model(self, sample_name: str) -> Optional[EllipseModel]:
        """Load ellipse model from file."""
        sample_dir = Path("sample_txt") / sample_name
        
        top_file = sample_dir / f"top_ellipse_{sample_name}.txt"
        bottom_file = sample_dir / f"bottom_ellipse_{sample_name}.txt"
        
        if not top_file.exists() or not bottom_file.exists():
            return None
        
        model = EllipseModel(sample_name=sample_name)
        
        # Load top ellipse
        model.top_ellipse = self._read_ellipse_params(top_file)
        
        # Load bottom ellipse
        model.bottom_ellipse = self._read_ellipse_params(bottom_file)
        
        return model
    
    def _read_ellipse_params(self, filepath: Path) -> Optional[EllipseParameters]:
        """Read ellipse parameters from file."""
        try:
            data = {}
            with open(filepath, 'r') as f:
                for line in f:
                    if ':' in line:
                        key, value = line.split(':', 1)
                        data[key.strip()] = float(value.strip())
            
            return EllipseParameters(
                center_x=data.get('Center X', 0),
                center_y=data.get('Center Y', 0),
                semi_major=data.get('Semi-major axis', 1),
                semi_minor=data.get('Semi-minor axis', 1),
                rotation=data.get('Rotation', 0)
            )
        except Exception as e:
            self.logger.error(f"Failed to read ellipse params from {filepath}: {e}")
            return None