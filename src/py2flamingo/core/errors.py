"""
Unified error handling framework for Flamingo Control.

This module defines the standard error hierarchy and provides consistent
error handling across the application.

Error Code Ranges:
- 1000-1999: Connection errors
- 2000-2999: Command errors
- 3000-3999: Hardware errors
- 4000-4999: Data/File errors
- 5000-5999: State/Workflow errors
- 6000-6999: Configuration errors
- 7000-7999: Validation errors
- 8000-8999: Timeout errors
- 9000-9999: Unknown/System errors
"""

from typing import Optional, Dict, Any, List
from datetime import datetime
import traceback
import logging


class FlamingoError(Exception):
    """
    Base exception for all Flamingo-specific errors.

    Provides structured error information with context tracking.
    """

    # Base error code for unknown errors
    DEFAULT_CODE = 9000

    def __init__(
        self,
        message: str,
        error_code: Optional[int] = None,
        context: Optional[Dict[str, Any]] = None,
        cause: Optional[Exception] = None,
        suggestions: Optional[List[str]] = None
    ):
        """
        Initialize a Flamingo error.

        Args:
            message: Human-readable error description
            error_code: Numeric error code for categorization
            context: Additional context information (WHERE)
            cause: Original exception if this wraps another error
            suggestions: List of possible solutions or next steps
        """
        super().__init__(message)
        self.message = message
        self.error_code = error_code or self.DEFAULT_CODE
        self.context = context or {}
        self.cause = cause
        self.suggestions = suggestions or []
        self.timestamp = datetime.now()

        # Capture stack trace
        self.stack_trace = traceback.format_exc() if cause else None

        # Add standard context
        if cause:
            self.context['original_error'] = str(cause)
            self.context['original_type'] = type(cause).__name__

    def to_dict(self) -> Dict[str, Any]:
        """Convert error to dictionary for logging/serialization."""
        return {
            'error_type': self.__class__.__name__,
            'message': self.message,
            'code': self.error_code,
            'context': self.context,
            'suggestions': self.suggestions,
            'timestamp': self.timestamp.isoformat(),
            'cause': str(self.cause) if self.cause else None,
            'stack_trace': self.stack_trace
        }

    def format_user_message(self) -> str:
        """Format error for user display (without technical details)."""
        msg = f"{self.message}"
        if self.suggestions:
            msg += "\n\nSuggestions:"
            for i, suggestion in enumerate(self.suggestions, 1):
                msg += f"\n  {i}. {suggestion}"
        return msg

    def format_log_message(self) -> str:
        """Format error for logging (with all details)."""
        parts = [
            f"[{self.error_code}] {self.__class__.__name__}: {self.message}"
        ]

        if self.context:
            parts.append(f"Context: {self.context}")

        if self.cause:
            parts.append(f"Caused by: {self.cause}")

        if self.stack_trace:
            parts.append(f"Stack trace:\n{self.stack_trace}")

        return " | ".join(parts)


class ConnectionError(FlamingoError):
    """Errors related to network connections and socket operations."""
    DEFAULT_CODE = 1001

    def __init__(self, message: str, **kwargs):
        if 'context' not in kwargs:
            kwargs['context'] = {}
        kwargs['context']['category'] = 'CONNECTION'
        super().__init__(message, **kwargs)


class CommandError(FlamingoError):
    """Errors related to command execution and protocol violations."""
    DEFAULT_CODE = 2001

    def __init__(self, message: str, command_code: Optional[int] = None, **kwargs):
        if 'context' not in kwargs:
            kwargs['context'] = {}
        kwargs['context']['category'] = 'COMMAND'
        if command_code is not None:
            kwargs['context']['command_code'] = command_code
        super().__init__(message, **kwargs)


class HardwareError(FlamingoError):
    """Errors related to hardware state and physical limitations."""
    DEFAULT_CODE = 3001

    def __init__(self, message: str, component: Optional[str] = None, **kwargs):
        if 'context' not in kwargs:
            kwargs['context'] = {}
        kwargs['context']['category'] = 'HARDWARE'
        if component:
            kwargs['context']['component'] = component
        super().__init__(message, **kwargs)


class DataError(FlamingoError):
    """Errors related to data formats, file I/O, and parsing."""
    DEFAULT_CODE = 4001

    def __init__(self, message: str, file_path: Optional[str] = None, **kwargs):
        if 'context' not in kwargs:
            kwargs['context'] = {}
        kwargs['context']['category'] = 'DATA'
        if file_path:
            kwargs['context']['file_path'] = file_path
        super().__init__(message, **kwargs)


class WorkflowError(FlamingoError):
    """Errors related to workflow execution and state management."""
    DEFAULT_CODE = 5001

    def __init__(self, message: str, workflow_name: Optional[str] = None, **kwargs):
        if 'context' not in kwargs:
            kwargs['context'] = {}
        kwargs['context']['category'] = 'WORKFLOW'
        if workflow_name:
            kwargs['context']['workflow'] = workflow_name
        super().__init__(message, **kwargs)


class ConfigurationError(FlamingoError):
    """Errors related to application configuration and settings."""
    DEFAULT_CODE = 6001

    def __init__(self, message: str, setting_name: Optional[str] = None, **kwargs):
        if 'context' not in kwargs:
            kwargs['context'] = {}
        kwargs['context']['category'] = 'CONFIGURATION'
        if setting_name:
            kwargs['context']['setting'] = setting_name
        super().__init__(message, **kwargs)


class ValidationError(FlamingoError):
    """Errors related to input validation and parameter checking."""
    DEFAULT_CODE = 7001

    def __init__(self, message: str, field_name: Optional[str] = None, **kwargs):
        if 'context' not in kwargs:
            kwargs['context'] = {}
        kwargs['context']['category'] = 'VALIDATION'
        if field_name:
            kwargs['context']['field'] = field_name
        super().__init__(message, **kwargs)


class TimeoutError(FlamingoError):
    """Errors related to operation timeouts."""
    DEFAULT_CODE = 8001

    def __init__(self, message: str, timeout_seconds: Optional[float] = None, **kwargs):
        if 'context' not in kwargs:
            kwargs['context'] = {}
        kwargs['context']['category'] = 'TIMEOUT'
        if timeout_seconds is not None:
            kwargs['context']['timeout_seconds'] = timeout_seconds
        super().__init__(message, **kwargs)


class SystemError(FlamingoError):
    """Errors related to system-level failures and unknown conditions."""
    DEFAULT_CODE = 9001

    def __init__(self, message: str, **kwargs):
        if 'context' not in kwargs:
            kwargs['context'] = {}
        kwargs['context']['category'] = 'SYSTEM'
        super().__init__(message, **kwargs)


# Error code constants for common scenarios
class ErrorCodes:
    """Standard error codes for common error scenarios."""

    # Connection errors (1000-1999)
    CONNECTION_REFUSED = 1001
    CONNECTION_TIMEOUT = 1002
    CONNECTION_LOST = 1003
    SOCKET_ERROR = 1004
    PORT_IN_USE = 1005

    # Command errors (2000-2999)
    INVALID_COMMAND = 2001
    COMMAND_FAILED = 2002
    PROTOCOL_ERROR = 2003
    ENCODING_ERROR = 2004
    RESPONSE_PARSE_ERROR = 2005

    # Hardware errors (3000-3999)
    STAGE_LIMIT = 3001
    LASER_ERROR = 3002
    CAMERA_ERROR = 3003
    FOCUS_ERROR = 3004
    TEMPERATURE_ERROR = 3005

    # Data errors (4000-4999)
    FILE_NOT_FOUND = 4001
    FILE_READ_ERROR = 4002
    FILE_WRITE_ERROR = 4003
    PARSE_ERROR = 4004
    INVALID_FORMAT = 4005

    # Workflow errors (5000-5999)
    WORKFLOW_NOT_FOUND = 5001
    WORKFLOW_INVALID = 5002
    WORKFLOW_FAILED = 5003
    STATE_ERROR = 5004
    SEQUENCE_ERROR = 5005

    # Configuration errors (6000-6999)
    CONFIG_NOT_FOUND = 6001
    CONFIG_INVALID = 6002
    CONFIG_SAVE_ERROR = 6003
    MISSING_SETTING = 6004

    # Validation errors (7000-7999)
    INVALID_PARAMETER = 7001
    OUT_OF_RANGE = 7002
    MISSING_REQUIRED = 7003
    TYPE_ERROR = 7004

    # Timeout errors (8000-8999)
    OPERATION_TIMEOUT = 8001
    RESPONSE_TIMEOUT = 8002
    CONNECTION_TIMEOUT = 8003

    # System errors (9000-9999)
    UNKNOWN_ERROR = 9000
    INTERNAL_ERROR = 9001
    NOT_IMPLEMENTED = 9002
    DEPRECATED = 9003


def wrap_external_error(e: Exception, message: str, error_class=SystemError, **context) -> FlamingoError:
    """
    Wrap an external exception in a FlamingoError.

    Args:
        e: The original exception
        message: Context-specific error message
        error_class: The FlamingoError subclass to use
        **context: Additional context information

    Returns:
        A FlamingoError instance wrapping the original exception
    """
    return error_class(
        message=message,
        cause=e,
        context=context
    )