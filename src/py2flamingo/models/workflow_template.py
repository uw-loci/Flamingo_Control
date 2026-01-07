# src/py2flamingo/models/workflow_template.py

"""
Data model for workflow templates.

Workflow templates allow users to save and load workflow configurations
for quick reuse of common acquisition setups.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Dict, Any, Optional


@dataclass
class WorkflowTemplate:
    """
    Named workflow template containing all settings for a workflow configuration.

    Attributes:
        name: User-friendly name for the template
        workflow_type: Type of workflow (SNAPSHOT, ZSTACK, TIME_LAPSE, TILE, MULTI_ANGLE)
        settings: Complete workflow settings dictionary from WorkflowView.get_workflow_dict()
        description: Optional description of the template's purpose
        created_date: ISO format timestamp when template was created
    """
    name: str
    workflow_type: str
    settings: Dict[str, Any]
    description: str = ""
    created_date: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """Convert template to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'WorkflowTemplate':
        """Create template from dictionary (loaded from JSON)."""
        return cls(
            name=data['name'],
            workflow_type=data['workflow_type'],
            settings=data['settings'],
            description=data.get('description', ''),
            created_date=data.get('created_date', datetime.now().isoformat())
        )

    def get_display_name(self) -> str:
        """Get formatted display name with workflow type."""
        return f"{self.name} ({self.workflow_type})"

    def get_summary(self) -> str:
        """Get a brief summary of the template settings."""
        summary_parts = [f"Type: {self.workflow_type}"]

        # Extract key settings for summary
        if 'Stack Settings' in self.settings:
            stack = self.settings['Stack Settings']
            if 'Number of planes' in stack:
                summary_parts.append(f"Planes: {stack['Number of planes']}")
            if 'Change in Z axis (mm)' in stack:
                z_step_mm = float(stack['Change in Z axis (mm)'])
                summary_parts.append(f"Z step: {z_step_mm * 1000:.1f} µm")

        if 'Experiment Settings' in self.settings:
            exp = self.settings['Experiment Settings']
            if 'Sample' in exp and exp['Sample']:
                summary_parts.append(f"Sample: {exp['Sample']}")

        return " | ".join(summary_parts)
