"""Image Service - Unified interface for all image operations.

This module provides a single entry point for all image-related operations,
consolidating the previously scattered image processing code into a clean,
maintainable architecture.

Replaces:
- ImageAcquisitionService (acquisition)
- image_transforms.py (transformations)
- image_processing.py (display conversion)
- Scattered analysis functions in calculations.py
"""

import logging
import numpy as np
from typing import Optional, Dict, Any, List, Tuple, Union
from pathlib import Path
from datetime import datetime

from ..models.data.image import (
    ImageData, ImageMetadata, ImageStack, PixelCalibration, ImageType
)
from ..models.hardware.stage import Position
from ..core.errors import FlamingoError


logger = logging.getLogger(__name__)


class ImageServiceError(FlamingoError):
    """Base exception for image service errors."""
    pass


class AcquisitionError(ImageServiceError):
    """Raised when image acquisition fails."""
    pass


class ProcessingError(ImageServiceError):
    """Raised when image processing fails."""
    pass


class ImageService:
    """Unified service for all image operations.

    This facade provides a single interface for:
    - Image acquisition (snapshots, z-stacks, tiles)
    - Image transformations (rotate, flip, crop, etc.)
    - Image processing (filtering, enhancement, etc.)
    - Image analysis (statistics, projections, measurements)
    - Display preparation (normalization, colormapping, QImage conversion)

    Architecture:
        ImageService (facade) delegates to:
        - ImageAcquisitionPipeline: Hardware acquisition
        - ImageTransformer: Geometric/intensity transformations
        - ImageProcessor: Filtering and enhancement
        - ImageAnalyzer: Measurements and statistics
        - ImageDisplayPrep: Visualization preparation

    Usage Example:
        ```python
        service = ImageService()

        # Acquire image
        image_data = service.acquire_snapshot(position, laser_power=10.0)

        # Process image
        processed = service.apply_transforms(
            image_data.data,
            rotation=90,
            flip_horizontal=True
        )

        # Analyze
        stats = service.get_statistics(processed)
        projection = service.get_max_projection(image_data)

        # Prepare for display
        qimage = service.prepare_for_display(processed)
        ```
    """

    def __init__(self):
        """Initialize image service with all component services."""
        # Lazy initialization to avoid circular dependencies
        self._transformer = None
        self._processor = None
        self._analyzer = None
        self._display_prep = None
        self._acquisition_pipeline = None

        # Configuration
        self._default_exposure_ms = 10.0
        self._default_gain_db = 0.0
        self._default_binning = 1

        # Statistics
        self._stats = {
            'images_acquired': 0,
            'images_processed': 0,
            'total_acquisition_time': 0.0,
            'last_acquisition_time': None
        }

    def _ensure_components(self):
        """Lazily initialize component services."""
        if self._transformer is None:
            from .image_transformer import ImageTransformer
            self._transformer = ImageTransformer()

        if self._processor is None:
            from .image_processor import ImageProcessor
            self._processor = ImageProcessor()

        if self._analyzer is None:
            from .image_analyzer import ImageAnalyzer
            self._analyzer = ImageAnalyzer()

        if self._display_prep is None:
            from .image_display_prep import ImageDisplayPrep
            self._display_prep = ImageDisplayPrep()

        if self._acquisition_pipeline is None:
            from .acquisition_pipeline import AcquisitionPipeline
            self._acquisition_pipeline = AcquisitionPipeline()

    # ==================== Acquisition Operations ====================

    def acquire_snapshot(self, position: Position,
                        laser_channel: Optional[str] = None,
                        laser_power: float = 5.0,
                        exposure_ms: Optional[float] = None,
                        save_data: bool = False) -> ImageData:
        """Acquire a single snapshot image.

        Args:
            position: Stage position for acquisition
            laser_channel: Laser to use
            laser_power: Laser power in mW
            exposure_ms: Camera exposure time
            save_data: Whether to save acquired data

        Returns:
            ImageData with acquired image and metadata

        Raises:
            AcquisitionError: If acquisition fails
        """
        self._ensure_components()

        try:
            start_time = datetime.now()

            # Use workflow facade from Plan 2
            from ..workflows import get_facade
            workflow_facade = get_facade()

            # Create snapshot workflow
            workflow = workflow_facade.create_snapshot(
                position=position,
                laser_channel=laser_channel,
                laser_power=laser_power,
                save_data=save_data
            )

            # Execute acquisition through pipeline
            image_data = self._acquisition_pipeline.execute_snapshot(workflow)

            # Update statistics
            self._stats['images_acquired'] += 1
            acquisition_time = (datetime.now() - start_time).total_seconds()
            self._stats['total_acquisition_time'] += acquisition_time
            self._stats['last_acquisition_time'] = acquisition_time

            logger.info(f"Snapshot acquired in {acquisition_time:.2f}s")
            return image_data

        except Exception as e:
            raise AcquisitionError(f"Failed to acquire snapshot: {e}")

    def acquire_zstack(self, position: Position,
                      num_planes: int,
                      z_step_um: float,
                      laser_channel: Optional[str] = None,
                      laser_power: float = 5.0,
                      bidirectional: bool = False) -> ImageStack:
        """Acquire a z-stack.

        Args:
            position: Starting position
            num_planes: Number of z-planes
            z_step_um: Step size in micrometers
            laser_channel: Laser to use
            laser_power: Laser power in mW
            bidirectional: Use bidirectional scanning

        Returns:
            ImageStack containing all z-planes

        Raises:
            AcquisitionError: If acquisition fails
        """
        self._ensure_components()

        try:
            # Create z-stack workflow using facade from Plan 2
            from ..workflows import get_facade
            workflow_facade = get_facade()

            workflow = workflow_facade.create_zstack(
                position=position,
                num_planes=num_planes,
                z_step_um=z_step_um,
                laser_channel=laser_channel,
                laser_power=laser_power
            )

            if bidirectional:
                workflow.stack_settings.bidirectional = True

            # Execute acquisition
            stack = self._acquisition_pipeline.execute_zstack(workflow)

            self._stats['images_acquired'] += num_planes
            logger.info(f"Z-stack acquired: {num_planes} planes")
            return stack

        except Exception as e:
            raise AcquisitionError(f"Failed to acquire z-stack: {e}")

    def acquire_tile_scan(self, start_position: Position,
                         num_tiles_x: int,
                         num_tiles_y: int,
                         tile_size_mm: float,
                         overlap_percent: float = 10.0) -> List[ImageData]:
        """Acquire a tile scan.

        Args:
            start_position: Top-left starting position
            num_tiles_x: Number of tiles in X
            num_tiles_y: Number of tiles in Y
            tile_size_mm: Size of each tile in mm
            overlap_percent: Overlap between tiles

        Returns:
            List of ImageData for each tile

        Raises:
            AcquisitionError: If acquisition fails
        """
        self._ensure_components()

        try:
            from ..workflows import get_facade
            workflow_facade = get_facade()

            workflow = workflow_facade.create_tile_scan(
                start_position=start_position,
                num_tiles_x=num_tiles_x,
                num_tiles_y=num_tiles_y,
                tile_size_mm=tile_size_mm,
                overlap_percent=overlap_percent
            )

            tiles = self._acquisition_pipeline.execute_tile_scan(workflow)

            self._stats['images_acquired'] += len(tiles)
            logger.info(f"Tile scan acquired: {len(tiles)} tiles")
            return tiles

        except Exception as e:
            raise AcquisitionError(f"Failed to acquire tile scan: {e}")

    # ==================== Transformation Operations ====================

    def rotate_image(self, image: np.ndarray, degrees: int) -> np.ndarray:
        """Rotate image by specified degrees.

        Args:
            image: Input image
            degrees: Rotation angle (0, 90, 180, 270)

        Returns:
            Rotated image
        """
        self._ensure_components()
        return self._transformer.rotate(image, degrees)

    def flip_image(self, image: np.ndarray,
                   horizontal: bool = False,
                   vertical: bool = False) -> np.ndarray:
        """Flip image horizontally and/or vertically.

        Args:
            image: Input image
            horizontal: Flip horizontally
            vertical: Flip vertically

        Returns:
            Flipped image
        """
        self._ensure_components()
        return self._transformer.flip(image, horizontal, vertical)

    def downsample_image(self, image: np.ndarray, factor: int = 2) -> np.ndarray:
        """Downsample image by specified factor.

        Args:
            image: Input image
            factor: Downsampling factor

        Returns:
            Downsampled image
        """
        self._ensure_components()
        return self._transformer.downsample(image, factor)

    def crop_image(self, image: np.ndarray,
                  x: int, y: int, width: int, height: int) -> np.ndarray:
        """Crop image to specified region.

        Args:
            image: Input image
            x: Top-left X coordinate
            y: Top-left Y coordinate
            width: Crop width
            height: Crop height

        Returns:
            Cropped image
        """
        self._ensure_components()
        return self._transformer.crop(image, x, y, width, height)

    def apply_transforms(self, image: np.ndarray,
                        rotation: int = 0,
                        flip_horizontal: bool = False,
                        flip_vertical: bool = False,
                        downsample_factor: int = 1,
                        crop_region: Optional[Tuple[int, int, int, int]] = None) -> np.ndarray:
        """Apply multiple transformations in pipeline.

        Args:
            image: Input image
            rotation: Rotation angle
            flip_horizontal: Flip horizontally
            flip_vertical: Flip vertically
            downsample_factor: Downsampling factor
            crop_region: Optional (x, y, width, height) crop

        Returns:
            Transformed image
        """
        self._ensure_components()
        return self._transformer.apply_pipeline(
            image,
            rotation=rotation,
            flip_horizontal=flip_horizontal,
            flip_vertical=flip_vertical,
            downsample_factor=downsample_factor,
            crop_region=crop_region
        )

    # ==================== Processing Operations ====================

    def normalize_image(self, image: np.ndarray,
                       percentile_low: float = 1.0,
                       percentile_high: float = 99.0) -> np.ndarray:
        """Normalize image using percentile clipping.

        Args:
            image: Input image
            percentile_low: Lower percentile for clipping
            percentile_high: Upper percentile for clipping

        Returns:
            Normalized uint8 image
        """
        self._ensure_components()
        return self._processor.normalize_percentile(
            image, percentile_low, percentile_high
        )

    def apply_colormap(self, image: np.ndarray, colormap: str = "gray") -> np.ndarray:
        """Apply colormap to image.

        Args:
            image: Input image (should be normalized)
            colormap: Colormap name

        Returns:
            Colormapped RGB image
        """
        self._ensure_components()
        return self._processor.apply_colormap(image, colormap)

    def enhance_contrast(self, image: np.ndarray,
                        method: str = "adaptive") -> np.ndarray:
        """Enhance image contrast.

        Args:
            image: Input image
            method: Enhancement method ('adaptive', 'histogram', 'clahe')

        Returns:
            Contrast-enhanced image
        """
        self._ensure_components()
        return self._processor.enhance_contrast(image, method)

    def denoise_image(self, image: np.ndarray,
                     method: str = "gaussian",
                     **kwargs) -> np.ndarray:
        """Denoise image.

        Args:
            image: Input image
            method: Denoising method
            **kwargs: Method-specific parameters

        Returns:
            Denoised image
        """
        self._ensure_components()
        return self._processor.denoise(image, method, **kwargs)

    # ==================== Analysis Operations ====================

    def get_statistics(self, image: Union[np.ndarray, ImageData]) -> Dict[str, float]:
        """Calculate image statistics.

        Args:
            image: Input image or ImageData

        Returns:
            Dictionary with min, max, mean, std, median, etc.
        """
        self._ensure_components()

        if isinstance(image, ImageData):
            return image.get_statistics()
        else:
            return self._analyzer.calculate_statistics(image)

    def get_max_projection(self, stack: Union[np.ndarray, ImageStack],
                          axis: str = 'Z') -> np.ndarray:
        """Create maximum intensity projection.

        Args:
            stack: Image stack
            axis: Axis to project along

        Returns:
            Maximum projection image
        """
        self._ensure_components()

        if isinstance(stack, ImageStack):
            return stack.get_max_projection()
        else:
            return self._analyzer.max_projection(stack, axis)

    def get_mean_projection(self, stack: np.ndarray, axis: int = 0) -> np.ndarray:
        """Create mean intensity projection.

        Args:
            stack: Image stack
            axis: Axis to project along

        Returns:
            Mean projection image
        """
        self._ensure_components()
        return self._analyzer.mean_projection(stack, axis)

    def analyze_intensity_profile(self, image: np.ndarray,
                                 direction: str = 'Y',
                                 n_lines: int = 10) -> np.ndarray:
        """Analyze intensity profile along direction.

        Args:
            image: Input image
            direction: Direction to analyze ('X' or 'Y')
            n_lines: Number of lines to average

        Returns:
            Intensity profile array
        """
        self._ensure_components()
        return self._analyzer.intensity_profile(image, direction, n_lines)

    def find_focus_plane(self, stack: np.ndarray) -> int:
        """Find most in-focus plane in z-stack.

        Args:
            stack: Z-stack array

        Returns:
            Index of best focus plane
        """
        self._ensure_components()
        return self._analyzer.find_best_focus(stack)

    def detect_sample_boundaries(self, image: np.ndarray,
                                num_samples: int = 1,
                                threshold_pct: float = 10.0) -> List[Tuple[int, int]]:
        """Detect sample boundaries in image.

        Args:
            image: Input image
            num_samples: Number of samples to detect
            threshold_pct: Threshold percentage for detection

        Returns:
            List of (start, end) boundary tuples
        """
        self._ensure_components()
        return self._analyzer.detect_boundaries(image, num_samples, threshold_pct)

    # ==================== Display Preparation ====================

    def prepare_for_display(self, image: np.ndarray,
                           normalize: bool = True,
                           percentile_low: float = 1.0,
                           percentile_high: float = 99.0,
                           colormap: Optional[str] = None):
        """Prepare image for display (QImage conversion).

        Args:
            image: Input image
            normalize: Whether to normalize
            percentile_low: Lower percentile
            percentile_high: Upper percentile
            colormap: Optional colormap to apply

        Returns:
            QImage ready for display
        """
        self._ensure_components()
        return self._display_prep.prepare_qimage(
            image,
            normalize=normalize,
            percentile_low=percentile_low,
            percentile_high=percentile_high,
            colormap=colormap
        )

    def export_to_png(self, image: np.ndarray,
                     filepath: Path,
                     thumbnail: bool = False) -> None:
        """Export image to PNG file.

        Args:
            filepath: Output file path
            thumbnail: If True, create small thumbnail
        """
        self._ensure_components()
        self._display_prep.export_png(image, filepath, thumbnail)

    # ==================== Metadata and Containers ====================

    def create_image_data(self, array: np.ndarray,
                         metadata: Optional[ImageMetadata] = None,
                         dimension_order: str = "YX") -> ImageData:
        """Create ImageData from numpy array.

        Args:
            array: Image data
            metadata: Optional metadata
            dimension_order: Dimension order string

        Returns:
            ImageData object
        """
        if metadata is None:
            metadata = ImageMetadata(
                acquisition_time=datetime.now(),
                exposure_time_ms=self._default_exposure_ms,
                gain_db=self._default_gain_db,
                binning=self._default_binning
            )

        return ImageData.create_from_array(array, metadata, dimension_order)

    def create_image_stack(self, images: List[ImageData],
                          stack_type: str = "z-stack") -> ImageStack:
        """Create ImageStack from list of images.

        Args:
            images: List of ImageData objects
            stack_type: Type of stack

        Returns:
            ImageStack object
        """
        return ImageStack(
            images=images,
            stack_type=stack_type,
            name=f"{stack_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )

    # ==================== Statistics and Configuration ====================

    def get_service_statistics(self) -> Dict[str, Any]:
        """Get service usage statistics.

        Returns:
            Statistics dictionary
        """
        return self._stats.copy()

    def reset_statistics(self):
        """Reset service statistics."""
        self._stats = {
            'images_acquired': 0,
            'images_processed': 0,
            'total_acquisition_time': 0.0,
            'last_acquisition_time': None
        }

    def set_default_exposure(self, exposure_ms: float):
        """Set default exposure time.

        Args:
            exposure_ms: Exposure time in milliseconds
        """
        self._default_exposure_ms = exposure_ms

    def set_default_gain(self, gain_db: float):
        """Set default camera gain.

        Args:
            gain_db: Gain in decibels
        """
        self._default_gain_db = gain_db

    # ==================== Singleton Support ====================

    _instance = None

    @classmethod
    def get_instance(cls) -> 'ImageService':
        """Get singleton instance.

        Returns:
            Singleton ImageService instance
        """
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance