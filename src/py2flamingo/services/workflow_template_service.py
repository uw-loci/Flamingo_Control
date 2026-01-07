# src/py2flamingo/services/workflow_template_service.py

"""
Service for managing workflow templates.

This service handles saving, loading, and deleting named workflow templates
that allow users to quickly apply frequently-used acquisition configurations.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from py2flamingo.models.workflow_template import WorkflowTemplate


class WorkflowTemplateService:
    """
    Service for managing workflow templates.

    Templates are stored in a JSON file in the microscope_settings directory.
    """

    def __init__(self, templates_file: Optional[str] = None):
        """
        Initialize workflow template service.

        Args:
            templates_file: Path to templates JSON file. If None, uses default location.
        """
        self.logger = logging.getLogger(__name__)

        if templates_file is None:
            settings_dir = Path("microscope_settings")
            settings_dir.mkdir(exist_ok=True)
            self.templates_file = settings_dir / "workflow_templates.json"
        else:
            self.templates_file = Path(templates_file)

        self._templates: Dict[str, WorkflowTemplate] = {}
        self._load_templates()

    def _load_templates(self) -> None:
        """Load templates from JSON file."""
        try:
            if self.templates_file.exists():
                with open(self.templates_file, 'r') as f:
                    data = json.load(f)
                    self._templates = {
                        name: WorkflowTemplate.from_dict(template_data)
                        for name, template_data in data.items()
                    }
                self.logger.info(
                    f"Loaded {len(self._templates)} workflow templates from {self.templates_file}"
                )
            else:
                self.logger.info(
                    f"No template file found at {self.templates_file}, starting with empty templates"
                )
                self._templates = {}
        except Exception as e:
            self.logger.error(f"Error loading templates: {e}", exc_info=True)
            self._templates = {}

    def _save_templates(self) -> None:
        """Save templates to JSON file."""
        try:
            data = {
                name: template.to_dict()
                for name, template in self._templates.items()
            }
            with open(self.templates_file, 'w') as f:
                json.dump(data, f, indent=2)
            self.logger.info(f"Saved {len(self._templates)} templates to {self.templates_file}")
        except Exception as e:
            self.logger.error(f"Error saving templates: {e}", exc_info=True)
            raise

    def save_template(
        self,
        name: str,
        workflow_type: str,
        settings: Dict,
        description: str = ""
    ) -> WorkflowTemplate:
        """
        Save a workflow template.

        Args:
            name: Name for the template
            workflow_type: Type of workflow (SNAPSHOT, ZSTACK, etc.)
            settings: Complete workflow settings dictionary
            description: Optional description

        Returns:
            The created WorkflowTemplate

        Raises:
            ValueError: If name is empty or invalid
        """
        if not name or not name.strip():
            raise ValueError("Template name cannot be empty")

        name = name.strip()

        template = WorkflowTemplate(
            name=name,
            workflow_type=workflow_type,
            settings=settings,
            description=description
        )
        self._templates[name] = template
        self._save_templates()

        self.logger.info(f"Saved workflow template '{name}' (type: {workflow_type})")
        return template

    def get_template(self, name: str) -> Optional[WorkflowTemplate]:
        """
        Get a template by name.

        Args:
            name: Template name

        Returns:
            WorkflowTemplate if found, None otherwise
        """
        return self._templates.get(name)

    def delete_template(self, name: str) -> bool:
        """
        Delete a template.

        Args:
            name: Template name

        Returns:
            True if template was deleted, False if not found
        """
        if name in self._templates:
            del self._templates[name]
            self._save_templates()
            self.logger.info(f"Deleted workflow template '{name}'")
            return True
        return False

    def list_templates(self) -> List[WorkflowTemplate]:
        """
        Get list of all templates.

        Returns:
            List of templates sorted by name
        """
        return sorted(self._templates.values(), key=lambda t: t.name)

    def get_template_names(self) -> List[str]:
        """
        Get list of template names.

        Returns:
            List of template names sorted alphabetically
        """
        return sorted(self._templates.keys())

    def get_templates_by_type(self, workflow_type: str) -> List[WorkflowTemplate]:
        """
        Get templates filtered by workflow type.

        Args:
            workflow_type: Workflow type to filter by

        Returns:
            List of templates matching the workflow type
        """
        return [
            t for t in self._templates.values()
            if t.workflow_type == workflow_type
        ]

    def template_exists(self, name: str) -> bool:
        """
        Check if template exists.

        Args:
            name: Template name

        Returns:
            True if template exists
        """
        return name in self._templates

    def rename_template(self, old_name: str, new_name: str) -> bool:
        """
        Rename a template.

        Args:
            old_name: Current template name
            new_name: New template name

        Returns:
            True if renamed successfully, False otherwise
        """
        if old_name not in self._templates:
            return False
        if not new_name or not new_name.strip():
            raise ValueError("New template name cannot be empty")

        new_name = new_name.strip()
        if new_name in self._templates and new_name != old_name:
            raise ValueError(f"Template '{new_name}' already exists")

        template = self._templates.pop(old_name)
        template.name = new_name
        self._templates[new_name] = template
        self._save_templates()

        self.logger.info(f"Renamed template '{old_name}' to '{new_name}'")
        return True

    def clear_all_templates(self) -> None:
        """Delete all templates (for testing/reset)."""
        self._templates.clear()
        self._save_templates()
        self.logger.warning("Cleared all workflow templates")
