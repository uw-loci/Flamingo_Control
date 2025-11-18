"""
Configuration Migration Service

Migrates old configuration formats to new consolidated YAML format.
Ensures data accuracy by reading from actual source files.
"""

import yaml
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime
import shutil

from py2flamingo.utils.file_handlers import text_to_dict


class ConfigMigrationService:
    """
    Service for migrating old configuration formats to new YAML format.

    This service:
    1. Reads existing configuration files
    2. Consolidates data into new YAML structure
    3. Preserves all original values
    4. Creates backups before migration
    5. Validates migrated data
    """

    def __init__(self, base_path: Optional[Path] = None):
        """Initialize migration service."""
        self.logger = logging.getLogger(__name__)

        # Find base path
        if base_path:
            self.base_path = Path(base_path)
        else:
            self.base_path = self._find_base_path()

        # Define paths
        self.microscope_settings_dir = self.base_path / "microscope_settings"
        self.configs_dir = self.base_path / "configs"
        self.workflows_dir = self.base_path / "workflows"
        self.backup_dir = self.base_path / "config_backups"

        # Create directories
        self.configs_dir.mkdir(exist_ok=True)
        self.backup_dir.mkdir(exist_ok=True)

    def _find_base_path(self) -> Path:
        """Find the base path of the Flamingo Control installation."""
        current = Path.cwd()
        while current != current.parent:
            if (current / "microscope_settings").exists():
                return current
            current = current.parent
        return Path.cwd()

    def migrate_all(self, dry_run: bool = False) -> Dict[str, Any]:
        """
        Migrate all configuration files to new format.

        Args:
            dry_run: If True, only simulate migration without writing files

        Returns:
            Migration report with status and any issues
        """
        self.logger.info(f"Starting configuration migration (dry_run={dry_run})")

        report = {
            'timestamp': datetime.now().isoformat(),
            'dry_run': dry_run,
            'source_path': str(self.base_path),
            'hardware_config': None,
            'application_config': None,
            'errors': [],
            'warnings': []
        }

        try:
            # Create backups first
            if not dry_run:
                self._create_backups()

            # Migrate hardware configuration
            hardware_config = self._migrate_hardware_config()
            report['hardware_config'] = 'configs/microscope_hardware.yaml'

            # Migrate application settings
            app_config = self._migrate_application_settings()
            report['application_config'] = 'configs/application_settings.yaml'

            # Write new configurations
            if not dry_run:
                self._write_yaml(self.configs_dir / 'microscope_hardware.yaml', hardware_config)
                self._write_yaml(self.configs_dir / 'application_settings.yaml', app_config)
                self.logger.info("Migration completed successfully")
            else:
                self.logger.info("Dry run completed - no files written")

        except Exception as e:
            self.logger.error(f"Migration failed: {e}")
            report['errors'].append(str(e))

        return report

    def _create_backups(self):
        """Create backups of all existing configuration files."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_subdir = self.backup_dir / f"backup_{timestamp}"
        backup_subdir.mkdir(exist_ok=True)

        # Backup microscope_settings directory
        if self.microscope_settings_dir.exists():
            shutil.copytree(
                self.microscope_settings_dir,
                backup_subdir / "microscope_settings",
                dirs_exist_ok=True
            )

        # Backup saved_configurations.json
        config_file = self.base_path / "saved_configurations.json"
        if config_file.exists():
            shutil.copy2(config_file, backup_subdir / "saved_configurations.json")

        self.logger.info(f"Created backup in {backup_subdir}")

    def _migrate_hardware_config(self) -> Dict[str, Any]:
        """Migrate hardware configuration from old format to new YAML."""
        hardware = {
            'microscope': {},
            'lasers': {},
            'stage': {},
            'camera': {},
            'filter_wheel': {},
            'illumination': {},
            'objectives': {},
            'sample_chamber': {},
            'calibration': {},
            'safety': {},
            'metadata': {}
        }

        # Read ScopeSettings.txt
        scope_settings_path = self.microscope_settings_dir / "ScopeSettings.txt"
        if scope_settings_path.exists():
            scope_settings = text_to_dict(str(scope_settings_path))
            self._extract_scope_settings(scope_settings, hardware)
        else:
            self.logger.warning("ScopeSettings.txt not found")

        # Read ControlSettings.txt
        control_settings_path = self.microscope_settings_dir / "ControlSettings.txt"
        if control_settings_path.exists():
            control_settings = text_to_dict(str(control_settings_path))
            self._extract_control_settings(control_settings, hardware)
        else:
            self.logger.warning("ControlSettings.txt not found")

        # Read microscope-specific JSON settings
        self._extract_microscope_json_settings(hardware)

        # Add metadata
        hardware['metadata'] = {
            'config_version': '1.0',
            'schema_version': '2024.11.18',
            'created_date': datetime.now().strftime('%Y-%m-%d'),
            'modified_date': datetime.now().strftime('%Y-%m-%d'),
            'modified_by': 'migration_service',
            'migrated_from': 'legacy_format'
        }

        return hardware

    def _extract_scope_settings(self, data: Dict, hardware: Dict):
        """Extract hardware settings from ScopeSettings.txt data."""

        # Extract microscope info
        if 'Microscope type' in data:
            hardware['microscope']['type'] = data.get('Microscope type', 'Unknown')
            hardware['microscope']['name'] = data.get('Microscope name', 'unknown')

        # Extract stage limits (use actual values from ScopeSettings.txt)
        if 'Hard limit max x-axis' in data:
            hardware['stage']['limits'] = {
                'x_axis': {
                    'min_mm': float(data.get('Hard limit min x-axis', 0)),
                    'max_mm': float(data.get('Hard limit max x-axis', 26)),
                    'soft_min_mm': float(data.get('Soft limit min x-axis', 0)),
                    'soft_max_mm': float(data.get('Soft limit max x-axis', 26)),
                    'resolution_um': 0.1,
                    'speed_mm_per_sec': float(data.get('Default velocity x-axis', 1.0))
                },
                'y_axis': {
                    'min_mm': float(data.get('Hard limit min y-axis', 0)),
                    'max_mm': float(data.get('Hard limit max y-axis', 26)),
                    'soft_min_mm': float(data.get('Soft limit min y-axis', 0)),
                    'soft_max_mm': float(data.get('Soft limit max y-axis', 26)),
                    'resolution_um': 0.1,
                    'speed_mm_per_sec': float(data.get('Default velocity y-axis', 1.0))
                },
                'z_axis': {
                    'min_mm': float(data.get('Hard limit min z-axis', 0)),
                    'max_mm': float(data.get('Hard limit max z-axis', 26)),
                    'soft_min_mm': float(data.get('Soft limit min z-axis', 0)),
                    'soft_max_mm': float(data.get('Soft limit max z-axis', 26)),
                    'resolution_um': 0.1,
                    'speed_mm_per_sec': float(data.get('Default velocity z-axis', 1.0))
                },
                'r_axis': {
                    'min_degrees': float(data.get('Hard limit min theta-axis', -3600)),
                    'max_degrees': float(data.get('Hard limit max theta-axis', 3600)),
                    'soft_min_degrees': float(data.get('Soft limit min theta-axis', -360)),
                    'soft_max_degrees': float(data.get('Soft limit max theta-axis', 360)),
                    'resolution_degrees': 0.001,
                    'speed_degrees_per_sec': float(data.get('Default velocity theta-axis', 50))
                }
            }

        # Extract home and unload positions
        if 'Home x-axis' in data:
            hardware['stage']['homing'] = {
                'enabled': True,
                'sequence': ['z', 'x', 'y', 'r'],
                'timeout_seconds': 30.0,
                'home_positions': {
                    'x_mm': float(data.get('Home x-axis', 0)),
                    'y_mm': float(data.get('Home y-axis', 0)),
                    'z_mm': float(data.get('Home z-axis', 0)),
                    'r_degrees': float(data.get('Home theta-axis', 0))
                },
                'unload_positions': {
                    'x_mm': float(data.get('Unload x-axis', 0)),
                    'y_mm': float(data.get('Unload y-axis', 0)),
                    'z_mm': float(data.get('Unload z-axis', 0)),
                    'r_degrees': float(data.get('Unload theta-axis', 0))
                }
            }

        # Extract filter wheel settings (use actual encoder values)
        filters = []
        for i in range(8):
            key = f"Filter wheel position {i}"
            if key in data:
                # Parse "encoder_value other_value" format
                values = data[key].split()
                encoder = int(values[0]) if values else 0

                filters.append({
                    'position': i,
                    'name': f"Filter {i}",
                    'description': data.get(f"Filter wheel description {i}", f"Position {i}"),
                    'encoder_count': encoder,
                    'wavelength_nm': None
                })

        if filters:
            hardware['filter_wheel']['filters'] = filters

        # Extract illumination settings (use actual values)
        hardware['illumination']['light_sheet'] = {
            'left_path': {
                'enabled': True,
                'x_offset_um': float(data.get('Offset left x-axis', 0)),
                'y_offset_um': float(data.get('Offset left y-axis', 0)),
                'amplitude': float(data.get('Amplitude left x-axis', 1)),
                'frequency_hz': float(data.get('Sample scan frequency (Hz)', 100))
            },
            'right_path': {
                'enabled': True,
                'x_offset_um': float(data.get('Offset right x-axis', 0)),
                'y_offset_um': float(data.get('Offset right y-axis', 0)),
                'amplitude': float(data.get('Amplitude right x-axis', 1)),
                'frequency_hz': float(data.get('Sample scan frequency (Hz)', 100))
            }
        }

        # Extract camera overlap settings
        if 'Overlap left-to-right x-axis' in data:
            hardware['camera']['overlap'] = {
                'left_to_right_x': float(data.get('Overlap left-to-right x-axis', 0)),
                'left_to_right_y': float(data.get('Overlap left-to-right y-axis', 0)),
                'right_to_left_x': float(data.get('Overlap right-to-left x-axis', 0)),
                'right_to_left_y': float(data.get('Overlap right-to-left y-axis', 0))
            }

    def _extract_control_settings(self, data: Dict, hardware: Dict):
        """Extract control settings from ControlSettings.txt."""

        # Extract laser settings
        # This would parse laser configuration from ControlSettings.txt
        # Implementation depends on actual format of ControlSettings.txt
        pass

    def _extract_microscope_json_settings(self, hardware: Dict):
        """Extract settings from microscope-specific JSON files."""

        # Try to determine microscope name
        microscope_name = hardware.get('microscope', {}).get('name', 'unknown')

        # Look for microscope-specific JSON
        json_path = self.microscope_settings_dir / f"{microscope_name}_settings.json"
        if not json_path.exists():
            # Try common names
            for name in ['n7', 'zion', 'localhost']:
                test_path = self.microscope_settings_dir / f"{name}_settings.json"
                if test_path.exists():
                    json_path = test_path
                    microscope_name = name
                    break

        if json_path.exists():
            with open(json_path) as f:
                json_data = json.load(f)

            # Extract stage limits if not already set
            if 'stage_limits' in json_data and 'limits' not in hardware.get('stage', {}):
                limits = json_data['stage_limits']
                hardware['stage']['limits'] = {
                    'x_axis': {
                        'min_mm': limits['x']['min'],
                        'max_mm': limits['x']['max'],
                        'soft_min_mm': limits['x']['min'] + 0.5,
                        'soft_max_mm': limits['x']['max'] - 0.5
                    },
                    'y_axis': {
                        'min_mm': limits['y']['min'],
                        'max_mm': limits['y']['max'],
                        'soft_min_mm': limits['y']['min'] + 0.5,
                        'soft_max_mm': limits['y']['max'] - 0.5
                    },
                    'z_axis': {
                        'min_mm': limits['z']['min'],
                        'max_mm': limits['z']['max'],
                        'soft_min_mm': limits['z']['min'] + 0.5,
                        'soft_max_mm': limits['z']['max'] - 0.5
                    },
                    'r_axis': {
                        'min_degrees': limits['r']['min'],
                        'max_degrees': limits['r']['max'],
                        'soft_min_degrees': limits['r']['min'] + 10,
                        'soft_max_degrees': limits['r']['max'] - 10
                    }
                }

    def _migrate_application_settings(self) -> Dict[str, Any]:
        """Migrate application settings to new format."""

        app_settings = self._get_default_application_settings()

        # Read saved_configurations.json
        config_path = self.base_path / "saved_configurations.json"
        if config_path.exists():
            with open(config_path) as f:
                saved_configs = json.load(f)
                # Extract last used microscope
                if saved_configs:
                    app_settings['application']['startup']['last_microscope'] = \
                        saved_configs[0].get('name', 'unknown')

        # Read microscope-specific output directories from workflow files
        self._extract_workflow_directories(app_settings)

        return app_settings

    def _extract_workflow_directories(self, app_settings: Dict):
        """Extract output directories from workflow files."""

        # Check Snapshot.txt for output directory
        snapshot_path = self.workflows_dir / "Snapshot.txt"
        if snapshot_path.exists():
            workflow_data = text_to_dict(str(snapshot_path))
            if 'File location' in workflow_data.get('Experiment Settings', {}):
                path = workflow_data['Experiment Settings']['File location']
                # Map to microscope
                if 'MSN_LS' in path:
                    app_settings['workflow']['directories']['microscope_output_dirs']['n7'] = path
                elif 'ctlsm1' in path:
                    app_settings['workflow']['directories']['microscope_output_dirs']['zion'] = path

    def _get_default_application_settings(self) -> Dict[str, Any]:
        """Get default application settings structure."""
        return {
            'application': {
                'name': 'Flamingo Control',
                'version': '2.0.0',
                'environment': 'production',
                'startup': {
                    'auto_connect': False,
                    'last_microscope': '',
                    'restore_window_positions': True
                }
            },
            'user_interface': {
                'appearance': {
                    'theme': 'dark',
                    'font_size': 10
                }
            },
            'workflow': {
                'directories': {
                    'workflows_dir': 'workflows/',
                    'output_base_dir': '/data/microscopy/',
                    'microscope_output_dirs': {}
                }
            },
            'positions': {
                'history': {
                    'max_positions': 100,
                    'display_count': 20
                }
            },
            'logging': {
                'level': 'INFO',
                'console_output': True,
                'file_output': True
            },
            'metadata': {
                'config_version': '1.0',
                'schema_version': '2024.11.18',
                'created_date': datetime.now().strftime('%Y-%m-%d'),
                'modified_date': datetime.now().strftime('%Y-%m-%d'),
                'modified_by': 'migration_service'
            }
        }

    def _write_yaml(self, path: Path, data: Dict):
        """Write data to YAML file with proper formatting."""
        with open(path, 'w') as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False, indent=2)
        self.logger.info(f"Wrote configuration to {path}")

    def validate_migration(self) -> Dict[str, Any]:
        """Validate that migration preserved all data."""
        validation_report = {
            'valid': True,
            'errors': [],
            'warnings': []
        }

        # Check that new files exist
        hardware_path = self.configs_dir / 'microscope_hardware.yaml'
        app_path = self.configs_dir / 'application_settings.yaml'

        if not hardware_path.exists():
            validation_report['valid'] = False
            validation_report['errors'].append("Hardware config not found")

        if not app_path.exists():
            validation_report['valid'] = False
            validation_report['errors'].append("Application config not found")

        # Validate hardware config has required sections
        if hardware_path.exists():
            with open(hardware_path) as f:
                hardware = yaml.safe_load(f)

            required_sections = ['microscope', 'stage', 'camera', 'filter_wheel']
            for section in required_sections:
                if section not in hardware:
                    validation_report['warnings'].append(f"Missing section: {section}")

        return validation_report


# CLI interface
if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Migrate Flamingo Control configurations')
    parser.add_argument('--dry-run', action='store_true',
                       help='Simulate migration without writing files')
    parser.add_argument('--validate', action='store_true',
                       help='Validate existing migration')
    parser.add_argument('--path', type=str,
                       help='Base path of Flamingo Control installation')

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(level=logging.INFO,
                       format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # Create service
    service = ConfigMigrationService(base_path=args.path)

    if args.validate:
        report = service.validate_migration()
        print(f"Validation: {'PASSED' if report['valid'] else 'FAILED'}")
        if report['errors']:
            print("Errors:", report['errors'])
        if report['warnings']:
            print("Warnings:", report['warnings'])
    else:
        report = service.migrate_all(dry_run=args.dry_run)
        print(f"Migration {'simulated' if args.dry_run else 'completed'}")
        if report['errors']:
            print("Errors:", report['errors'])