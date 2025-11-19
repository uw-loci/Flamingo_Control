"""Base model classes for Flamingo Control domain models.

This module provides foundational classes for all domain models in the application,
including base functionality for serialization, validation, and metadata tracking.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any, TypeVar, Type
from datetime import datetime
import uuid
import json
from abc import ABC, abstractmethod


T = TypeVar('T', bound='BaseModel')


@dataclass
class BaseModel:
    """Base class for all domain models.

    Provides common functionality:
    - Unique ID generation
    - Timestamp tracking
    - Metadata storage
    - Serialization/deserialization
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def update(self) -> None:
        """Mark model as updated with current timestamp."""
        self.updated_at = datetime.now()

    def to_dict(self) -> Dict[str, Any]:
        """Convert model to dictionary for serialization.

        Returns:
            Dictionary representation of the model
        """
        result = asdict(self)

        # Convert datetime objects to ISO format strings
        if result.get('created_at'):
            result['created_at'] = result['created_at'].isoformat()
        if result.get('updated_at'):
            result['updated_at'] = result['updated_at'].isoformat()

        return result

    @classmethod
    def from_dict(cls: Type[T], data: Dict[str, Any]) -> T:
        """Create model instance from dictionary.

        Args:
            data: Dictionary containing model data

        Returns:
            New instance of the model
        """
        # Create a copy to avoid modifying original
        data = dict(data)

        # Convert ISO format strings back to datetime
        if 'created_at' in data and isinstance(data['created_at'], str):
            data['created_at'] = datetime.fromisoformat(data['created_at'])
        if 'updated_at' in data and isinstance(data['updated_at'], str):
            data['updated_at'] = datetime.fromisoformat(data['updated_at'])

        # Remove any extra fields not in the model
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered_data = {k: v for k, v in data.items() if k in valid_fields}

        return cls(**filtered_data)

    def to_json(self) -> str:
        """Convert model to JSON string.

        Returns:
            JSON string representation
        """
        return json.dumps(self.to_dict(), default=str)

    @classmethod
    def from_json(cls: Type[T], json_str: str) -> T:
        """Create model instance from JSON string.

        Args:
            json_str: JSON string containing model data

        Returns:
            New instance of the model
        """
        data = json.loads(json_str)
        return cls.from_dict(data)

    def copy(self: T, **kwargs) -> T:
        """Create a copy of the model with optional field updates.

        Args:
            **kwargs: Fields to update in the copy

        Returns:
            New instance with updated fields
        """
        data = self.to_dict()
        data.update(kwargs)
        return self.__class__.from_dict(data)


@dataclass
class ValidatedModel(BaseModel):
    """Base class for models requiring validation.

    Automatically validates after initialization and updates.
    """

    def __post_init__(self):
        """Validate model after initialization."""
        super().__post_init__() if hasattr(super(), '__post_init__') else None
        self.validate()

    def update(self) -> None:
        """Mark model as updated and revalidate."""
        super().update()
        self.validate()

    @abstractmethod
    def validate(self) -> None:
        """Validate model state.

        Should raise ValueError or ValidationError if validation fails.
        Subclasses must implement this method.
        """
        pass


@dataclass
class ImmutableModel(BaseModel):
    """Base class for immutable models.

    Once created, fields cannot be modified directly.
    Use copy() method to create modified versions.
    """

    def __setattr__(self, name: str, value: Any) -> None:
        """Prevent modification after initialization."""
        if hasattr(self, '_initialized') and self._initialized:
            raise AttributeError(f"Cannot modify immutable model field '{name}'")
        super().__setattr__(name, value)

    def __post_init__(self):
        """Mark model as initialized to prevent further modifications."""
        super().__post_init__() if hasattr(super(), '__post_init__') else None
        object.__setattr__(self, '_initialized', True)

    def update(self) -> None:
        """Immutable models cannot be updated."""
        raise NotImplementedError("Immutable models cannot be updated. Use copy() instead.")


class ValidationError(Exception):
    """Custom exception for model validation failures."""

    def __init__(self, message: str, field: Optional[str] = None, value: Any = None):
        """Initialize validation error.

        Args:
            message: Error message
            field: Field that failed validation
            value: Invalid value that caused the error
        """
        super().__init__(message)
        self.field = field
        self.value = value
        self.message = message

    def __str__(self) -> str:
        """String representation of validation error."""
        if self.field:
            return f"Validation failed for field '{self.field}': {self.message}"
        return f"Validation failed: {self.message}"


def validate_range(value: float, min_val: Optional[float] = None,
                  max_val: Optional[float] = None, field_name: str = "value") -> None:
    """Utility function to validate numeric ranges.

    Args:
        value: Value to validate
        min_val: Minimum allowed value (inclusive)
        max_val: Maximum allowed value (inclusive)
        field_name: Name of field for error messages

    Raises:
        ValidationError: If value is outside the specified range
    """
    if min_val is not None and value < min_val:
        raise ValidationError(
            f"Value {value} is below minimum {min_val}",
            field=field_name,
            value=value
        )

    if max_val is not None and value > max_val:
        raise ValidationError(
            f"Value {value} is above maximum {max_val}",
            field=field_name,
            value=value
        )


def validate_not_empty(value: Any, field_name: str = "value") -> None:
    """Utility function to validate non-empty values.

    Args:
        value: Value to validate
        field_name: Name of field for error messages

    Raises:
        ValidationError: If value is None or empty
    """
    if value is None:
        raise ValidationError(f"Value cannot be None", field=field_name, value=value)

    if hasattr(value, '__len__') and len(value) == 0:
        raise ValidationError(f"Value cannot be empty", field=field_name, value=value)