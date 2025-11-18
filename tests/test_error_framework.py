"""
Tests for the unified error handling framework.

Verifies that the new error classes and formatting utilities work correctly.
"""

import unittest
import json
import logging
from io import StringIO
from unittest.mock import patch, MagicMock

from py2flamingo.core.errors import (
    FlamingoError,
    ConnectionError,
    CommandError,
    HardwareError,
    DataError,
    WorkflowError,
    ConfigurationError,
    ValidationError,
    TimeoutError,
    SystemError,
    ErrorCodes,
    wrap_external_error
)

from py2flamingo.core.error_formatting import (
    ErrorFormatter,
    ErrorLogger,
    format_error,
    log_error
)


class TestFlamingoError(unittest.TestCase):
    """Test the base FlamingoError class."""

    def test_basic_error_creation(self):
        """Test creating a basic error."""
        error = FlamingoError(
            message="Test error",
            error_code=1001,
            context={'location': 'test'},
            suggestions=["Try again", "Check settings"]
        )

        self.assertEqual(error.message, "Test error")
        self.assertEqual(error.error_code, 1001)
        self.assertEqual(error.context['location'], 'test')
        self.assertEqual(len(error.suggestions), 2)
        self.assertIsNotNone(error.timestamp)

    def test_error_with_cause(self):
        """Test wrapping another exception."""
        original = ValueError("Original error")
        error = FlamingoError(
            message="Wrapped error",
            cause=original
        )

        self.assertIs(error.cause, original)
        self.assertEqual(error.context['original_error'], "Original error")
        self.assertEqual(error.context['original_type'], "ValueError")
        self.assertIsNotNone(error.stack_trace)

    def test_to_dict(self):
        """Test converting error to dictionary."""
        error = FlamingoError(
            message="Test error",
            error_code=1001,
            context={'test': True}
        )

        error_dict = error.to_dict()
        self.assertEqual(error_dict['message'], "Test error")
        self.assertEqual(error_dict['code'], 1001)
        self.assertEqual(error_dict['context']['test'], True)
        self.assertIn('timestamp', error_dict)

    def test_format_user_message(self):
        """Test formatting for user display."""
        error = FlamingoError(
            message="Connection failed",
            suggestions=["Check network", "Verify IP"]
        )

        user_msg = error.format_user_message()
        self.assertIn("Connection failed", user_msg)
        self.assertIn("Check network", user_msg)
        self.assertIn("Verify IP", user_msg)

    def test_format_log_message(self):
        """Test formatting for logs."""
        error = FlamingoError(
            message="Test error",
            error_code=1001,
            context={'location': 'test'}
        )

        log_msg = error.format_log_message()
        self.assertIn("[1001]", log_msg)
        self.assertIn("FlamingoError", log_msg)
        self.assertIn("Test error", log_msg)
        self.assertIn("Context:", log_msg)


class TestErrorSubclasses(unittest.TestCase):
    """Test specific error subclasses."""

    def test_connection_error(self):
        """Test ConnectionError specifics."""
        error = ConnectionError(
            "Connection failed",
            error_code=ErrorCodes.CONNECTION_REFUSED,
            context={'ip': '127.0.0.1'}
        )

        self.assertEqual(error.context['category'], 'CONNECTION')
        self.assertEqual(error.error_code, ErrorCodes.CONNECTION_REFUSED)

    def test_command_error(self):
        """Test CommandError with command code."""
        error = CommandError(
            "Command failed",
            command_code=12345,
            error_code=ErrorCodes.COMMAND_FAILED
        )

        self.assertEqual(error.context['category'], 'COMMAND')
        self.assertEqual(error.context['command_code'], 12345)

    def test_hardware_error(self):
        """Test HardwareError with component."""
        error = HardwareError(
            "Stage limit reached",
            component='stage',
            error_code=ErrorCodes.STAGE_LIMIT
        )

        self.assertEqual(error.context['category'], 'HARDWARE')
        self.assertEqual(error.context['component'], 'stage')

    def test_data_error(self):
        """Test DataError with file path."""
        error = DataError(
            "File not found",
            file_path="/path/to/file.txt",
            error_code=ErrorCodes.FILE_NOT_FOUND
        )

        self.assertEqual(error.context['category'], 'DATA')
        self.assertEqual(error.context['file_path'], "/path/to/file.txt")

    def test_workflow_error(self):
        """Test WorkflowError with workflow name."""
        error = WorkflowError(
            "Workflow failed",
            workflow_name="test_workflow",
            error_code=ErrorCodes.WORKFLOW_FAILED
        )

        self.assertEqual(error.context['category'], 'WORKFLOW')
        self.assertEqual(error.context['workflow'], "test_workflow")

    def test_validation_error(self):
        """Test ValidationError with field name."""
        error = ValidationError(
            "Invalid input",
            field_name="laser_power",
            error_code=ErrorCodes.OUT_OF_RANGE
        )

        self.assertEqual(error.context['category'], 'VALIDATION')
        self.assertEqual(error.context['field'], "laser_power")

    def test_timeout_error(self):
        """Test TimeoutError with timeout duration."""
        error = TimeoutError(
            "Operation timed out",
            timeout_seconds=5.0,
            error_code=ErrorCodes.OPERATION_TIMEOUT
        )

        self.assertEqual(error.context['category'], 'TIMEOUT')
        self.assertEqual(error.context['timeout_seconds'], 5.0)


class TestWrapExternalError(unittest.TestCase):
    """Test wrapping external exceptions."""

    def test_wrap_external_error(self):
        """Test wrapping a standard Python exception."""
        original = ValueError("Bad value")
        wrapped = wrap_external_error(
            original,
            "Value processing failed",
            ValidationError,
            field='test_field'
        )

        self.assertIsInstance(wrapped, ValidationError)
        self.assertEqual(wrapped.message, "Value processing failed")
        self.assertIs(wrapped.cause, original)
        self.assertEqual(wrapped.context['field'], 'test_field')


class TestErrorFormatter(unittest.TestCase):
    """Test error formatting utilities."""

    def setUp(self):
        self.formatter = ErrorFormatter()

    def test_format_for_user_flamingo_error(self):
        """Test formatting FlamingoError for users."""
        error = ConnectionError(
            "Connection failed",
            suggestions=["Check network"]
        )

        user_msg = self.formatter.format_for_user(error)
        self.assertIn("Connection failed", user_msg)
        self.assertIn("Check network", user_msg)

    def test_format_for_user_standard_error(self):
        """Test formatting standard exception for users."""
        error = ValueError("Test error")
        user_msg = self.formatter.format_for_user(error)
        self.assertEqual(user_msg, "An error occurred: Test error")

    def test_format_for_gui(self):
        """Test formatting for GUI display."""
        error = HardwareError(
            "Stage error",
            component='stage',
            error_code=ErrorCodes.STAGE_LIMIT,
            suggestions=["Home stage"]
        )

        gui_data = self.formatter.format_for_gui(error)
        self.assertEqual(gui_data['title'], 'Hardware Error')
        self.assertEqual(gui_data['message'], 'Stage error')
        self.assertEqual(gui_data['code'], ErrorCodes.STAGE_LIMIT)
        self.assertEqual(gui_data['suggestions'], ["Home stage"])
        self.assertEqual(gui_data['severity'], 'error')

    def test_format_for_json(self):
        """Test JSON formatting."""
        error = CommandError(
            "Command failed",
            command_code=12345
        )

        json_str = self.formatter.format_for_json(error)
        data = json.loads(json_str)

        self.assertEqual(data['error_type'], 'CommandError')
        self.assertEqual(data['message'], 'Command failed')
        self.assertIn('timestamp', data)

    def test_severity_determination(self):
        """Test severity level determination."""
        formatter = ErrorFormatter()

        # Connection errors are critical
        self.assertEqual(formatter._get_severity(1500), 'critical')

        # Command/Hardware errors are errors
        self.assertEqual(formatter._get_severity(2500), 'error')
        self.assertEqual(formatter._get_severity(3500), 'error')

        # Data/Workflow/Config are warnings
        self.assertEqual(formatter._get_severity(4500), 'warning')
        self.assertEqual(formatter._get_severity(5500), 'warning')
        self.assertEqual(formatter._get_severity(6500), 'warning')

        # Others are errors
        self.assertEqual(formatter._get_severity(7500), 'error')
        self.assertEqual(formatter._get_severity(8500), 'error')


class TestErrorLogger(unittest.TestCase):
    """Test error logging functionality."""

    @patch('logging.getLogger')
    def test_logger_initialization(self, mock_get_logger):
        """Test logger setup."""
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger

        logger = ErrorLogger(logger_name='test.errors')

        mock_get_logger.assert_called_with('test.errors')
        mock_logger.setLevel.assert_called_with(logging.DEBUG)

    @patch('logging.getLogger')
    def test_log_error_flamingo_error(self, mock_get_logger):
        """Test logging a FlamingoError."""
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger

        logger = ErrorLogger()
        error = ConnectionError(
            "Test connection error",
            error_code=ErrorCodes.CONNECTION_REFUSED
        )

        logger.log_error(error, level=logging.ERROR)

        # Should log user message at ERROR level
        calls = mock_logger.log.call_args_list
        self.assertTrue(any(
            call[0][0] == logging.ERROR
            for call in calls
        ))

    @patch('logging.getLogger')
    def test_log_and_raise(self, mock_get_logger):
        """Test log_and_raise method."""
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger

        logger = ErrorLogger()
        error = ValidationError("Test error")

        with self.assertRaises(ValidationError) as cm:
            logger.log_and_raise(error)

        self.assertIs(cm.exception, error)
        mock_logger.log.assert_called()


class TestConvenienceFunctions(unittest.TestCase):
    """Test module-level convenience functions."""

    def test_format_error_convenience(self):
        """Test format_error convenience function."""
        error = CommandError("Test error")

        # Test different format types
        user_format = format_error(error, 'user')
        self.assertIsInstance(user_format, str)

        gui_format = format_error(error, 'gui')
        self.assertIsInstance(gui_format, dict)

        json_format = format_error(error, 'json')
        json.loads(json_format)  # Should parse as valid JSON

        # Invalid format type
        with self.assertRaises(ValueError):
            format_error(error, 'invalid')


class TestRealWorldScenarios(unittest.TestCase):
    """Test realistic error handling scenarios."""

    def test_connection_timeout_scenario(self):
        """Test realistic connection timeout handling."""
        import socket

        # Simulate timeout
        original_error = socket.timeout("Connection timed out")

        # Wrap in FlamingoError
        error = ConnectionError(
            "Failed to connect to microscope at 192.168.1.100:53717",
            error_code=ErrorCodes.CONNECTION_TIMEOUT,
            context={
                'ip_address': '192.168.1.100',
                'port': 53717,
                'timeout': 5.0
            },
            cause=original_error,
            suggestions=[
                "Check if microscope is powered on",
                "Verify network connectivity",
                "Check firewall settings"
            ]
        )

        # Format for different outputs
        formatter = ErrorFormatter()

        # User sees helpful message
        user_msg = formatter.format_for_user(error)
        self.assertIn("Failed to connect", user_msg)
        self.assertIn("Check if microscope", user_msg)

        # Log contains full details
        log_msg = formatter.format_for_log(error)
        self.assertIn(str(ErrorCodes.CONNECTION_TIMEOUT), log_msg)  # Check for numeric code
        self.assertIn("192.168.1.100", log_msg)

        # GUI gets structured data
        gui_data = formatter.format_for_gui(error)
        self.assertEqual(gui_data['severity'], 'error')  # Timeout errors have 'error' severity
        self.assertEqual(len(gui_data['suggestions']), 3)

    def test_command_chain_error_scenario(self):
        """Test error propagation through command chain."""
        # Stage 1: Low-level socket error
        socket_error = OSError("Broken pipe")

        # Stage 2: Wrapped as CommandError
        command_error = CommandError(
            "Failed to send stage movement command",
            command_code=24580,
            error_code=ErrorCodes.COMMAND_FAILED,
            cause=socket_error,
            context={'position': {'x': 100, 'y': 200, 'z': 50}}
        )

        # Stage 3: Wrapped as WorkflowError
        workflow_error = WorkflowError(
            "Workflow aborted due to stage movement failure",
            workflow_name="automated_scan",
            error_code=ErrorCodes.WORKFLOW_FAILED,
            cause=command_error,
            context={
                'step': 5,
                'total_steps': 20,
                'elapsed_time': 120.5
            },
            suggestions=[
                "Check stage mechanical limits",
                "Restart the workflow from step 5",
                "Home the stage and try again"
            ]
        )

        # Check error chain
        self.assertIs(workflow_error.cause, command_error)
        self.assertIs(command_error.cause, socket_error)

        # Format for display
        formatter = ErrorFormatter()
        user_msg = formatter.format_for_user(workflow_error)
        self.assertIn("Workflow aborted", user_msg)
        self.assertIn("Home the stage", user_msg)


if __name__ == '__main__':
    unittest.main()