"""
Error formatting and logging utilities for Flamingo Control.

Provides consistent error formatting across the application for both
user display and technical logging.
"""

import logging
import json
from typing import Optional, Dict, Any, Union
from datetime import datetime
from pathlib import Path

from py2flamingo.core.errors import FlamingoError


class ErrorFormatter:
    """
    Formats errors for consistent presentation.

    Handles both FlamingoError instances and standard Python exceptions.
    """

    # ANSI color codes for terminal output (optional)
    COLORS = {
        'RED': '\033[91m',
        'YELLOW': '\033[93m',
        'BLUE': '\033[94m',
        'GREEN': '\033[92m',
        'RESET': '\033[0m'
    }

    def __init__(self, use_colors: bool = False):
        """
        Initialize formatter.

        Args:
            use_colors: Whether to use ANSI colors in terminal output
        """
        self.use_colors = use_colors

    def format_for_user(self, error: Exception) -> str:
        """
        Format error for end-user display.

        Shows helpful message without technical details.

        Args:
            error: The error to format

        Returns:
            User-friendly error message
        """
        if isinstance(error, FlamingoError):
            return error.format_user_message()
        else:
            # For non-Flamingo errors, provide a generic message
            return f"An error occurred: {str(error)}"

    def format_for_log(self, error: Exception, include_trace: bool = True) -> str:
        """
        Format error for technical logging.

        Includes all available context and technical details.

        Args:
            error: The error to format
            include_trace: Whether to include stack trace

        Returns:
            Detailed error information for logging
        """
        if isinstance(error, FlamingoError):
            return error.format_log_message()
        else:
            # Format standard exception
            import traceback
            msg = f"{error.__class__.__name__}: {str(error)}"
            if include_trace:
                msg += f"\nStack trace:\n{traceback.format_exc()}"
            return msg

    def format_for_gui(self, error: Exception) -> Dict[str, Any]:
        """
        Format error for GUI display.

        Returns structured data suitable for dialog boxes or status bars.

        Args:
            error: The error to format

        Returns:
            Dictionary with title, message, details, and suggestions
        """
        if isinstance(error, FlamingoError):
            return {
                'title': error.__class__.__name__.replace('Error', ' Error'),
                'message': error.message,
                'code': error.error_code,
                'suggestions': error.suggestions,
                'details': error.context if error.context else None,
                'severity': self._get_severity(error.error_code)
            }
        else:
            return {
                'title': 'Error',
                'message': str(error),
                'code': 9000,
                'suggestions': [],
                'details': {'type': error.__class__.__name__},
                'severity': 'error'
            }

    def format_for_json(self, error: Exception) -> str:
        """
        Format error as JSON for API responses or structured logging.

        Args:
            error: The error to format

        Returns:
            JSON string representation
        """
        if isinstance(error, FlamingoError):
            data = error.to_dict()
        else:
            data = {
                'error_type': error.__class__.__name__,
                'message': str(error),
                'timestamp': datetime.now().isoformat()
            }
        return json.dumps(data, indent=2, default=str)

    def _get_severity(self, error_code: int) -> str:
        """
        Determine error severity from error code.

        Args:
            error_code: Numeric error code

        Returns:
            Severity level: 'critical', 'error', 'warning', or 'info'
        """
        if error_code < 2000:  # Connection errors
            return 'critical'
        elif error_code < 4000:  # Command/Hardware errors
            return 'error'
        elif error_code < 7000:  # Data/Workflow/Config errors
            return 'warning'
        else:  # Validation/Timeout/System errors
            return 'error'

    def colorize(self, text: str, color: str) -> str:
        """
        Add ANSI color codes to text if colors are enabled.

        Args:
            text: Text to colorize
            color: Color name ('RED', 'YELLOW', 'BLUE', 'GREEN')

        Returns:
            Colorized text or original text if colors disabled
        """
        if self.use_colors and color in self.COLORS:
            return f"{self.COLORS[color]}{text}{self.COLORS['RESET']}"
        return text


class ErrorLogger:
    """
    Centralized error logging with consistent formatting.

    Logs errors to both file and console with appropriate detail levels.
    """

    def __init__(
        self,
        logger_name: str = 'flamingo.errors',
        log_file: Optional[Path] = None,
        console_level: int = logging.WARNING,
        file_level: int = logging.DEBUG
    ):
        """
        Initialize error logger.

        Args:
            logger_name: Name for the logger instance
            log_file: Optional path to error log file
            console_level: Logging level for console output
            file_level: Logging level for file output
        """
        self.logger = logging.getLogger(logger_name)
        self.logger.setLevel(logging.DEBUG)
        self.formatter = ErrorFormatter()

        # Console handler - user-friendly messages
        console_handler = logging.StreamHandler()
        console_handler.setLevel(console_level)
        console_formatter = logging.Formatter(
            '%(levelname)s - %(message)s'
        )
        console_handler.setFormatter(console_formatter)
        self.logger.addHandler(console_handler)

        # File handler - detailed technical logs
        if log_file:
            file_handler = logging.FileHandler(log_file)
            file_handler.setLevel(file_level)
            file_formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            file_handler.setFormatter(file_formatter)
            self.logger.addHandler(file_handler)

    def log_error(
        self,
        error: Exception,
        level: int = logging.ERROR,
        include_trace: bool = True,
        extra_context: Optional[Dict[str, Any]] = None
    ):
        """
        Log an error with appropriate formatting.

        Args:
            error: The error to log
            level: Logging level
            include_trace: Whether to include stack trace
            extra_context: Additional context to include
        """
        # Build complete context
        context = {}
        if isinstance(error, FlamingoError) and error.context:
            context.update(error.context)
        if extra_context:
            context.update(extra_context)

        # Format message based on error type
        if isinstance(error, FlamingoError):
            # Log user-friendly message at specified level
            self.logger.log(level, self.formatter.format_for_user(error))

            # Log technical details at debug level
            self.logger.debug(self.formatter.format_for_log(error, include_trace))

            # Log context if available
            if context:
                self.logger.debug(f"Error context: {json.dumps(context, indent=2, default=str)}")
        else:
            # For non-Flamingo errors, log full details
            self.logger.log(
                level,
                self.formatter.format_for_log(error, include_trace),
                extra={'context': context} if context else None
            )

    def log_and_raise(
        self,
        error: FlamingoError,
        level: int = logging.ERROR
    ):
        """
        Log an error and then raise it.

        Convenience method for common pattern.

        Args:
            error: The error to log and raise
            level: Logging level

        Raises:
            The provided error after logging
        """
        self.log_error(error, level)
        raise error


# Global error logger instance
_error_logger: Optional[ErrorLogger] = None


def get_error_logger() -> ErrorLogger:
    """
    Get the global error logger instance.

    Creates one if it doesn't exist.

    Returns:
        The global ErrorLogger instance
    """
    global _error_logger
    if _error_logger is None:
        # Default to logging errors to a file in the logs directory
        from pathlib import Path
        log_dir = Path.home() / 'LSControl' / 'Flamingo_Control' / 'logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / 'errors.log'
        _error_logger = ErrorLogger(log_file=log_file)
    return _error_logger


def log_error(error: Exception, **kwargs):
    """
    Convenience function to log an error using the global logger.

    Args:
        error: The error to log
        **kwargs: Additional arguments passed to ErrorLogger.log_error
    """
    get_error_logger().log_error(error, **kwargs)


def format_error(error: Exception, format_type: str = 'user') -> Union[str, Dict]:
    """
    Convenience function to format an error.

    Args:
        error: The error to format
        format_type: One of 'user', 'log', 'gui', or 'json'

    Returns:
        Formatted error based on type
    """
    formatter = ErrorFormatter()

    if format_type == 'user':
        return formatter.format_for_user(error)
    elif format_type == 'log':
        return formatter.format_for_log(error)
    elif format_type == 'gui':
        return formatter.format_for_gui(error)
    elif format_type == 'json':
        return formatter.format_for_json(error)
    else:
        raise ValueError(f"Unknown format type: {format_type}")