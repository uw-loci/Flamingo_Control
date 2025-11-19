"""Workflow Repository - Handles all file I/O operations for workflows.

This module manages loading, saving, and organizing workflow files,
keeping file operations separate from business logic.
"""

import logging
import json
import yaml
from typing import Optional, List, Dict, Any, Union
from pathlib import Path
from datetime import datetime
import shutil

from ..models.data.workflow import (
    Workflow, WorkflowType, IlluminationSettings,
    StackSettings, TileSettings, TimeLapseSettings, ExperimentSettings
)
from ..models.hardware.stage import Position
from ..utils.workflow_parser import WorkflowParser
from ..core.errors import FlamingoError


logger = logging.getLogger(__name__)


class RepositoryError(FlamingoError):
    """Base exception for repository operations."""
    pass


class WorkflowNotFoundError(RepositoryError):
    """Raised when a workflow file is not found."""
    pass


class WorkflowFormatError(RepositoryError):
    """Raised when workflow file format is invalid."""
    pass


class WorkflowRepository:
    """Manages workflow file storage and retrieval.

    Handles:
    - Loading workflows from various formats (.txt, .json, .yaml)
    - Saving workflows to files
    - Managing workflow templates
    - Organizing workflow directories
    - Backup and versioning
    """

    SUPPORTED_FORMATS = {'.txt', '.json', '.yaml', '.yml'}
    DEFAULT_FORMAT = '.json'

    def __init__(self, base_directory: Optional[Path] = None):
        """Initialize repository.

        Args:
            base_directory: Base directory for workflows (default: workflows/)
        """
        self.base_directory = Path(base_directory or "workflows")
        self._ensure_directory_structure()
        self._parser = WorkflowParser()
        self._template_cache: Dict[str, Workflow] = {}

    def _ensure_directory_structure(self):
        """Ensure workflow directory structure exists."""
        directories = [
            self.base_directory,
            self.base_directory / "templates",
            self.base_directory / "saved",
            self.base_directory / "completed",
            self.base_directory / "backups",
            self.base_directory / "exports"
        ]

        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)

        logger.debug(f"Ensured directory structure under {self.base_directory}")

    # ==================== Loading ====================

    def load(self, file_path: Union[str, Path]) -> Workflow:
        """Load a workflow from a file.

        Automatically detects format based on file extension.

        Args:
            file_path: Path to workflow file

        Returns:
            Loaded workflow

        Raises:
            WorkflowNotFoundError: If file doesn't exist
            WorkflowFormatError: If format is unsupported or invalid
        """
        file_path = Path(file_path)

        if not file_path.exists():
            raise WorkflowNotFoundError(f"Workflow file not found: {file_path}")

        # Check format
        suffix = file_path.suffix.lower()
        if suffix not in self.SUPPORTED_FORMATS:
            raise WorkflowFormatError(
                f"Unsupported format: {suffix}. "
                f"Supported formats: {', '.join(self.SUPPORTED_FORMATS)}"
            )

        try:
            # Load based on format
            if suffix == '.txt':
                return self._load_txt(file_path)
            elif suffix == '.json':
                return self._load_json(file_path)
            elif suffix in {'.yaml', '.yml'}:
                return self._load_yaml(file_path)
            else:
                raise WorkflowFormatError(f"Unknown format: {suffix}")

        except Exception as e:
            logger.error(f"Failed to load workflow from {file_path}: {e}")
            raise WorkflowFormatError(f"Failed to parse workflow: {e}")

    def _load_txt(self, file_path: Path) -> Workflow:
        """Load workflow from legacy text format."""
        with open(file_path, 'r') as f:
            content = f.read()

        # Use parser to convert text to dictionary
        workflow_dict = self._parser.parse_workflow_text(content)
        return self._dict_to_workflow(workflow_dict, name=file_path.stem)

    def _load_json(self, file_path: Path) -> Workflow:
        """Load workflow from JSON format."""
        with open(file_path, 'r') as f:
            workflow_dict = json.load(f)

        return self._dict_to_workflow(workflow_dict, name=file_path.stem)

    def _load_yaml(self, file_path: Path) -> Workflow:
        """Load workflow from YAML format."""
        with open(file_path, 'r') as f:
            workflow_dict = yaml.safe_load(f)

        return self._dict_to_workflow(workflow_dict, name=file_path.stem)

    def _dict_to_workflow(self, data: Dict[str, Any], name: str = "Workflow") -> Workflow:
        """Convert dictionary to Workflow object."""
        # Handle both new format and legacy format
        if "workflow_type" in data:
            # New format - direct deserialization
            return self._deserialize_workflow(data)
        else:
            # Legacy format - parse old structure
            return self._parse_legacy_workflow(data, name)

    def _deserialize_workflow(self, data: Dict[str, Any]) -> Workflow:
        """Deserialize workflow from modern format."""
        workflow = Workflow(
            workflow_type=WorkflowType(data["workflow_type"]),
            name=data.get("name", "Workflow"),
            start_position=Position(**data["start_position"]) if "start_position" in data else Position(0, 0, 0, 0),
            end_position=Position(**data["end_position"]) if "end_position" in data else None
        )

        # Deserialize settings
        if "illumination" in data:
            workflow.illumination = IlluminationSettings(**data["illumination"])

        if "stack_settings" in data:
            workflow.stack_settings = StackSettings(**data["stack_settings"])

        if "tile_settings" in data:
            workflow.tile_settings = TileSettings(**data["tile_settings"])

        if "time_lapse_settings" in data:
            workflow.time_lapse_settings = TimeLapseSettings(**data["time_lapse_settings"])

        if "experiment_settings" in data:
            workflow.experiment_settings = ExperimentSettings(**data["experiment_settings"])

        # Deserialize positions and channels
        if "positions" in data:
            workflow.positions = [Position(**p) for p in data["positions"]]

        if "channels" in data:
            workflow.channels = data["channels"]

        return workflow

    def _parse_legacy_workflow(self, data: Dict[str, Any], name: str) -> Workflow:
        """Parse workflow from legacy format."""
        # Extract positions
        start_pos = Position(0, 0, 0, 0)
        if "Start Position" in data:
            pos_data = data["Start Position"]
            start_pos = Position(
                x=float(pos_data.get("X (mm)", 0)),
                y=float(pos_data.get("Y (mm)", 0)),
                z=float(pos_data.get("Z (mm)", 0)),
                r=float(pos_data.get("Angle (degrees)", 0))
            )

        # Determine workflow type
        workflow_type = WorkflowType.SNAPSHOT
        if "Stack Settings" in data:
            stack_data = data["Stack Settings"]
            if int(stack_data.get("Number of planes", 1)) > 1:
                workflow_type = WorkflowType.ZSTACK

        # Create workflow
        workflow = Workflow(
            workflow_type=workflow_type,
            name=name,
            start_position=start_pos
        )

        # Parse illumination
        if "Illumination Source" in data:
            workflow.illumination = self._parse_legacy_illumination(data["Illumination Source"])

        # Parse stack settings
        if "Stack Settings" in data:
            workflow.stack_settings = self._parse_legacy_stack(data["Stack Settings"])

        # Parse experiment settings
        if "Experiment Settings" in data:
            workflow.experiment_settings = self._parse_legacy_experiment(data["Experiment Settings"])

        return workflow

    def _parse_legacy_illumination(self, illum_data: Dict[str, str]) -> IlluminationSettings:
        """Parse legacy illumination format."""
        settings = IlluminationSettings()

        for source, value in illum_data.items():
            parts = value.split()
            if len(parts) >= 2:
                if "Laser" in source:
                    settings.laser_channel = source
                    settings.laser_power_mw = float(parts[0])
                    settings.laser_enabled = bool(int(parts[1]))
                elif "LED" in source:
                    settings.led_channel = source
                    settings.led_intensity_percent = float(parts[0])
                    settings.led_enabled = bool(int(parts[1]))

        return settings

    def _parse_legacy_stack(self, stack_data: Dict[str, Any]) -> StackSettings:
        """Parse legacy stack settings."""
        return StackSettings(
            num_planes=int(stack_data.get("Number of planes", 1)),
            z_step_um=float(stack_data.get("Change in Z axis (mm)", 0.01)) * 1000,
            z_velocity_mm_s=float(str(stack_data.get("Z stage velocity (mm/s)", "0.4"))),
            bidirectional=str(stack_data.get("Bidirectional", "false")).lower() == "true"
        )

    def _parse_legacy_experiment(self, exp_data: Dict[str, Any]) -> ExperimentSettings:
        """Parse legacy experiment settings."""
        save_format = exp_data.get("Save image data", "NotSaved")

        return ExperimentSettings(
            save_data=save_format != "NotSaved",
            save_format="tiff" if save_format == "Tiff" else "tiff",
            save_directory=Path(exp_data.get("Save image directory", "data")),
            comment=exp_data.get("Comments", ""),
            max_projection_display=str(exp_data.get("Display max projection", "true")).lower() == "true"
        )

    # ==================== Saving ====================

    def save(self, workflow: Workflow,
            file_path: Optional[Union[str, Path]] = None,
            format: Optional[str] = None) -> Path:
        """Save a workflow to a file.

        Args:
            workflow: Workflow to save
            file_path: Optional file path (auto-generated if not provided)
            format: File format (.json, .txt, .yaml)

        Returns:
            Path where workflow was saved
        """
        # Determine file path
        if file_path:
            file_path = Path(file_path)
        else:
            file_path = self._generate_file_path(workflow, format)

        # Ensure parent directory exists
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # Determine format from extension
        suffix = file_path.suffix.lower() or format or self.DEFAULT_FORMAT
        if not suffix.startswith('.'):
            suffix = f'.{suffix}'

        # Add suffix if missing
        if not file_path.suffix:
            file_path = file_path.with_suffix(suffix)

        # Save based on format
        try:
            if suffix == '.txt':
                self._save_txt(workflow, file_path)
            elif suffix == '.json':
                self._save_json(workflow, file_path)
            elif suffix in {'.yaml', '.yml'}:
                self._save_yaml(workflow, file_path)
            else:
                raise WorkflowFormatError(f"Unsupported save format: {suffix}")

            logger.info(f"Saved workflow to {file_path}")
            return file_path

        except Exception as e:
            logger.error(f"Failed to save workflow: {e}")
            raise RepositoryError(f"Failed to save workflow: {e}")

    def _generate_file_path(self, workflow: Workflow, format: Optional[str] = None) -> Path:
        """Generate automatic file path for workflow."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = workflow.name.replace(' ', '_').replace('/', '_')
        filename = f"{safe_name}_{timestamp}"

        suffix = format or self.DEFAULT_FORMAT
        if not suffix.startswith('.'):
            suffix = f'.{suffix}'

        return self.base_directory / "saved" / f"{filename}{suffix}"

    def _save_txt(self, workflow: Workflow, file_path: Path):
        """Save workflow in legacy text format."""
        # Convert to legacy dictionary format
        legacy_dict = workflow.to_workflow_dict()

        # Format as text
        lines = []
        for section, content in legacy_dict.items():
            lines.append(f"[{section}]")

            if isinstance(content, dict):
                for key, value in content.items():
                    lines.append(f"{key}: {value}")
            else:
                lines.append(str(content))

            lines.append("")  # Empty line between sections

        with open(file_path, 'w') as f:
            f.write('\n'.join(lines))

    def _save_json(self, workflow: Workflow, file_path: Path):
        """Save workflow in JSON format."""
        data = self._workflow_to_dict(workflow)

        with open(file_path, 'w') as f:
            json.dump(data, f, indent=2, default=str)

    def _save_yaml(self, workflow: Workflow, file_path: Path):
        """Save workflow in YAML format."""
        data = self._workflow_to_dict(workflow)

        with open(file_path, 'w') as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    def _workflow_to_dict(self, workflow: Workflow) -> Dict[str, Any]:
        """Convert workflow to dictionary for serialization."""
        data = {
            "workflow_type": workflow.workflow_type.value,
            "name": workflow.name,
            "start_position": workflow.start_position.to_dict() if workflow.start_position else None,
            "end_position": workflow.end_position.to_dict() if workflow.end_position else None,
        }

        # Add settings if present
        if workflow.illumination:
            data["illumination"] = {
                "laser_channel": workflow.illumination.laser_channel,
                "laser_power_mw": workflow.illumination.laser_power_mw,
                "laser_enabled": workflow.illumination.laser_enabled,
                "led_channel": workflow.illumination.led_channel,
                "led_intensity_percent": workflow.illumination.led_intensity_percent,
                "led_enabled": workflow.illumination.led_enabled
            }

        if workflow.stack_settings:
            data["stack_settings"] = {
                "num_planes": workflow.stack_settings.num_planes,
                "z_step_um": workflow.stack_settings.z_step_um,
                "z_velocity_mm_s": workflow.stack_settings.z_velocity_mm_s,
                "bidirectional": workflow.stack_settings.bidirectional
            }

        if workflow.experiment_settings:
            data["experiment_settings"] = {
                "save_data": workflow.experiment_settings.save_data,
                "save_format": workflow.experiment_settings.save_format,
                "save_directory": str(workflow.experiment_settings.save_directory),
                "comment": workflow.experiment_settings.comment
            }

        # Add positions and channels if present
        if workflow.positions:
            data["positions"] = [p.to_dict() for p in workflow.positions]

        if workflow.channels:
            data["channels"] = workflow.channels

        return data

    # ==================== Templates ====================

    def save_as_template(self, workflow: Workflow, template_name: str) -> Path:
        """Save workflow as a reusable template.

        Args:
            workflow: Workflow to save as template
            template_name: Name for the template

        Returns:
            Path where template was saved
        """
        template_path = self.base_directory / "templates" / f"{template_name}.json"
        self.save(workflow, template_path)

        # Update cache
        self._template_cache[template_name] = workflow

        logger.info(f"Saved workflow template: {template_name}")
        return template_path

    def load_template(self, template_name: str) -> Workflow:
        """Load a workflow template.

        Args:
            template_name: Name of template to load

        Returns:
            Template workflow

        Raises:
            WorkflowNotFoundError: If template doesn't exist
        """
        # Check cache first
        if template_name in self._template_cache:
            return self._template_cache[template_name]

        # Load from file
        template_path = self.base_directory / "templates" / f"{template_name}.json"

        if not template_path.exists():
            # Try without extension
            template_path = self.base_directory / "templates" / template_name
            if not template_path.exists():
                raise WorkflowNotFoundError(f"Template not found: {template_name}")

        workflow = self.load(template_path)
        self._template_cache[template_name] = workflow
        return workflow

    def get_templates(self) -> Dict[str, Workflow]:
        """Get all available templates.

        Returns:
            Dictionary of template name to workflow
        """
        templates = {}
        template_dir = self.base_directory / "templates"

        for file_path in template_dir.glob("*"):
            if file_path.suffix in self.SUPPORTED_FORMATS:
                try:
                    name = file_path.stem
                    templates[name] = self.load_template(name)
                except Exception as e:
                    logger.warning(f"Failed to load template {file_path}: {e}")

        return templates

    # ==================== Directory Management ====================

    def list_workflows(self, directory: Optional[Union[str, Path]] = None) -> List[Path]:
        """List all workflow files in a directory.

        Args:
            directory: Directory to search (default: saved/)

        Returns:
            List of workflow file paths
        """
        if directory:
            search_dir = Path(directory)
        else:
            search_dir = self.base_directory / "saved"

        workflow_files = []
        for suffix in self.SUPPORTED_FORMATS:
            workflow_files.extend(search_dir.glob(f"*{suffix}"))

        return sorted(workflow_files, key=lambda p: p.stat().st_mtime, reverse=True)

    def backup_workflow(self, file_path: Path) -> Path:
        """Create a backup of a workflow file.

        Args:
            file_path: Workflow file to backup

        Returns:
            Path to backup file
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{file_path.stem}_backup_{timestamp}{file_path.suffix}"
        backup_path = self.base_directory / "backups" / backup_name

        shutil.copy2(file_path, backup_path)
        logger.info(f"Created backup: {backup_path}")
        return backup_path

    def export_workflow(self, workflow: Workflow, export_format: str = "json") -> Path:
        """Export workflow for external use.

        Args:
            workflow: Workflow to export
            export_format: Export format

        Returns:
            Path to exported file
        """
        export_path = self.base_directory / "exports"
        return self.save(workflow, file_path=export_path / workflow.name, format=export_format)

    def clean_old_files(self, days: int = 30):
        """Clean up old workflow files.

        Args:
            days: Delete files older than this many days
        """
        cutoff_time = datetime.now().timestamp() - (days * 24 * 60 * 60)

        for directory in ["completed", "backups"]:
            dir_path = self.base_directory / directory

            for file_path in dir_path.glob("*"):
                if file_path.stat().st_mtime < cutoff_time:
                    file_path.unlink()
                    logger.info(f"Deleted old file: {file_path}")

    # ==================== Import/Export ====================

    def import_legacy_workflows(self, legacy_directory: Path) -> List[Workflow]:
        """Import workflows from legacy directory structure.

        Args:
            legacy_directory: Directory containing legacy workflows

        Returns:
            List of imported workflows
        """
        imported = []

        for file_path in Path(legacy_directory).rglob("*.txt"):
            try:
                workflow = self.load(file_path)
                # Save in new format
                new_path = self.save(workflow)
                imported.append(workflow)
                logger.info(f"Imported legacy workflow: {file_path} -> {new_path}")
            except Exception as e:
                logger.error(f"Failed to import {file_path}: {e}")

        return imported