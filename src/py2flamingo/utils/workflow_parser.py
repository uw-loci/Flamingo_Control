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


def dict_to_workflow_text(workflow_dict: Dict[str, Any]) -> str:
    """Convert workflow dictionary to workflow file text format.

    Uses the C++ expected format with <Workflow Settings> wrapper,
    4-space indentation, and ` = ` separator.

    Args:
        workflow_dict: Workflow configuration dictionary

    Returns:
        Workflow file content as string

    Example:
        >>> workflow_dict = {'Experiment Settings': {'Sample': 'test'}, ...}
        >>> text = dict_to_workflow_text(workflow_dict)
        >>> print(text[:50])
        <Workflow Settings>
            <Experiment Settings>
    """
    lines = ["<Workflow Settings>"]

    # Experiment Settings section
    lines.append("    <Experiment Settings>")
    exp = workflow_dict.get('Experiment Settings', {})

    # Plane spacing (from stack settings or default)
    plane_spacing = exp.get('Plane spacing (um)', 1.0)
    lines.append(f"    Plane spacing (um) = {plane_spacing}")

    # Frame rate and exposure
    frame_rate = exp.get('Frame rate (f/s)', 100.0)
    exposure_time = exp.get('Exposure time (us)', 10000)
    lines.append(f"    Frame rate (f/s) = {frame_rate:.1f}")
    lines.append(f"    Exposure time (us) = {int(exposure_time)}")

    # Time-lapse settings
    duration = exp.get('Duration (dd:hh:mm:ss)', '00:00:00:01')
    interval = exp.get('Interval (dd:hh:mm:ss)', '00:00:00:01')
    lines.append(f"    Duration (dd:hh:mm:ss) = {duration}")
    lines.append(f"    Interval (dd:hh:mm:ss) = {interval}")

    # Sample name
    sample = exp.get('Sample', '')
    lines.append(f"    Sample = {sample}")

    # Multi-angle settings
    num_angles = exp.get('Number of angles', 1)
    angle_step = exp.get('Angle step size', 0)
    lines.append(f"    Number of angles = {num_angles}")
    lines.append(f"    Angle step size = {angle_step}")

    # Region
    region = exp.get('Region', '')
    lines.append(f"    Region = {region}")

    # Save settings
    save_drive = exp.get('Save image drive', '/media/deploy/ctlsm1')
    save_dir = exp.get('Save image directory', 'data')
    lines.append(f"    Save image drive = {save_drive}")
    lines.append(f"    Save image directory = {save_dir}")

    # Comments
    comments = exp.get('Comments', '')
    lines.append(f"    Comments = {comments}")

    # Display/Save options
    save_mip = exp.get('Save max projection', 'false')
    display_mip = exp.get('Display max projection', 'true')
    save_format = exp.get('Save image data', 'Tiff')
    save_subfolders = exp.get('Save to subfolders', 'false')
    live_view = exp.get('Work flow live view enabled', 'true')

    lines.append(f"    Save max projection = {save_mip}")
    lines.append(f"    Display max projection = {display_mip}")
    lines.append(f"    Save image data = {save_format}")
    lines.append(f"    Save to subfolders = {save_subfolders}")
    lines.append(f"    Work flow live view enabled = {live_view}")

    lines.append("    </Experiment Settings>")

    # Camera Settings section
    lines.append("")
    lines.append("    <Camera Settings>")
    cam = workflow_dict.get('Camera Settings', {})

    cam_exposure = cam.get('Exposure time (us)', 10000)
    cam_framerate = cam.get('Frame rate (f/s)', 100.0)
    aoi_width = cam.get('AOI width', 2048)
    aoi_height = cam.get('AOI height', 2048)

    lines.append(f"    Exposure time (us) = {int(cam_exposure)}")
    lines.append(f"    Frame rate (f/s) = {cam_framerate:.1f}")
    lines.append(f"    AOI width = {aoi_width}")
    lines.append(f"    AOI height = {aoi_height}")
    lines.append("    </Camera Settings>")

    # Stack Settings section
    lines.append("")
    lines.append("    <Stack Settings>")
    stack = workflow_dict.get('Stack Settings', {})

    lines.append("    Stack index = ")
    lines.append(f"    Change in Z axis (mm) = {stack.get('Change in Z axis (mm)', 0.001):.6f}")
    lines.append(f"    Number of planes = {stack.get('Number of planes', 1)}")
    lines.append(f"    Z stage velocity (mm/s) = {stack.get('Z stage velocity (mm/s)', 0.4)}")
    lines.append(f"    Rotational stage velocity (°/s) = {stack.get('Rotational stage velocity (°/s)', 0)}")
    lines.append(f"    Auto update stack calculations = {stack.get('Auto update stack calculations', 'true')}")
    lines.append(f"    Camera 1 capture percentage = {stack.get('Camera 1 capture percentage', 100)}")
    lines.append(f"    Camera 1 capture mode = {stack.get('Camera 1 capture mode', 0)}")
    lines.append(f"    Camera 2 capture percentage = {stack.get('Camera 2 capture percentage', 100)}")
    lines.append(f"    Camera 2 capture mode = {stack.get('Camera 2 capture mode', 0)}")
    lines.append(f"    Stack option = {stack.get('Stack option', 'None')}")
    lines.append(f"    Stack option settings 1 = {stack.get('Stack option settings 1', 0)}")
    lines.append(f"    Stack option settings 2 = {stack.get('Stack option settings 2', 0)}")
    lines.append("    </Stack Settings>")

    # Start Position section
    lines.append("")
    lines.append("    <Start Position>")
    start_pos = workflow_dict.get('Start Position', {})
    lines.append(f"    X (mm) = {start_pos.get('X (mm)', 0.0):.6f}")
    lines.append(f"    Y (mm) = {start_pos.get('Y (mm)', 0.0):.6f}")
    lines.append(f"    Z (mm) = {start_pos.get('Z (mm)', 10.0):.6f}")
    lines.append(f"    Angle (degrees) = {start_pos.get('Angle (degrees)', 0.0):.2f}")
    lines.append("    </Start Position>")

    # End Position section
    lines.append("")
    lines.append("    <End Position>")
    end_pos = workflow_dict.get('End Position', start_pos)
    lines.append(f"    X (mm) = {end_pos.get('X (mm)', 0.0):.6f}")
    lines.append(f"    Y (mm) = {end_pos.get('Y (mm)', 0.0):.6f}")
    lines.append(f"    Z (mm) = {end_pos.get('Z (mm)', 10.0):.6f}")
    lines.append(f"    Angle (degrees) = {end_pos.get('Angle (degrees)', 0.0):.2f}")
    lines.append("    </End Position>")

    # Illumination Source section
    lines.append("")
    lines.append("    <Illumination Source>")
    illum = workflow_dict.get('Illumination Source', {})

    # Write all illumination settings (except path settings)
    for key, value in illum.items():
        if key in ('Left path', 'Right path'):
            continue
        lines.append(f"    {key} = {value}")

    lines.append("    </Illumination Source>")

    # Illumination Path section
    lines.append("")
    lines.append("    <Illumination Path>")
    left_path = illum.get('Left path', 'ON 1')
    right_path = illum.get('Right path', 'OFF 0')
    lines.append(f"    Left path = {left_path}")
    lines.append(f"    Right path = {right_path}")
    lines.append("    </Illumination Path>")

    # Illumination Options section
    lines.append("")
    lines.append("    <Illumination Options>")
    illum_opts = workflow_dict.get('Illumination Options', {})
    multi_laser = illum_opts.get('Run stack with multiple lasers on', 'false')
    lines.append(f"    Run stack with multiple lasers on = {multi_laser}")
    lines.append("    </Illumination Options>")

    lines.append("</Workflow Settings>")

    return "\n".join(lines)


class WorkflowTextFormatter:
    """Utility class for converting workflow dictionaries to text format.

    This is the standard way to convert workflow dicts (from UI or code)
    to the text format expected by the microscope.

    Example:
        >>> formatter = WorkflowTextFormatter()
        >>> text = formatter.format(workflow_dict)
        >>> workflow_bytes = text.encode('utf-8')
    """

    def format(self, workflow_dict: Dict[str, Any]) -> str:
        """Convert workflow dictionary to text format.

        Args:
            workflow_dict: Workflow configuration dictionary

        Returns:
            Workflow file content as string
        """
        return dict_to_workflow_text(workflow_dict)

    def format_to_bytes(self, workflow_dict: Dict[str, Any]) -> bytes:
        """Convert workflow dictionary to UTF-8 bytes.

        Args:
            workflow_dict: Workflow configuration dictionary

        Returns:
            Workflow file content as UTF-8 encoded bytes
        """
        return dict_to_workflow_text(workflow_dict).encode('utf-8')
