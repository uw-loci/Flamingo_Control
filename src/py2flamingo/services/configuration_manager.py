"""
Configuration management service for Flamingo microscope settings.

This module provides utilities to save, load, and manage microscope
configurations using JSON-based persistent storage.
"""
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, asdict
import logging
import json

from ..models.connection import ConnectionConfig


logger = logging.getLogger(__name__)


@dataclass
class MicroscopeConfiguration:
    """Complete microscope configuration with metadata."""

    name: str  # Microscope display name
    ip_address: str  # IP address
    port: int  # Port number
    description: str = ""  # Optional description

    def __str__(self) -> str:
        """String representation for display."""
        return f"{self.name} ({self.ip_address}:{self.port})"

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            'name': self.name,
            'ip_address': self.ip_address,
            'port': self.port,
            'description': self.description
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'MicroscopeConfiguration':
        """Create from dictionary loaded from JSON."""
        return cls(
            name=data['name'],
            ip_address=data['ip_address'],
            port=data['port'],
            description=data.get('description', '')
        )

    def to_connection_config(self) -> ConnectionConfig:
        """Convert to ConnectionConfig for connection service."""
        return ConnectionConfig(
            ip_address=self.ip_address,
            port=self.port,
            live_port=self.port + 1
        )


class ConfigurationManager:
    """Manages microscope configurations using JSON-based storage."""

    def __init__(self, config_file: str = "saved_configurations.json"):
        """Initialize configuration manager.

        Args:
            config_file: Path to JSON file storing configurations
        """
        self.config_file = Path(config_file)
        self._configurations: Dict[str, MicroscopeConfiguration] = {}

        # Load existing configurations on init
        self._load_from_json()

    def _load_from_json(self) -> None:
        """Load configurations from JSON file.

        If file doesn't exist, start with empty configuration set.
        """
        if not self.config_file.exists():
            logger.info(f"Configuration file not found: {self.config_file}. Starting with empty configuration set.")
            self._configurations = {}
            return

        try:
            with open(self.config_file, 'r') as f:
                data = json.load(f)

            # Load each configuration
            for config_data in data.get('configurations', []):
                config = MicroscopeConfiguration.from_dict(config_data)
                self._configurations[config.name] = config

            logger.info(f"Loaded {len(self._configurations)} configurations from {self.config_file}")

        except Exception as e:
            logger.error(f"Error loading configurations from JSON: {e}")
            self._configurations = {}

    def _save_to_json(self) -> None:
        """Save current configurations to JSON file."""
        try:
            # Convert configurations to list of dicts
            config_list = [config.to_dict() for config in self._configurations.values()]

            data = {
                'configurations': config_list,
                'version': '1.0'
            }

            # Ensure parent directory exists
            self.config_file.parent.mkdir(parents=True, exist_ok=True)

            # Write to file with nice formatting
            with open(self.config_file, 'w') as f:
                json.dump(data, f, indent=2)

            logger.info(f"Saved {len(self._configurations)} configurations to {self.config_file}")

        except Exception as e:
            logger.error(f"Error saving configurations to JSON: {e}")
            raise

    def discover_configurations(self) -> List[MicroscopeConfiguration]:
        """Get all saved configurations.

        Returns:
            List of MicroscopeConfiguration objects

        Example:
            >>> manager = ConfigurationManager()
            >>> configs = manager.discover_configurations()
            >>> for config in configs:
            ...     print(config.name, config.ip_address)
        """
        return list(self._configurations.values())

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
            List of configuration names sorted alphabetically
        """
        return sorted(self._configurations.keys())

    def get_default_configuration(self) -> Optional[MicroscopeConfiguration]:
        """Get the default configuration.

        Returns the first available configuration alphabetically,
        or None if no configurations exist.

        Returns:
            Default MicroscopeConfiguration if any configs exist, None otherwise
        """
        names = self.get_configuration_names()
        if names:
            return self._configurations[names[0]]
        return None

    def refresh(self) -> List[MicroscopeConfiguration]:
        """Refresh the list of available configurations.

        Reloads from JSON file to pick up any external changes.

        Returns:
            Updated list of configurations
        """
        self._load_from_json()
        return list(self._configurations.values())

    def save_configuration(self, name: str, ip: str, port: int, description: str = "") -> Tuple[bool, str]:
        """Save a new configuration to JSON storage.

        Creates or updates a configuration with the specified connection parameters.
        The configuration will be immediately available in the dropdown list.

        Args:
            name: Display name for the microscope configuration
            ip: IP address (e.g., "192.168.1.1")
            port: Port number (e.g., 53717)
            description: Optional description

        Returns:
            Tuple of (success: bool, message: str)

        Example:
            >>> manager = ConfigurationManager()
            >>> success, msg = manager.save_configuration("N7-10GB", "192.168.1.1", 53717)
            >>> if success:
            ...     print(f"Configuration saved: {msg}")
        """
        try:
            # Validate connection parameters
            config = ConnectionConfig(
                ip_address=ip,
                port=port,
                live_port=port + 1
            )
            valid, errors = config.validate()
            if not valid:
                error_msg = ", ".join(errors)
                logger.warning(f"Invalid configuration parameters: {error_msg}")
                return False, f"Invalid parameters: {error_msg}"

            # Check if configuration already exists
            if name in self._configurations:
                logger.warning(f"Configuration '{name}' already exists")
                return False, f"Configuration '{name}' already exists. Please use a different name or delete the existing one first."

            # Create new configuration
            new_config = MicroscopeConfiguration(
                name=name,
                ip_address=ip,
                port=port,
                description=description
            )

            # Add to configurations
            self._configurations[name] = new_config

            # Save to JSON file
            self._save_to_json()

            logger.info(f"Saved configuration '{name}' ({ip}:{port})")
            return True, f"Configuration '{name}' saved successfully"

        except Exception as e:
            logger.error(f"Error saving configuration: {e}")
            return False, f"Error: {str(e)}"

    def delete_configuration(self, name: str) -> Tuple[bool, str]:
        """Delete a configuration from storage.

        Args:
            name: Name of configuration to delete

        Returns:
            Tuple of (success: bool, message: str)

        Example:
            >>> manager = ConfigurationManager()
            >>> success, msg = manager.delete_configuration("Old Config")
            >>> if success:
            ...     print(f"Configuration deleted: {msg}")
        """
        try:
            if name not in self._configurations:
                logger.warning(f"Configuration '{name}' not found")
                return False, f"Configuration '{name}' not found"

            # Remove from configurations
            del self._configurations[name]

            # Save updated list to JSON
            self._save_to_json()

            logger.info(f"Deleted configuration '{name}'")
            return True, f"Configuration '{name}' deleted successfully"

        except Exception as e:
            logger.error(f"Error deleting configuration: {e}")
            return False, f"Error: {str(e)}"
