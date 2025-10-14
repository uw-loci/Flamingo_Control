"""
Metadata file parser for Flamingo microscope settings.

This module provides utilities to parse FlamingoMetaData.txt files and extract
connection configuration. It leverages existing file_handlers utilities and
returns typed ConnectionConfig objects.
"""
from pathlib import Path
from typing import Tuple, List, Union
import re

from ..models.connection import ConnectionConfig
from .file_handlers import text_to_dict


def parse_metadata_file(path: Union[str, Path]) -> ConnectionConfig:
    """Parse FlamingoMetaData.txt into ConnectionConfig.

    Reads a metadata file, extracts the microscope IP address and port,
    and returns a validated ConnectionConfig object.

    Args:
        path: Path to FlamingoMetaData.txt file

    Returns:
        ConnectionConfig: Configuration object with IP, port, and live port

    Raises:
        FileNotFoundError: If the metadata file doesn't exist
        ValueError: If the file format is invalid or connection info not found

    Example:
        >>> config = parse_metadata_file("microscope_settings/FlamingoMetaData_test.txt")
        >>> print(f"{config.ip_address}:{config.port}")
        127.0.0.1:53717
    """
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Metadata file not found: {path}")

    if not path.is_file():
        raise ValueError(f"Path is not a file: {path}")

    # Use existing file_handlers to parse the nested structure
    try:
        data = text_to_dict(path)
    except Exception as e:
        raise ValueError(f"Failed to parse metadata file: {e}")

    # Search for "Microscope address" in the nested structure
    address_line = _find_microscope_address(data)

    if not address_line:
        raise ValueError(
            "Could not find 'Microscope address' in metadata file. "
            "Expected format: 'Microscope address = <ip> <port>'"
        )

    # Extract IP and port from the address line
    ip, port = extract_connection_info(address_line)

    # Create and validate ConnectionConfig
    config = ConnectionConfig(
        ip_address=ip,
        port=port,
        live_port=port + 1  # Live port is always command port + 1
    )

    # Validate the configuration
    valid, errors = config.validate()
    if not valid:
        raise ValueError(f"Invalid connection configuration: {', '.join(errors)}")

    return config


def _find_microscope_address(data: dict) -> str:
    """Recursively search for 'Microscope address' key in nested dict.

    Args:
        data: Nested dictionary from text_to_dict

    Returns:
        Value of 'Microscope address' key, or empty string if not found
    """
    for key, value in data.items():
        if key == "Microscope address":
            return str(value)
        elif isinstance(value, dict):
            result = _find_microscope_address(value)
            if result:
                return result
    return ""


def extract_connection_info(line: str) -> Tuple[str, int]:
    """Extract IP address and port from a connection line.

    Parses a line like "192.168.1.1 53717" or "127.0.0.1 53717"
    and extracts the IP and port.

    Args:
        line: Line containing IP and port separated by space

    Returns:
        Tuple of (ip_address, port)

    Raises:
        ValueError: If the line format is invalid

    Example:
        >>> ip, port = extract_connection_info("192.168.1.1 53717")
        >>> print(ip, port)
        192.168.1.1 53717
    """
    if not line or not line.strip():
        raise ValueError("Connection info line is empty")

    # Split by whitespace and take first two parts
    parts = line.strip().split()

    if len(parts) < 2:
        raise ValueError(
            f"Invalid connection info format: '{line}'. "
            "Expected: '<ip_address> <port>'"
        )

    ip_address = parts[0]
    port_str = parts[1]

    # Validate IP format (basic IPv4 regex)
    ip_pattern = re.compile(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$')
    if not ip_pattern.match(ip_address):
        raise ValueError(f"Invalid IP address format: '{ip_address}'")

    # Validate port is a number
    try:
        port = int(port_str)
    except ValueError:
        raise ValueError(f"Invalid port number: '{port_str}'. Port must be an integer.")

    # Validate port range
    if port < 1 or port > 65535:
        raise ValueError(f"Port {port} out of valid range (1-65535)")

    return ip_address, port


def validate_metadata_file(path: Union[str, Path]) -> Tuple[bool, List[str]]:
    """Validate a metadata file without creating a ConnectionConfig.

    Checks if the file exists, is parseable, and contains valid connection info.

    Args:
        path: Path to metadata file

    Returns:
        Tuple of (is_valid, error_list)
        - is_valid: True if file is valid, False otherwise
        - error_list: List of error messages (empty if valid)

    Example:
        >>> valid, errors = validate_metadata_file("metadata.txt")
        >>> if not valid:
        ...     print(f"Errors: {errors}")
    """
    path = Path(path)
    errors = []

    # Check file exists
    if not path.exists():
        errors.append(f"File not found: {path}")
        return False, errors

    if not path.is_file():
        errors.append(f"Path is not a file: {path}")
        return False, errors

    # Try to parse
    try:
        data = text_to_dict(path)
    except Exception as e:
        errors.append(f"Failed to parse file: {e}")
        return False, errors

    # Check for microscope address
    address_line = _find_microscope_address(data)
    if not address_line:
        errors.append("Missing 'Microscope address' field")
        return False, errors

    # Try to extract connection info
    try:
        ip, port = extract_connection_info(address_line)
    except ValueError as e:
        errors.append(str(e))
        return False, errors

    # Validate the configuration would be valid
    config = ConnectionConfig(ip_address=ip, port=port, live_port=port + 1)
    valid, config_errors = config.validate()
    if not valid:
        errors.extend(config_errors)
        return False, errors

    return True, []
