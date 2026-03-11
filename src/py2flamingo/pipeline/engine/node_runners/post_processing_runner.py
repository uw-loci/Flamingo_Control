"""Runner for the POST_PROCESSING pipeline node.

Invokes the stitching pipeline on a raw acquisition directory.
Exposes key parameters through the node's config dict.
"""

import logging
from pathlib import Path

from py2flamingo.pipeline.engine.context import ExecutionContext
from py2flamingo.pipeline.engine.node_runners.base_runner import AbstractNodeRunner
from py2flamingo.pipeline.models.pipeline import Pipeline, PipelineNode
from py2flamingo.pipeline.models.port_types import PortType

logger = logging.getLogger(__name__)


class PostProcessingRunner(AbstractNodeRunner):
    """Execute the stitching/post-processing pipeline as a pipeline node."""

    def run(
        self,
        node: PipelineNode,
        pipeline: Pipeline,
        context: ExecutionContext,
    ) -> None:
        """Run the stitching pipeline on the configured acquisition directory."""
        from py2flamingo.stitching.pipeline import StitchingConfig, StitchingPipeline

        # Get acquisition directory from input port or config
        acq_dir = self._get_input(node, pipeline, context, "acquisition_dir")
        if not acq_dir:
            acq_dir = node.config.get("acquisition_dir", "")
        if not acq_dir:
            raise ValueError("No acquisition directory specified")

        acq_path = Path(acq_dir)
        if not acq_path.is_dir():
            raise FileNotFoundError(f"Acquisition directory not found: {acq_path}")

        # Build output path
        output_dir = node.config.get("output_dir", "")
        if not output_dir:
            output_dir = str(acq_path / "stitched")
        output_path = Path(output_dir)

        # Build config from node properties
        cfg = node.config
        config = StitchingConfig(
            pixel_size_um=float(cfg.get("pixel_size_um", 0.406)),
            z_step_um=float(cfg.get("z_step_um", 0)) or None,
            illumination_fusion=cfg.get("illumination_fusion", "max"),
            destripe=bool(cfg.get("destripe", False)),
            deconvolution_enabled=bool(cfg.get("deconvolution_enabled", False)),
            deconvolution_engine=cfg.get("deconvolution_engine", "pycudadecon"),
            output_format=cfg.get("output_format", "ome-zarr-sharded"),
            package_ozx=bool(cfg.get("package_ozx", False)),
        )

        # Parse channels
        ch_str = cfg.get("channels", "")
        channels = None
        if ch_str:
            try:
                channels = [int(c.strip()) for c in str(ch_str).split(",") if c.strip()]
            except ValueError:
                channels = None

        logger.info(
            f"PostProcessingRunner: {acq_path} → {output_path} "
            f"(format={config.output_format})"
        )

        # Run pipeline with cancellation support
        sp = StitchingPipeline(
            config=config,
            cancelled_fn=(
                context.check_cancelled
                if hasattr(context, "check_cancelled")
                else lambda: False
            ),
        )
        result = sp.run(acq_path, output_path, channels=channels)

        # Set outputs
        self._set_output(node, context, "output_path", PortType.FILE_PATH, str(result))
        self._set_output(node, context, "completed", PortType.TRIGGER, True)

        logger.info(f"PostProcessingRunner complete: {result}")
