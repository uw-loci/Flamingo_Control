"""
Configuration management service for Flamingo microscope settings.

This module provides utilities to discover, load, and manage microscope
configuration files from the microscope_settings directory.
"""
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
import logging

from ..models.connection import ConnectionConfig
from ..utils.metadata_parser import parse_metadata_file, validate_metadata_file
from ..utils.file_handlers import text_to_dict


logger = logging.getLogger(__name__)


@dataclass
class MicroscopeConfiguration:
    """Complete microscope configuration with metadata."""

    name: str  # Microscope name from file
    file_path: Path  # Path to configuration file
    connection_config: ConnectionConfig  # Parsed connection settings
    description: str = ""  # Optional description

    def __str__(self) -> str:
        """String representation for display."""
        return f"{self.name} ({self.connection_config.ip_address}:{self.connection_config.port})"


class ConfigurationManager:
    """Manages discovery and loading of microscope configuration files."""

    def __init__(self, settings_directory: str = "microscope_settings"):
        """Initialize configuration manager.

        Args:
            settings_directory: Directory containing configuration files
        """
        self.settings_directory = Path(settings_directory)
        self._configurations: Dict[str, MicroscopeConfiguration] = {}

    def discover_configurations(self) -> List[MicroscopeConfiguration]:
        """Scan settings directory and discover valid configuration files.

        Searches for all .txt files in the settings directory, validates them,
        and extracts configuration information.

        Returns:
            List of valid MicroscopeConfiguration objects

        Example:
            >>> manager = ConfigurationManager()
            >>> configs = manager.discover_configurations()
            >>> for config in configs:
            ...     print(config.name, config.connection_config.ip_address)
        """
        self._configurations = {}

        if not self.settings_directory.exists():
            logger.warning(f"Settings directory not found: {self.settings_directory}")
            return []

        # Find all .txt files
        txt_files = list(self.settings_directory.glob("*.txt"))

        logger.info(f"Found {len(txt_files)} .txt files in {self.settings_directory}")

        for file_path in txt_files:
            try:
                # Validate file contains valid connection info
                valid, errors = validate_metadata_file(file_path)

                if not valid:
                    logger.debug(f"Skipping {file_path.name}: {errors}")
                    continue

                # Parse configuration
                connection_config = parse_metadata_file(file_path)

                # Extract microscope name from file
                microscope_name = self._extract_microscope_name(file_path)

                # Create configuration object
                config = MicroscopeConfiguration(
                    name=microscope_name,
                    file_path=file_path,
                    connection_config=connection_config,
                    description=f"Config from {file_path.name}"
                )

                # Store by name (use filename as fallback for duplicates)
                key = microscope_name or file_path.stem
                if key in self._configurations:
                    key = f"{key} ({file_path.name})"

                self._configurations[key] = config
                logger.info(f"Loaded configuration: {key}")

            except Exception as e:
                logger.debug(f"Could not parse {file_path.name}: {e}")
                continue

        configs = list(self._configurations.values())
        logger.info(f"Discovered {len(configs)} valid configurations")
        return configs

    def _extract_microscope_name(self, file_path: Path) -> str:
        """Extract microscope name from configuration file.

        Args:
            file_path: Path to configuration file

        Returns:
            Microscope name, or filename if not found
        """
        try:
            data = text_to_dict(file_path)

            # Search for "Microscope name" field
            name = self._find_in_dict(data, "Microscope name")
            if name:
                return str(name).strip()

            # Fallback to filename without extension
            return file_path.stem

        except Exception as e:
            logger.debug(f"Could not extract name from {file_path}: {e}")
            return file_path.stem

    def _find_in_dict(self, data: dict, key: str) -> Optional[str]:
        """Recursively search for a key in nested dict.

        Args:
            data: Nested dictionary
            key: Key to search for

        Returns:
            Value if found, None otherwise
        """
        for k, v in data.items():
            if k == key:
                return str(v)
            elif isinstance(v, dict):
                result = self._find_in_dict(v, key)
                if result:
                    return result
        return None

    def get_configuration(self, name: str) -> Optional[MicroscopeConfiguration]:
        """Get configuration by name.

        Args:
            name: Configuration name

        Returns:
            MicroscopeConfiguration if found, None otherwise
        """
        return self._configurations.get(name)

    def get_configuration_names(self) -> List[str]:
        """Get list of all configuration names.

        Returns:
            List of configuration names
        """
        return list(self._configurations.keys())

    def load_configuration_from_file(self, file_path: str) -> MicroscopeConfiguration:
        """Load a specific configuration file.

        Args:
            file_path: Path to configuration file

        Returns:
            MicroscopeConfiguration object

        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If file is invalid
        """
        path = Path(file_path)

        if not path.exists():
            raise FileNotFoundError(f"Configuration file not found: {path}")

        # Validate
        valid, errors = validate_metadata_file(path)
        if not valid:
            raise ValueError(f"Invalid configuration file: {', '.join(errors)}")

        # Parse
        connection_config = parse_metadata_file(path)
        microscope_name = self._extract_microscope_name(path)

        return MicroscopeConfiguration(
            name=microscope_name,
            file_path=path,
            connection_config=connection_config,
            description=f"Config from {path.name}"
        )

    def get_default_configuration(self) -> Optional[MicroscopeConfiguration]:
        """Get the default configuration.

        Looks for:
        1. FlamingoMetaData.txt (standard name)
        2. First available configuration

        Returns:
            Default MicroscopeConfiguration if any configs exist, None otherwise
        """
        if not self._configurations:
            self.discover_configurations()

        # Try standard name first
        for name, config in self._configurations.items():
            if "FlamingoMetaData.txt" in str(config.file_path):
                return config

        # Return first available
        if self._configurations:
            return next(iter(self._configurations.values()))

        return None

    def refresh(self) -> List[MicroscopeConfiguration]:
        """Refresh the list of available configurations.

        Re-scans the settings directory.

        Returns:
            Updated list of configurations
        """
        return self.discover_configurations()
