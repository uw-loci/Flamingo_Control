# Phase 2 Consolidation Plan: Models, Workflows, and Image Processing
**Date:** 2024-11-18
**Priority:** High
**Estimated Duration:** 2-3 weeks

---

## ✅ UPDATE (2025-11-18): Plan 1 Complete

**Plan 1 (Model Classes Consolidation) has been completed!**

### Key Deliverables from Plan 1:
- ✅ Created comprehensive model hierarchy in `models/` directory
- ✅ Implemented all hardware models (Stage, Camera, Laser, FilterWheel, Objectives)
- ✅ Implemented core data models (Image, Workflow, Sample)
- ✅ Created base infrastructure (BaseModel, ValidatedModel, ValidationError)
- ✅ **Addressed original laser power bug** with centralized Laser model

### Important Notes for Plans 2 & 3:

#### For Plan 2 (Workflow Management):
- **USE** the new `models.data.workflow.Workflow` class as foundation
- **USE** `WorkflowStep` for execution tracking
- **USE** `WorkflowState` enum for status management
- The new Workflow model already has:
  - Step generation (`generate_steps()`)
  - Progress tracking (`get_progress()`)
  - Time estimation (`estimate_duration()`)
  - Legacy format support (`to_workflow_dict()`)

#### For Plan 3 (Image Processing):
- **USE** the new `models.data.image.ImageData` class as core structure
- **USE** `ImageMetadata` for acquisition parameters
- **USE** `ImageStack` for collections
- The new ImageData model already has:
  - Flexible dimensions (TCZYX)
  - Channel/plane extraction
  - Statistics computation
  - Maximum projections

---

## Overview

This document outlines the plan for three major consolidation efforts:
1. **Model Classes Consolidation** - ✅ COMPLETE (See `/claude-reports/plan1-model-consolidation-complete.md`)
2. **Workflow Management Unification** - Single pipeline for workflow handling
3. **Image Processing Consolidation** - Centralized image operations

---

# PLAN 1: Consolidate Model Classes

## Current State Analysis

### Scattered Model Files
Currently models are distributed across multiple locations without clear organization:

```
src/py2flamingo/
├── models/
│   ├── command.py         # Command types
│   ├── connection.py      # Connection models
│   ├── microscope.py      # Microscope state (Position class)
│   ├── sample.py          # Sample data
│   └── settings.py        # MicroscopeSettings dataclass
├── services/
│   └── [various services with inline models]
└── controllers/
    └── [controllers with view-specific models]
```

### Issues
- No clear separation between domain models and DTOs
- Some models defined inline in services
- Inconsistent use of dataclasses vs regular classes
- Missing models for important concepts (Image, Workflow, etc.)

## Target Architecture

```
src/py2flamingo/models/
├── __init__.py            # Export all models
├── base.py                # Base model classes and interfaces
│
├── hardware/              # Physical hardware representations
│   ├── __init__.py
│   ├── microscope.py      # Microscope class with all components
│   ├── stage.py           # Stage, Position, Limits
│   ├── camera.py          # Camera, ROI, AcquisitionSettings
│   ├── laser.py           # Laser, LaserSettings, PowerLimits
│   ├── filter_wheel.py    # FilterWheel, Filter, FilterPosition
│   └── objectives.py      # Objective, ObjectiveProperties
│
├── data/                  # Data and file representations
│   ├── __init__.py
│   ├── image.py           # Image, ImageMetadata, ImageStack
│   ├── workflow.py        # Workflow, WorkflowStep, WorkflowResult
│   ├── sample.py          # Sample, SampleMetadata, SampleRegion
│   └── dataset.py         # Dataset, DatasetMetadata
│
├── protocol/              # Communication protocol models
│   ├── __init__.py
│   ├── command.py         # Command, CommandCode, CommandData
│   ├── response.py        # Response, ResponseStatus, ResponseData
│   └── message.py         # Message, MessageType, Payload
│
├── configuration/         # Configuration models
│   ├── __init__.py
│   ├── hardware_config.py # HardwareConfiguration
│   ├── app_config.py      # ApplicationConfiguration
│   └── connection.py      # ConnectionConfig, MicroscopeEndpoint
│
└── dto/                   # Data Transfer Objects for API/GUI
    ├── __init__.py
    ├── requests.py        # Request DTOs
    └── responses.py       # Response DTOs
```

## Implementation Steps

### Step 1: Create Base Model Infrastructure (Day 1)
```python
# models/base.py
from dataclasses import dataclass, field
from typing import Optional, Dict, Any
from datetime import datetime
import uuid

@dataclass
class BaseModel:
    """Base class for all domain models."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def update(self):
        """Mark model as updated."""
        self.updated_at = datetime.now()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        pass

    @classmethod
    def from_dict(cls, data: Dict[str, Any]):
        """Create from dictionary."""
        pass

@dataclass
class ValidatedModel(BaseModel):
    """Base class for models requiring validation."""

    def __post_init__(self):
        """Validate after initialization."""
        self.validate()

    def validate(self):
        """Override to implement validation logic."""
        pass
```

### Step 2: Create Hardware Models (Day 2-3)

#### Stage Model
```python
# models/hardware/stage.py
from dataclasses import dataclass
from typing import Optional, Tuple
from ..base import ValidatedModel

@dataclass
class StageLimits:
    """Stage axis limits in millimeters."""
    min_mm: float
    max_mm: float
    soft_min_mm: float
    soft_max_mm: float

    def is_within_limits(self, value: float) -> bool:
        """Check if value is within hard limits."""
        return self.min_mm <= value <= self.max_mm

    def is_within_soft_limits(self, value: float) -> bool:
        """Check if value is within soft limits."""
        return self.soft_min_mm <= value <= self.soft_max_mm

@dataclass
class Position(ValidatedModel):
    """4D position (X, Y, Z, R)."""
    x_mm: float
    y_mm: float
    z_mm: float
    r_degrees: float

    def validate(self):
        """Validate position values."""
        if not all(isinstance(v, (int, float)) for v in [self.x_mm, self.y_mm, self.z_mm, self.r_degrees]):
            raise ValueError("Position values must be numeric")

    def distance_to(self, other: 'Position') -> float:
        """Calculate Euclidean distance to another position."""
        import math
        return math.sqrt(
            (self.x_mm - other.x_mm)**2 +
            (self.y_mm - other.y_mm)**2 +
            (self.z_mm - other.z_mm)**2
        )

    def __add__(self, other: 'Position') -> 'Position':
        """Add two positions."""
        return Position(
            x_mm=self.x_mm + other.x_mm,
            y_mm=self.y_mm + other.y_mm,
            z_mm=self.z_mm + other.z_mm,
            r_degrees=self.r_degrees + other.r_degrees
        )

@dataclass
class Stage(BaseModel):
    """Complete stage representation."""
    model: str
    serial_number: str
    current_position: Position
    limits: Dict[str, StageLimits]  # 'x', 'y', 'z', 'r' axes
    is_homed: bool = False
    is_moving: bool = False

    def can_move_to(self, position: Position) -> Tuple[bool, Optional[str]]:
        """Check if stage can move to position."""
        if not self.is_homed:
            return False, "Stage not homed"

        if not self.limits['x'].is_within_limits(position.x_mm):
            return False, f"X position {position.x_mm} out of limits"

        if not self.limits['y'].is_within_limits(position.y_mm):
            return False, f"Y position {position.y_mm} out of limits"

        if not self.limits['z'].is_within_limits(position.z_mm):
            return False, f"Z position {position.z_mm} out of limits"

        return True, None
```

### Step 3: Create Data Models (Day 4-5)

#### Image Model
```python
# models/data/image.py
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
import numpy as np
from datetime import datetime
from ..base import BaseModel

@dataclass
class ImageMetadata(BaseModel):
    """Metadata for an acquired image."""
    channel: str
    exposure_ms: float
    laser_power_percent: float
    position: 'Position'
    timestamp: datetime
    pixel_size_um: float
    bit_depth: int
    binning: int = 1

@dataclass
class Image(BaseModel):
    """Single image with data and metadata."""
    data: np.ndarray
    metadata: ImageMetadata

    @property
    def shape(self) -> Tuple[int, int]:
        """Get image dimensions."""
        return self.data.shape[:2]

    @property
    def dtype(self):
        """Get data type."""
        return self.data.dtype

    def get_statistics(self) -> Dict[str, float]:
        """Calculate image statistics."""
        return {
            'min': float(np.min(self.data)),
            'max': float(np.max(self.data)),
            'mean': float(np.mean(self.data)),
            'std': float(np.std(self.data))
        }

@dataclass
class ImageStack(BaseModel):
    """Z-stack or time-lapse image series."""
    images: List[Image]
    stack_type: str  # 'z-stack', 'time-lapse', 'multi-channel'

    @property
    def num_slices(self) -> int:
        return len(self.images)

    def get_projection(self, method: str = 'max') -> np.ndarray:
        """Create projection (max, mean, min)."""
        stack = np.stack([img.data for img in self.images])

        if method == 'max':
            return np.max(stack, axis=0)
        elif method == 'mean':
            return np.mean(stack, axis=0)
        elif method == 'min':
            return np.min(stack, axis=0)
        else:
            raise ValueError(f"Unknown projection method: {method}")
```

#### Workflow Model
```python
# models/data/workflow.py
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum
from ..base import BaseModel

class WorkflowType(Enum):
    SNAPSHOT = "snapshot"
    Z_STACK = "z_stack"
    TIME_LAPSE = "time_lapse"
    MULTI_POSITION = "multi_position"
    TILE_SCAN = "tile_scan"

class WorkflowStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

@dataclass
class WorkflowStep:
    """Single step in a workflow."""
    step_number: int
    action: str  # 'move', 'acquire', 'wait', 'change_laser'
    parameters: Dict[str, Any]
    status: WorkflowStatus = WorkflowStatus.PENDING
    result: Optional[Any] = None
    error: Optional[str] = None

@dataclass
class Workflow(BaseModel):
    """Complete workflow definition and state."""
    name: str
    type: WorkflowType
    steps: List[WorkflowStep]
    status: WorkflowStatus = WorkflowStatus.PENDING
    current_step: int = 0
    total_steps: int = field(init=False)

    def __post_init__(self):
        super().__post_init__()
        self.total_steps = len(self.steps)

    @property
    def progress_percent(self) -> float:
        """Get workflow progress as percentage."""
        if self.total_steps == 0:
            return 0.0
        return (self.current_step / self.total_steps) * 100

    def get_next_step(self) -> Optional[WorkflowStep]:
        """Get next pending step."""
        for step in self.steps[self.current_step:]:
            if step.status == WorkflowStatus.PENDING:
                return step
        return None
```

### Step 4: Create Migration Service (Day 6)

```python
# services/model_migration_service.py
"""Service to migrate existing code to new model structure."""

class ModelMigrationService:
    """Migrate old model usage to new structure."""

    def migrate_position_usage(self):
        """Update all Position class imports and usage."""
        # From: from py2flamingo.models.microscope import Position
        # To:   from py2flamingo.models.hardware.stage import Position
        pass

    def migrate_inline_models(self):
        """Extract inline model definitions to proper files."""
        pass

    def update_imports(self):
        """Update all model imports throughout codebase."""
        pass
```

### Step 5: Update Services to Use New Models (Day 7-8)

Update each service to use the new model structure:
- StageService → uses hardware.stage models
- CameraService → uses hardware.camera models
- ImageAcquisitionService → uses data.image models
- WorkflowService → uses data.workflow models

### Step 6: Testing and Validation (Day 9-10)

Create comprehensive tests for all new models:
```python
# tests/test_models/test_hardware.py
def test_stage_limits_validation():
    limits = StageLimits(min_mm=0, max_mm=10, soft_min_mm=1, soft_max_mm=9)
    assert limits.is_within_limits(5)
    assert not limits.is_within_limits(11)
    assert limits.is_within_soft_limits(5)
    assert not limits.is_within_soft_limits(0.5)

def test_position_arithmetic():
    pos1 = Position(x_mm=1, y_mm=2, z_mm=3, r_degrees=0)
    pos2 = Position(x_mm=1, y_mm=1, z_mm=1, r_degrees=45)
    pos3 = pos1 + pos2
    assert pos3.x_mm == 2
    assert pos3.y_mm == 3
    assert pos3.z_mm == 4
    assert pos3.r_degrees == 45
```

---

# PLAN 2: Remove Duplicate Workflow Management

## Current State Analysis

### Duplicate Workflow Handling
Currently workflow management exists in 5+ places:

1. **WorkflowController** (`controllers/workflow_controller.py`)
   - UI layer workflow control
   - Direct workflow file manipulation
   - Some execution logic mixed in

2. **WorkflowService** (`services/workflow_service.py`)
   - Workflow file I/O
   - Template management
   - Some execution coordination

3. **WorkflowExecutionService** (`services/workflow_execution_service.py`)
   - Actual workflow execution
   - Step sequencing
   - Progress tracking

4. **workflow_parser.py** (`utils/workflow_parser.py`)
   - Text format parsing
   - Workflow validation
   - Format conversion

5. **Direct workflow sending in TCP clients**
   - `tcp_client.py`: `send_workflow()` method
   - `connection_service.py`: workflow methods
   - Raw workflow transmission logic

### Issues
- No single source of truth for workflow state
- Execution logic scattered across multiple services
- File I/O mixed with business logic
- No clear separation of concerns
- Difficult to track workflow progress

## Target Architecture

```
┌─────────────────┐
│ WorkflowController │  ← UI Layer (user interactions)
└────────┬────────┘
         │
┌────────▼────────┐
│ WorkflowFacade  │  ← Single API entry point
└────────┬────────┘
         │
┌────────▼────────────────────┐
│                             │
│   WorkflowOrchestrator      │  ← Core business logic
│                             │
│  ┌────────────────────┐     │
│  │ WorkflowRepository │     │  ← File I/O only
│  └────────────────────┘     │
│                             │
│  ┌────────────────────┐     │
│  │ WorkflowValidator  │     │  ← Validation logic
│  └────────────────────┘     │
│                             │
│  ┌────────────────────┐     │
│  │ WorkflowExecutor   │     │  ← Execution engine
│  └────────────────────┘     │
│                             │
└─────────────────────────────┘
         │
┌────────▼────────┐
│ MicroscopeCommandService │  ← Hardware communication
└─────────────────┘
```

## Implementation Steps

### Step 1: Create WorkflowFacade (Day 1)

```python
# services/workflow/workflow_facade.py
from typing import List, Optional
from py2flamingo.models.data.workflow import Workflow, WorkflowStatus

class WorkflowFacade:
    """
    Single entry point for all workflow operations.
    Coordinates between UI, business logic, and hardware layers.
    """

    def __init__(self):
        self.orchestrator = WorkflowOrchestrator()

    # --- Public API ---

    def list_workflows(self) -> List[Workflow]:
        """Get all available workflows."""
        return self.orchestrator.list_workflows()

    def load_workflow(self, workflow_id: str) -> Workflow:
        """Load a specific workflow."""
        return self.orchestrator.load_workflow(workflow_id)

    def save_workflow(self, workflow: Workflow) -> str:
        """Save workflow and return ID."""
        return self.orchestrator.save_workflow(workflow)

    def validate_workflow(self, workflow: Workflow) -> Tuple[bool, List[str]]:
        """Validate workflow, return (is_valid, errors)."""
        return self.orchestrator.validate_workflow(workflow)

    def start_workflow(self, workflow_id: str) -> str:
        """Start workflow execution, return execution ID."""
        return self.orchestrator.start_workflow(workflow_id)

    def pause_workflow(self, execution_id: str):
        """Pause running workflow."""
        self.orchestrator.pause_workflow(execution_id)

    def resume_workflow(self, execution_id: str):
        """Resume paused workflow."""
        self.orchestrator.resume_workflow(execution_id)

    def stop_workflow(self, execution_id: str):
        """Stop workflow execution."""
        self.orchestrator.stop_workflow(execution_id)

    def get_workflow_status(self, execution_id: str) -> WorkflowStatus:
        """Get current workflow status."""
        return self.orchestrator.get_workflow_status(execution_id)

    def get_workflow_progress(self, execution_id: str) -> float:
        """Get workflow progress percentage."""
        return self.orchestrator.get_workflow_progress(execution_id)
```

### Step 2: Create WorkflowOrchestrator (Day 2-3)

```python
# services/workflow/workflow_orchestrator.py
from typing import Dict, List, Optional
import threading
import uuid

class WorkflowOrchestrator:
    """
    Core workflow business logic.
    Coordinates repository, validator, and executor.
    """

    def __init__(self):
        self.repository = WorkflowRepository()
        self.validator = WorkflowValidator()
        self.executor = WorkflowExecutor()
        self.active_workflows: Dict[str, WorkflowExecution] = {}
        self._lock = threading.Lock()

    def list_workflows(self) -> List[Workflow]:
        """List all workflows from repository."""
        return self.repository.list_all()

    def load_workflow(self, workflow_id: str) -> Workflow:
        """Load workflow from repository."""
        return self.repository.load(workflow_id)

    def save_workflow(self, workflow: Workflow) -> str:
        """Validate and save workflow."""
        is_valid, errors = self.validator.validate(workflow)
        if not is_valid:
            raise ValueError(f"Invalid workflow: {errors}")

        return self.repository.save(workflow)

    def start_workflow(self, workflow_id: str) -> str:
        """Start workflow execution in background thread."""
        workflow = self.repository.load(workflow_id)

        # Validate before execution
        is_valid, errors = self.validator.validate(workflow)
        if not is_valid:
            raise ValueError(f"Cannot start invalid workflow: {errors}")

        # Create execution context
        execution_id = str(uuid.uuid4())
        execution = WorkflowExecution(
            execution_id=execution_id,
            workflow=workflow,
            executor=self.executor
        )

        # Start in background thread
        with self._lock:
            self.active_workflows[execution_id] = execution
            execution.start()

        return execution_id

    def pause_workflow(self, execution_id: str):
        """Pause workflow execution."""
        with self._lock:
            if execution_id in self.active_workflows:
                self.active_workflows[execution_id].pause()

    def get_workflow_status(self, execution_id: str) -> WorkflowStatus:
        """Get workflow execution status."""
        with self._lock:
            if execution_id in self.active_workflows:
                return self.active_workflows[execution_id].status
            return WorkflowStatus.COMPLETED  # Or fetch from history
```

### Step 3: Create WorkflowRepository (Day 4)

```python
# services/workflow/workflow_repository.py
from pathlib import Path
import yaml
import json

class WorkflowRepository:
    """
    Handles all workflow file I/O operations.
    No business logic, just storage.
    """

    def __init__(self, workflows_dir: Path = None):
        if workflows_dir is None:
            workflows_dir = Path("workflows")
        self.workflows_dir = workflows_dir
        self.workflows_dir.mkdir(exist_ok=True)

    def list_all(self) -> List[Workflow]:
        """List all workflows in repository."""
        workflows = []

        # Support multiple formats
        for pattern in ['*.yaml', '*.yml', '*.json', '*.txt']:
            for file_path in self.workflows_dir.glob(pattern):
                try:
                    workflow = self.load_from_file(file_path)
                    workflows.append(workflow)
                except Exception as e:
                    logger.warning(f"Failed to load {file_path}: {e}")

        return workflows

    def load(self, workflow_id: str) -> Workflow:
        """Load specific workflow."""
        file_path = self._find_workflow_file(workflow_id)
        if not file_path:
            raise FileNotFoundError(f"Workflow not found: {workflow_id}")

        return self.load_from_file(file_path)

    def save(self, workflow: Workflow) -> str:
        """Save workflow to file."""
        file_path = self.workflows_dir / f"{workflow.id}.yaml"

        with open(file_path, 'w') as f:
            yaml.dump(workflow.to_dict(), f)

        return workflow.id

    def delete(self, workflow_id: str):
        """Delete workflow file."""
        file_path = self._find_workflow_file(workflow_id)
        if file_path and file_path.exists():
            file_path.unlink()
```

### Step 4: Create WorkflowExecutor (Day 5-6)

```python
# services/workflow/workflow_executor.py
from typing import Optional, Callable
import time

class WorkflowExecutor:
    """
    Executes workflow steps.
    Delegates hardware commands to MicroscopeCommandService.
    """

    def __init__(self, command_service: MicroscopeCommandService):
        self.command_service = command_service
        self.progress_callback: Optional[Callable] = None
        self.error_callback: Optional[Callable] = None

    def execute(self, workflow: Workflow) -> WorkflowResult:
        """Execute complete workflow."""
        result = WorkflowResult(workflow_id=workflow.id)

        try:
            for step_num, step in enumerate(workflow.steps):
                if workflow.status == WorkflowStatus.CANCELLED:
                    break

                if workflow.status == WorkflowStatus.PAUSED:
                    # Wait for resume
                    while workflow.status == WorkflowStatus.PAUSED:
                        time.sleep(0.1)

                # Execute single step
                step_result = self.execute_step(step)
                result.step_results.append(step_result)

                # Update progress
                workflow.current_step = step_num + 1
                if self.progress_callback:
                    self.progress_callback(workflow.progress_percent)

                if not step_result.success:
                    workflow.status = WorkflowStatus.FAILED
                    break

            if workflow.status != WorkflowStatus.FAILED:
                workflow.status = WorkflowStatus.COMPLETED

        except Exception as e:
            workflow.status = WorkflowStatus.FAILED
            if self.error_callback:
                self.error_callback(e)

        return result

    def execute_step(self, step: WorkflowStep) -> StepResult:
        """Execute single workflow step."""
        try:
            if step.action == 'move':
                return self._execute_move(step.parameters)
            elif step.action == 'acquire':
                return self._execute_acquire(step.parameters)
            elif step.action == 'wait':
                return self._execute_wait(step.parameters)
            elif step.action == 'change_laser':
                return self._execute_laser_change(step.parameters)
            else:
                raise ValueError(f"Unknown step action: {step.action}")

        except Exception as e:
            return StepResult(success=False, error=str(e))

    def _execute_move(self, params: Dict) -> StepResult:
        """Execute stage movement."""
        position = Position(**params)
        self.command_service.move_stage(position)
        return StepResult(success=True)

    def _execute_acquire(self, params: Dict) -> StepResult:
        """Execute image acquisition."""
        image = self.command_service.acquire_image(**params)
        return StepResult(success=True, data=image)
```

### Step 5: Migration Path (Day 7-8)

```python
# scripts/migrate_workflows.py
"""Script to migrate existing workflow code to new structure."""

def migrate_workflow_controller():
    """Update WorkflowController to use WorkflowFacade."""
    # Change:
    # self.workflow_service.execute_workflow()
    # To:
    # self.workflow_facade.start_workflow()
    pass

def remove_duplicate_services():
    """Remove WorkflowExecutionService after migration."""
    # 1. Update all references to use WorkflowFacade
    # 2. Remove old service files
    pass

def update_tcp_clients():
    """Remove direct workflow sending from TCP clients."""
    # TCP clients should only send commands
    # Workflow logic should be in WorkflowOrchestrator
    pass
```

---

# PLAN 3: Consolidate Image Processing

## Current State Analysis

### Scattered Image Processing
Image handling is currently distributed across:

1. **utils/image_processing.py** - Basic operations
2. **utils/image_transforms.py** - Transformations
3. **services/image_acquisition_service.py** - Acquisition logic
4. **views/viewer_widget.py** - Display processing
5. **Inline numpy operations** - Throughout codebase

### Issues
- No consistent API for image operations
- Duplicate implementations of common operations
- Display logic mixed with processing logic
- No caching or optimization
- Difficult to add new processing features

## Target Architecture

```
┌──────────────────────┐
│   ImageService       │  ← Single entry point for all image operations
├──────────────────────┤
│ + acquire()          │
│ + process()          │
│ + transform()        │
│ + analyze()          │
│ + display_prepare()  │
│ + save()             │
│ + load()             │
└───────┬──────────────┘
        │
┌───────▼──────────────────────────┐
│         ImagePipeline            │  ← Processing pipeline
├──────────────────────────────────┤
│ ┌──────────────┐                 │
│ │ Acquisition  │ → Preprocessing │
│ └──────────────┘                 │
│        ↓                         │
│ ┌──────────────┐                 │
│ │ Enhancement  │ → Filtering     │
│ └──────────────┘                 │
│        ↓                         │
│ ┌──────────────┐                 │
│ │ Analysis     │ → Measurement   │
│ └──────────────┘                 │
│        ↓                         │
│ ┌──────────────┐                 │
│ │ Display      │ → Rendering     │
│ └──────────────┘                 │
└──────────────────────────────────┘
```

## Implementation Steps

### Step 1: Create Unified ImageService (Day 1-2)

```python
# services/imaging/image_service.py
from typing import Optional, Dict, Any, List, Tuple
import numpy as np
from py2flamingo.models.data.image import Image, ImageMetadata, ImageStack

class ImageService:
    """
    Centralized service for all image operations.
    Provides high-level API for image handling.
    """

    def __init__(self):
        self.acquisition = ImageAcquisition()
        self.processor = ImageProcessor()
        self.transformer = ImageTransformer()
        self.analyzer = ImageAnalyzer()
        self.display_prep = DisplayPreparation()
        self.io_handler = ImageIOHandler()
        self.cache = ImageCache()

    # --- Acquisition ---

    def acquire(self,
                channel: str,
                exposure_ms: float,
                laser_power: float,
                position: Optional[Position] = None) -> Image:
        """Acquire single image."""
        raw_data = self.acquisition.capture(channel, exposure_ms, laser_power)

        # Create metadata
        metadata = ImageMetadata(
            channel=channel,
            exposure_ms=exposure_ms,
            laser_power_percent=laser_power,
            position=position or self.get_current_position(),
            timestamp=datetime.now(),
            pixel_size_um=self.get_pixel_size(),
            bit_depth=16,
            binning=1
        )

        # Create Image object
        image = Image(data=raw_data, metadata=metadata)

        # Cache for quick access
        self.cache.store(image)

        return image

    def acquire_stack(self,
                     z_start: float,
                     z_end: float,
                     z_step: float,
                     **acquire_params) -> ImageStack:
        """Acquire Z-stack."""
        images = []
        z_positions = np.arange(z_start, z_end, z_step)

        for z in z_positions:
            # Move to Z position
            self.move_stage_z(z)

            # Acquire image
            image = self.acquire(**acquire_params)
            images.append(image)

        return ImageStack(images=images, stack_type='z-stack')

    # --- Processing ---

    def process(self,
                image: Image,
                operations: List[str],
                params: Optional[Dict] = None) -> Image:
        """Apply processing pipeline to image."""
        params = params or {}
        processed_data = image.data.copy()

        for operation in operations:
            if operation == 'denoise':
                processed_data = self.processor.denoise(processed_data, **params.get('denoise', {}))
            elif operation == 'deconvolve':
                processed_data = self.processor.deconvolve(processed_data, **params.get('deconvolve', {}))
            elif operation == 'background_subtract':
                processed_data = self.processor.subtract_background(processed_data, **params.get('background', {}))
            elif operation == 'enhance':
                processed_data = self.processor.enhance(processed_data, **params.get('enhance', {}))
            else:
                raise ValueError(f"Unknown operation: {operation}")

        # Create new image with processed data
        return Image(data=processed_data, metadata=image.metadata)

    # --- Transformation ---

    def transform(self,
                  image: Image,
                  transformation: str,
                  **params) -> Image:
        """Apply geometric transformation."""
        if transformation == 'rotate':
            data = self.transformer.rotate(image.data, **params)
        elif transformation == 'flip':
            data = self.transformer.flip(image.data, **params)
        elif transformation == 'resize':
            data = self.transformer.resize(image.data, **params)
        elif transformation == 'crop':
            data = self.transformer.crop(image.data, **params)
        else:
            raise ValueError(f"Unknown transformation: {transformation}")

        return Image(data=data, metadata=image.metadata)

    # --- Analysis ---

    def analyze(self, image: Image) -> Dict[str, Any]:
        """Perform image analysis."""
        return {
            'statistics': self.analyzer.calculate_statistics(image.data),
            'histogram': self.analyzer.calculate_histogram(image.data),
            'focus_metric': self.analyzer.calculate_focus_metric(image.data),
            'snr': self.analyzer.calculate_snr(image.data),
            'objects': self.analyzer.detect_objects(image.data)
        }

    # --- Display Preparation ---

    def prepare_for_display(self,
                           image: Image,
                           colormap: str = 'gray',
                           contrast_mode: str = 'auto',
                           scale_to_8bit: bool = True) -> np.ndarray:
        """Prepare image for display in GUI."""
        display_data = image.data.copy()

        # Apply contrast
        if contrast_mode == 'auto':
            display_data = self.display_prep.auto_contrast(display_data)
        elif contrast_mode == 'manual':
            display_data = self.display_prep.manual_contrast(display_data)

        # Convert to 8-bit for display
        if scale_to_8bit:
            display_data = self.display_prep.to_8bit(display_data)

        # Apply colormap
        if colormap != 'gray':
            display_data = self.display_prep.apply_colormap(display_data, colormap)

        return display_data

    # --- I/O Operations ---

    def save(self,
             image: Image,
             file_path: Path,
             format: str = 'tiff',
             include_metadata: bool = True) -> Path:
        """Save image to file."""
        return self.io_handler.save(image, file_path, format, include_metadata)

    def load(self, file_path: Path) -> Image:
        """Load image from file."""
        return self.io_handler.load(file_path)

    def export_stack(self,
                    stack: ImageStack,
                    output_dir: Path,
                    format: str = 'tiff') -> List[Path]:
        """Export image stack to files."""
        paths = []
        for i, image in enumerate(stack.images):
            file_path = output_dir / f"image_{i:04d}.{format}"
            paths.append(self.save(image, file_path))
        return paths
```

### Step 2: Create ImageProcessor Module (Day 3)

```python
# services/imaging/processors/image_processor.py
import numpy as np
from scipy import ndimage
from skimage import restoration, filters

class ImageProcessor:
    """Core image processing operations."""

    def denoise(self,
                data: np.ndarray,
                method: str = 'gaussian',
                **params) -> np.ndarray:
        """Apply denoising."""
        if method == 'gaussian':
            sigma = params.get('sigma', 1.0)
            return filters.gaussian(data, sigma=sigma)
        elif method == 'median':
            size = params.get('size', 3)
            return filters.median(data, selem=np.ones((size, size)))
        elif method == 'bilateral':
            return restoration.denoise_bilateral(data, **params)
        elif method == 'nlm':
            return restoration.denoise_nl_means(data, **params)
        else:
            raise ValueError(f"Unknown denoise method: {method}")

    def deconvolve(self,
                   data: np.ndarray,
                   psf: Optional[np.ndarray] = None,
                   method: str = 'richardson_lucy',
                   iterations: int = 10) -> np.ndarray:
        """Apply deconvolution."""
        if psf is None:
            # Generate gaussian PSF
            psf = self._generate_psf(data.shape)

        if method == 'richardson_lucy':
            return restoration.richardson_lucy(data, psf, iterations=iterations)
        elif method == 'wiener':
            return restoration.wiener(data, psf)
        else:
            raise ValueError(f"Unknown deconvolution method: {method}")

    def subtract_background(self,
                          data: np.ndarray,
                          method: str = 'rolling_ball',
                          radius: int = 50) -> np.ndarray:
        """Subtract background."""
        if method == 'rolling_ball':
            from skimage.morphology import white_tophat, disk
            return white_tophat(data, disk(radius))
        elif method == 'median':
            background = filters.median(data, disk(radius))
            return np.clip(data - background, 0, None)
        elif method == 'gaussian':
            background = filters.gaussian(data, sigma=radius)
            return np.clip(data - background, 0, None)
        else:
            raise ValueError(f"Unknown background method: {method}")

    def enhance(self,
               data: np.ndarray,
               method: str = 'clahe') -> np.ndarray:
        """Enhance image contrast."""
        from skimage import exposure

        if method == 'clahe':
            return exposure.equalize_adapthist(data)
        elif method == 'histogram':
            return exposure.equalize_hist(data)
        elif method == 'gamma':
            gamma = 1.2  # Default gamma
            return exposure.adjust_gamma(data, gamma)
        else:
            raise ValueError(f"Unknown enhance method: {method}")
```

### Step 3: Create ImageTransformer Module (Day 4)

```python
# services/imaging/processors/image_transformer.py
import numpy as np
from scipy import ndimage
from skimage import transform

class ImageTransformer:
    """Geometric transformations for images."""

    def rotate(self,
              data: np.ndarray,
              angle: float,
              reshape: bool = True,
              order: int = 3) -> np.ndarray:
        """Rotate image by angle degrees."""
        return ndimage.rotate(data, angle, reshape=reshape, order=order)

    def flip(self,
            data: np.ndarray,
            axis: str = 'horizontal') -> np.ndarray:
        """Flip image along axis."""
        if axis == 'horizontal':
            return np.fliplr(data)
        elif axis == 'vertical':
            return np.flipud(data)
        else:
            raise ValueError(f"Unknown flip axis: {axis}")

    def resize(self,
              data: np.ndarray,
              output_shape: Tuple[int, int],
              preserve_range: bool = True) -> np.ndarray:
        """Resize image to output shape."""
        return transform.resize(
            data,
            output_shape,
            preserve_range=preserve_range,
            anti_aliasing=True
        )

    def crop(self,
            data: np.ndarray,
            roi: Tuple[int, int, int, int]) -> np.ndarray:
        """Crop image to ROI (x, y, width, height)."""
        x, y, w, h = roi
        return data[y:y+h, x:x+w]

    def deskew(self,
              data: np.ndarray,
              angle: Optional[float] = None) -> np.ndarray:
        """Deskew image (auto-detect angle if not provided)."""
        if angle is None:
            angle = self._detect_skew_angle(data)

        return self.rotate(data, -angle, reshape=False)

    def register(self,
                fixed: np.ndarray,
                moving: np.ndarray,
                method: str = 'phase_correlation') -> np.ndarray:
        """Register moving image to fixed image."""
        if method == 'phase_correlation':
            from skimage.registration import phase_cross_correlation
            shift, error, phasediff = phase_cross_correlation(fixed, moving)
            return ndimage.shift(moving, shift)
        else:
            raise ValueError(f"Unknown registration method: {method}")
```

### Step 4: Create ImageAnalyzer Module (Day 5)

```python
# services/imaging/processors/image_analyzer.py
import numpy as np
from typing import Dict, List, Tuple
from scipy import ndimage
from skimage import measure, feature

class ImageAnalyzer:
    """Image analysis and measurement operations."""

    def calculate_statistics(self, data: np.ndarray) -> Dict[str, float]:
        """Calculate basic image statistics."""
        return {
            'min': float(np.min(data)),
            'max': float(np.max(data)),
            'mean': float(np.mean(data)),
            'median': float(np.median(data)),
            'std': float(np.std(data)),
            'sum': float(np.sum(data)),
            'percentile_01': float(np.percentile(data, 1)),
            'percentile_99': float(np.percentile(data, 99))
        }

    def calculate_histogram(self,
                          data: np.ndarray,
                          bins: int = 256) -> Tuple[np.ndarray, np.ndarray]:
        """Calculate image histogram."""
        hist, bin_edges = np.histogram(data, bins=bins)
        return hist, bin_edges

    def calculate_focus_metric(self,
                              data: np.ndarray,
                              method: str = 'variance') -> float:
        """Calculate focus quality metric."""
        if method == 'variance':
            return float(np.var(data))
        elif method == 'gradient':
            dy, dx = np.gradient(data)
            return float(np.mean(np.sqrt(dx**2 + dy**2)))
        elif method == 'laplacian':
            laplacian = ndimage.laplace(data)
            return float(np.var(laplacian))
        else:
            raise ValueError(f"Unknown focus method: {method}")

    def calculate_snr(self, data: np.ndarray) -> float:
        """Calculate signal-to-noise ratio."""
        signal = np.mean(data)
        noise = np.std(data)

        if noise == 0:
            return float('inf')

        return float(signal / noise)

    def detect_objects(self,
                      data: np.ndarray,
                      threshold: Optional[float] = None,
                      min_size: int = 10) -> List[Dict]:
        """Detect and measure objects in image."""
        # Threshold image
        if threshold is None:
            from skimage.filters import threshold_otsu
            threshold = threshold_otsu(data)

        binary = data > threshold

        # Label objects
        labeled = measure.label(binary)

        # Measure properties
        objects = []
        for region in measure.regionprops(labeled, intensity_image=data):
            if region.area >= min_size:
                objects.append({
                    'label': region.label,
                    'area': region.area,
                    'centroid': region.centroid,
                    'bbox': region.bbox,
                    'mean_intensity': region.mean_intensity,
                    'max_intensity': region.max_intensity,
                    'eccentricity': region.eccentricity,
                    'solidity': region.solidity
                })

        return objects
```

### Step 5: Create DisplayPreparation Module (Day 6)

```python
# services/imaging/processors/display_preparation.py
import numpy as np
from typing import Tuple

class DisplayPreparation:
    """Prepare images for display in GUI."""

    def auto_contrast(self,
                     data: np.ndarray,
                     percentile_low: float = 0.1,
                     percentile_high: float = 99.9) -> np.ndarray:
        """Apply automatic contrast adjustment."""
        p_low = np.percentile(data, percentile_low)
        p_high = np.percentile(data, percentile_high)

        # Clip and scale
        data_clipped = np.clip(data, p_low, p_high)
        data_scaled = (data_clipped - p_low) / (p_high - p_low)

        return data_scaled

    def manual_contrast(self,
                       data: np.ndarray,
                       min_val: float,
                       max_val: float,
                       gamma: float = 1.0) -> np.ndarray:
        """Apply manual contrast adjustment."""
        # Clip and scale
        data_clipped = np.clip(data, min_val, max_val)
        data_scaled = (data_clipped - min_val) / (max_val - min_val)

        # Apply gamma correction
        if gamma != 1.0:
            data_scaled = np.power(data_scaled, 1.0 / gamma)

        return data_scaled

    def to_8bit(self, data: np.ndarray) -> np.ndarray:
        """Convert to 8-bit for display."""
        # Ensure data is in [0, 1] range
        data_norm = np.clip(data, 0, 1)

        # Convert to 8-bit
        return (data_norm * 255).astype(np.uint8)

    def apply_colormap(self,
                      data: np.ndarray,
                      colormap: str) -> np.ndarray:
        """Apply colormap to grayscale image."""
        import matplotlib.cm as cm

        # Get colormap
        if colormap == 'fire':
            cmap = cm.get_cmap('hot')
        elif colormap == 'ice':
            cmap = cm.get_cmap('cool')
        elif colormap == 'rainbow':
            cmap = cm.get_cmap('hsv')
        else:
            cmap = cm.get_cmap(colormap)

        # Apply colormap
        return cmap(data)

    def create_composite(self,
                        channels: List[np.ndarray],
                        colors: List[str]) -> np.ndarray:
        """Create RGB composite from multiple channels."""
        # Initialize RGB image
        h, w = channels[0].shape
        composite = np.zeros((h, w, 3), dtype=np.float32)

        # Map colors to RGB
        color_map = {
            'red': [1, 0, 0],
            'green': [0, 1, 0],
            'blue': [0, 0, 1],
            'cyan': [0, 1, 1],
            'magenta': [1, 0, 1],
            'yellow': [1, 1, 0]
        }

        # Add each channel
        for channel, color in zip(channels, colors):
            rgb = color_map.get(color, [1, 1, 1])
            for i in range(3):
                composite[:, :, i] += channel * rgb[i]

        # Normalize
        composite = np.clip(composite, 0, 1)

        return composite
```

### Step 6: Create ImageCache (Day 7)

```python
# services/imaging/image_cache.py
from typing import Optional, Dict
import weakref
from collections import OrderedDict

class ImageCache:
    """LRU cache for processed images."""

    def __init__(self, max_size_mb: int = 500):
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self.cache: OrderedDict[str, weakref.ref] = OrderedDict()
        self.size_map: Dict[str, int] = {}
        self.current_size = 0

    def store(self, image: Image, key: Optional[str] = None):
        """Store image in cache."""
        if key is None:
            key = image.id

        # Calculate size
        size = image.data.nbytes

        # Evict if necessary
        while self.current_size + size > self.max_size_bytes and self.cache:
            self._evict_oldest()

        # Store weak reference
        self.cache[key] = weakref.ref(image)
        self.size_map[key] = size
        self.current_size += size

        # Move to end (most recently used)
        self.cache.move_to_end(key)

    def get(self, key: str) -> Optional[Image]:
        """Get image from cache."""
        if key in self.cache:
            ref = self.cache[key]
            image = ref()

            if image is not None:
                # Move to end (most recently used)
                self.cache.move_to_end(key)
                return image
            else:
                # Reference died, clean up
                del self.cache[key]
                self.current_size -= self.size_map[key]
                del self.size_map[key]

        return None

    def _evict_oldest(self):
        """Evict least recently used item."""
        if self.cache:
            key = next(iter(self.cache))
            del self.cache[key]
            self.current_size -= self.size_map[key]
            del self.size_map[key]
```

### Step 7: Migration Strategy (Day 8-9)

```python
# scripts/migrate_image_processing.py
"""Migrate scattered image processing to unified service."""

def update_imports():
    """Update all image processing imports."""
    # Old: from utils.image_processing import denoise
    # New: from services.imaging.image_service import ImageService
    pass

def consolidate_numpy_operations():
    """Find and consolidate inline numpy operations."""
    # Search for np.mean(image), np.std(image), etc.
    # Replace with image_service.analyze(image)
    pass

def update_viewer_widget():
    """Update viewer to use ImageService for display prep."""
    # Old: self._prepare_display(image)
    # New: self.image_service.prepare_for_display(image)
    pass
```

---

## Timeline Summary

### Week 1: Model Consolidation
- Days 1-3: Create base models and hardware models
- Days 4-5: Create data models
- Day 6: Create migration service
- Days 7-8: Update services
- Days 9-10: Testing

### Week 2: Workflow Consolidation
- Days 1-3: Create facade and orchestrator
- Day 4: Create repository
- Days 5-6: Create executor
- Days 7-8: Migration

### Week 3: Image Processing Consolidation
- Days 1-2: Create ImageService
- Day 3: Create processor
- Day 4: Create transformer
- Day 5: Create analyzer
- Day 6: Create display prep
- Day 7: Create cache
- Days 8-9: Migration

## Success Criteria

1. **Models**: All domain concepts have dedicated model classes
2. **Workflows**: Single entry point for all workflow operations
3. **Images**: Unified API for all image operations
4. **Testing**: Comprehensive test coverage for new modules
5. **Documentation**: Clear migration guides and API docs
6. **Performance**: No regression in processing speed

## Risk Mitigation

1. **Create feature flags** to switch between old/new implementations
2. **Maintain backward compatibility** during transition
3. **Extensive testing** before removing old code
4. **Gradual rollout** - one module at a time
5. **Keep backups** of old implementations until stable