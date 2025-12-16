"""
Workflow file parser for Flamingo microscope workflows.

This module provides utilities to parse, validate, and preview workflow .txt files.
It leverages existing file_handlers utilities and adds additional validation and
error handling for robust workflow processing.
"""
from pathlib import Path
from typing import Dict, Any, Tuple, List, Union

from .file_handlers import workflow_to_dict


def parse_workflow_file(path: Union[str, Path]) -> Dict[str, Any]:
    """Parse a workflow .txt file into a structured dictionary.

    Reads a workflow file and parses it into a nested dictionary structure
    representing the workflow settings, sections, and parameters.

    Args:
        path: Path to workflow .txt file

    Returns:
        Dictionary containing parsed workflow data

    Raises:
        FileNotFoundError: If the workflow file doesn't exist
        ValueError: If the file cannot be parsed

    Example:
        >>> workflow = parse_workflow_file("workflows/Snapshot.txt")
        >>> print(workflow.keys())
        dict_keys(['Experiment Settings', 'Camera Settings', ...])
    """
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Workflow file not found: {path}")

    if not path.is_file():
        raise ValueError(f"Path is not a file: {path}")

    # Use existing workflow_to_dict with enhanced error handling
    try:
        # Read with error handling for encoding issues
        workflow_dict = workflow_to_dict(path)
    except UnicodeDecodeError as e:
        raise ValueError(f"Encoding error in workflow file: {e}")
    except Exception as e:
        raise ValueError(f"Failed to parse workflow file: {e}")

    if not workflow_dict:
        raise ValueError(f"Workflow file is empty or has no parseable content: {path}")

    return workflow_dict


def validate_workflow(workflow_dict: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validate a workflow dictionary structure.

    Checks if the workflow has expected structure and common required fields.

    Args:
        workflow_dict: Dictionary from parse_workflow_file

    Returns:
        Tuple of (is_valid, error_list)
        - is_valid: True if workflow is valid, False otherwise
        - error_list: List of validation error messages (empty if valid)

    Example:
        >>> workflow = parse_workflow_file("workflows/Snapshot.txt")
        >>> valid, errors = validate_workflow(workflow)
        >>> if not valid:
        ...     print(f"Validation errors: {errors}")
    """
    errors = []

    # Check if workflow is empty
    if not workflow_dict:
        errors.append("Workflow is empty")
        return False, errors

    # Check for common expected sections
    expected_sections = [
        "Experiment Settings",
        "Stack Settings",
        "Start Position",
        "Illumination Source"
    ]

    missing_sections = []
    for section in expected_sections:
        if section not in workflow_dict:
            missing_sections.append(section)

    if missing_sections:
        errors.append(f"Missing expected sections: {', '.join(missing_sections)}")

    # Validate Experiment Settings if present
    if "Experiment Settings" in workflow_dict:
        exp_settings = workflow_dict["Experiment Settings"]
        if isinstance(exp_settings, dict):
            # Check for some critical fields
            critical_fields = ["Frame rate (f/s)", "Exposure time (us)"]
            for field in critical_fields:
                if field not in exp_settings:
                    errors.append(f"Missing critical field in Experiment Settings: {field}")

    # Validate Start Position if present
    if "Start Position" in workflow_dict:
        start_pos = workflow_dict["Start Position"]
        if isinstance(start_pos, dict):
            # Check for position coordinates
            position_fields = ["X (mm)", "Y (mm)", "Z (mm)"]
            for field in position_fields:
                if field not in start_pos:
                    errors.append(f"Missing position field in Start Position: {field}")

    # Return validation result
    return len(errors) == 0, errors


def get_workflow_preview(path: Union[str, Path], max_lines: int = 20) -> str:
    """Get a preview of the workflow file for UI display.

    Reads the first max_lines of the workflow file and returns them as a
    formatted string. Adds a truncation indicator if the file is longer.

    Args:
        path: Path to workflow file
        max_lines: Maximum number of lines to include in preview (default: 20)

    Returns:
        Preview text as a string

    Raises:
        FileNotFoundError: If the file doesn't exist
        ValueError: If the file cannot be read

    Example:
        >>> preview = get_workflow_preview("workflows/Snapshot.txt", 10)
        >>> print(preview)
        <Workflow Settings>
            <Experiment Settings>
            ...
    """
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Workflow file not found: {path}")

    if not path.is_file():
        raise ValueError(f"Path is not a file: {path}")

    try:
        # Read file with encoding error handling
        lines = []
        total_lines = 0
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            for i, line in enumerate(f):
                total_lines = i + 1
                if i < max_lines:
                    # Keep the line with trailing newline removed
                    lines.append(line.rstrip('\n\r'))
                else:
                    # Just count remaining lines
                    pass

        preview = '\n'.join(lines)

        # Add truncation indicator if file is longer
        if total_lines > max_lines:
            preview += f"\n\n... ({total_lines - max_lines} more lines)"

        return preview

    except Exception as e:
        raise ValueError(f"Failed to read workflow file: {e}")


def read_workflow_as_bytes(path: Union[str, Path]) -> bytes:
    """Read entire workflow file as UTF-8 bytes for transmission to microscope.

    Reads the workflow file and returns it as bytes, suitable for sending
    over the network to the microscope. Validates file size to prevent
    sending excessively large files.

    Args:
        path: Path to workflow file

    Returns:
        Workflow file contents as UTF-8 encoded bytes

    Raises:
        FileNotFoundError: If the file doesn't exist
        ValueError: If the file is too large or cannot be read

    Example:
        >>> data = read_workflow_as_bytes("workflows/Snapshot.txt")
        >>> print(f"Workflow size: {len(data)} bytes")
        Workflow size: 1523 bytes
    """
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Workflow file not found: {path}")

    if not path.is_file():
        raise ValueError(f"Path is not a file: {path}")

    # Check file size (warn if > 10MB, which is unusually large for a workflow)
    file_size = path.stat().st_size
    max_size = 10 * 1024 * 1024  # 10 MB

    if file_size > max_size:
        raise ValueError(
            f"Workflow file is too large ({file_size} bytes). "
            f"Maximum supported size is {max_size} bytes (10 MB)."
        )

    try:
        # Read entire file as bytes
        with open(path, 'rb') as f:
            workflow_bytes = f.read()

        # Verify it's valid UTF-8 (workflows should be text files)
        try:
            workflow_bytes.decode('utf-8')
        except UnicodeDecodeError:
            raise ValueError(
                "Workflow file contains invalid UTF-8 encoding. "
                "Workflows must be valid text files."
            )

        return workflow_bytes

    except Exception as e:
        if isinstance(e, (FileNotFoundError, ValueError)):
            raise
        raise ValueError(f"Failed to read workflow file: {e}")


def get_workflow_summary(workflow_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Extract a summary of key workflow parameters.

    Extracts commonly used workflow parameters into a flat dictionary
    for easy access and display.

    Args:
        workflow_dict: Parsed workflow dictionary

    Returns:
        Dictionary containing summary information

    Example:
        >>> workflow = parse_workflow_file("workflows/Snapshot.txt")
        >>> summary = get_workflow_summary(workflow)
        >>> print(summary.get('frame_rate'))
        40.003200
    """
    summary = {}

    # Extract experiment settings
    if "Experiment Settings" in workflow_dict:
        exp = workflow_dict["Experiment Settings"]
        if isinstance(exp, dict):
            summary['frame_rate'] = exp.get('Frame rate (f/s)', 'N/A')
            summary['exposure_time'] = exp.get('Exposure time (us)', 'N/A')
            summary['duration'] = exp.get('Duration (dd:hh:mm:ss)', 'N/A')
            summary['sample'] = exp.get('Sample', 'N/A')
            summary['save_directory'] = exp.get('Save image directory', 'N/A')

    # Extract stack settings
    if "Stack Settings" in workflow_dict:
        stack = workflow_dict["Stack Settings"]
        if isinstance(stack, dict):
            summary['num_planes'] = stack.get('Number of planes', 'N/A')
            summary['plane_spacing'] = stack.get('Change in Z axis (mm)', 'N/A')
            summary['stack_option'] = stack.get('Stack option', 'N/A')

    # Extract start position
    if "Start Position" in workflow_dict:
        start = workflow_dict["Start Position"]
        if isinstance(start, dict):
            summary['start_x'] = start.get('X (mm)', 'N/A')
            summary['start_y'] = start.get('Y (mm)', 'N/A')
            summary['start_z'] = start.get('Z (mm)', 'N/A')
            summary['start_angle'] = start.get('Angle (degrees)', 'N/A')

    return summary


class WorkflowParser:
    """Object-oriented wrapper for workflow parsing utilities.

    Provides a class-based interface to the workflow parsing functions
    for use cases that prefer object-oriented patterns.
    """

    def parse_file(self, path: Union[str, Path]) -> Dict[str, Any]:
        """Parse a workflow file.

        Args:
            path: Path to workflow file

        Returns:
            Parsed workflow dictionary
        """
        return parse_workflow_file(path)

    def validate(self, workflow_dict: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Validate a workflow dictionary.

        Args:
            workflow_dict: Workflow dictionary to validate

        Returns:
            Tuple of (is_valid, error_list)
        """
        return validate_workflow(workflow_dict)

    def get_preview(self, path: Union[str, Path], max_lines: int = 20) -> str:
        """Get workflow file preview.

        Args:
            path: Path to workflow file
            max_lines: Maximum lines to include

        Returns:
            Preview text
        """
        return get_workflow_preview(path, max_lines)

    def read_as_bytes(self, path: Union[str, Path]) -> bytes:
        """Read workflow file as bytes.

        Args:
            path: Path to workflow file

        Returns:
            Workflow file contents as bytes
        """
        return read_workflow_as_bytes(path)

    def get_summary(self, workflow_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Get workflow summary.

        Args:
            workflow_dict: Parsed workflow dictionary

        Returns:
            Summary dictionary
        """
        return get_workflow_summary(workflow_dict)
