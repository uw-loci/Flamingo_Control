"""
ExternalCommandRunner — executes a subprocess with temp file I/O.

Config:
    command_template: str — command with {input_file} and {output_dir} placeholders
    input_format: str — 'tiff' or 'numpy' (how to serialize input data)
    output_format: str — 'csv', 'json', or 'numpy' (how to parse output)
    timeout_seconds: int — max execution time (default 300)

Inputs:
    input_data — ANY (data to serialize and pass to the command)
    trigger — execution ordering

Outputs:
    output_data — ANY (parsed output from the command)
    file_path — FILE_PATH (path to the output directory)
    completed — TRIGGER
"""

import json
import logging
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from py2flamingo.pipeline.models.port_types import PortType
from py2flamingo.pipeline.models.pipeline import Pipeline, PipelineNode
from py2flamingo.pipeline.engine.context import ExecutionContext
from py2flamingo.pipeline.engine.node_runners.base_runner import AbstractNodeRunner

logger = logging.getLogger(__name__)


class ExternalCommandRunner(AbstractNodeRunner):
    """Runs an external command with serialized input and parsed output."""

    def run(self, node: PipelineNode, pipeline: Pipeline,
            context: ExecutionContext) -> None:
        config = node.config

        command_template = config.get('command_template', '')
        if not command_template:
            raise RuntimeError(f"External command '{node.name}': no command_template")

        input_format = config.get('input_format', 'numpy')
        output_format = config.get('output_format', 'json')
        timeout = config.get('timeout_seconds', 300)

        # Get input data
        input_data = self._get_input(node, pipeline, context, 'input_data')

        # Create temp directory for I/O
        with tempfile.TemporaryDirectory(prefix='pipeline_ext_') as tmpdir:
            tmpdir_path = Path(tmpdir)
            input_file = tmpdir_path / 'input'
            output_dir = tmpdir_path / 'output'
            output_dir.mkdir()

            # Serialize input
            if input_data is not None:
                self._serialize_input(input_data, input_file, input_format)
            else:
                input_file = Path('/dev/null')

            # Build command
            cmd = command_template.format(
                input_file=str(input_file),
                output_dir=str(output_dir),
            )

            logger.info(f"External command '{node.name}': running: {cmd}")

            # Execute
            try:
                result = subprocess.run(
                    cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                raise RuntimeError(
                    f"External command timed out after {timeout}s: {cmd}"
                )

            if result.returncode != 0:
                stderr = result.stderr.strip()
                raise RuntimeError(
                    f"External command failed (exit {result.returncode}): {stderr}"
                )

            if result.stdout.strip():
                logger.info(f"Command stdout: {result.stdout.strip()[:500]}")

            # Parse output
            output_data = self._parse_output(output_dir, output_format)

            # Set outputs
            self._set_output(node, context, 'output_data', PortType.ANY, output_data)
            self._set_output(node, context, 'file_path', PortType.FILE_PATH, str(output_dir))
            self._set_output(node, context, 'completed', PortType.TRIGGER, True)

        logger.info(f"External command '{node.name}': completed")

    def _serialize_input(self, data, path: Path, fmt: str) -> None:
        """Write input data to a file."""
        if fmt == 'tiff':
            try:
                import tifffile
                if isinstance(data, np.ndarray):
                    tifffile.imwrite(str(path.with_suffix('.tiff')), data)
                else:
                    raise ValueError("TIFF format requires numpy array input")
            except ImportError:
                # Fall back to numpy format
                logger.warning("tifffile not available, using numpy format")
                np.save(str(path.with_suffix('.npy')), np.asarray(data))
        elif fmt == 'numpy':
            np.save(str(path.with_suffix('.npy')), np.asarray(data))
        elif fmt == 'json':
            with open(path.with_suffix('.json'), 'w') as f:
                json.dump(data if not isinstance(data, np.ndarray) else data.tolist(), f)
        else:
            # Raw bytes
            with open(path, 'wb') as f:
                if isinstance(data, bytes):
                    f.write(data)
                else:
                    f.write(str(data).encode())

    def _parse_output(self, output_dir: Path, fmt: str):
        """Read output from the output directory."""
        files = sorted(output_dir.iterdir())
        if not files:
            logger.warning("External command produced no output files")
            return None

        output_file = files[0]  # Take the first output file

        if fmt == 'json':
            with open(output_file) as f:
                return json.load(f)
        elif fmt == 'csv':
            # Return as list of dicts
            import csv
            with open(output_file) as f:
                reader = csv.DictReader(f)
                return list(reader)
        elif fmt == 'numpy':
            return np.load(str(output_file), allow_pickle=False)
        else:
            with open(output_file) as f:
                return f.read()
