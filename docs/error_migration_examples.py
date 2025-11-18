"""
Example migrations from old error patterns to new unified error handling.

This file demonstrates how to update existing code to use the new
FlamingoError framework.
"""

# ============================================================================
# EXAMPLE 1: Connection Service
# ============================================================================

# OLD PATTERN - Returning tuples
def connect_old(ip_address, port):
    try:
        socket.connect((ip_address, port))
        return True, None
    except Exception as e:
        return False, str(e)

# NEW PATTERN - Using FlamingoError
from py2flamingo.core.errors import ConnectionError, ErrorCodes
from py2flamingo.core.error_formatting import log_error

def connect_new(ip_address, port):
    """Connect to microscope with proper error handling."""
    try:
        socket.connect((ip_address, port))
    except socket.timeout as e:
        # Wrap timeout with context
        error = ConnectionError(
            f"Connection timeout to {ip_address}:{port}",
            error_code=ErrorCodes.CONNECTION_TIMEOUT,
            context={
                'ip_address': ip_address,
                'port': port,
                'timeout_seconds': 2.0
            },
            cause=e,
            suggestions=[
                "Check if the microscope is powered on",
                "Verify the IP address is correct",
                "Check network connectivity"
            ]
        )
        log_error(error)
        raise error
    except socket.error as e:
        # Wrap socket error
        error = ConnectionError(
            f"Failed to connect to microscope at {ip_address}:{port}",
            error_code=ErrorCodes.SOCKET_ERROR,
            context={
                'ip_address': ip_address,
                'port': port
            },
            cause=e
        )
        log_error(error)
        raise error


# ============================================================================
# EXAMPLE 2: Command Sending
# ============================================================================

# OLD PATTERN - Silent failures
def send_command_old(cmd_code, data):
    try:
        encoded = encode_command(cmd_code, data)
        socket.send(encoded)
    except:
        print(f"Failed to send command {cmd_code}")
        return None

# NEW PATTERN - Explicit error handling
from py2flamingo.core.errors import CommandError, ErrorCodes

def send_command_new(cmd_code, data):
    """Send command with proper error handling."""
    try:
        encoded = encode_command(cmd_code, data)
    except ValueError as e:
        # Handle encoding errors
        raise CommandError(
            f"Failed to encode command {cmd_code}",
            command_code=cmd_code,
            error_code=ErrorCodes.ENCODING_ERROR,
            context={'data': data},
            cause=e,
            suggestions=["Check command data format", "Verify command code is valid"]
        )

    try:
        socket.send(encoded)
    except socket.error as e:
        # Handle send failures
        raise CommandError(
            f"Failed to send command {cmd_code} to microscope",
            command_code=cmd_code,
            error_code=ErrorCodes.COMMAND_FAILED,
            cause=e,
            suggestions=["Check connection status", "Retry the command"]
        )


# ============================================================================
# EXAMPLE 3: Workflow Execution
# ============================================================================

# OLD PATTERN - Generic error messages
def execute_workflow_old(workflow_path):
    try:
        with open(workflow_path) as f:
            workflow = parse_workflow(f.read())
        run_workflow(workflow)
    except Exception as e:
        raise Exception(f"Workflow failed: {e}")

# NEW PATTERN - Specific error types
from py2flamingo.core.errors import WorkflowError, DataError, ErrorCodes

def execute_workflow_new(workflow_path):
    """Execute workflow with detailed error handling."""
    # File reading errors
    try:
        with open(workflow_path) as f:
            content = f.read()
    except FileNotFoundError as e:
        raise DataError(
            f"Workflow file not found: {workflow_path}",
            file_path=str(workflow_path),
            error_code=ErrorCodes.FILE_NOT_FOUND,
            cause=e,
            suggestions=["Check the workflow file path", "Select a different workflow"]
        )
    except IOError as e:
        raise DataError(
            f"Cannot read workflow file: {workflow_path}",
            file_path=str(workflow_path),
            error_code=ErrorCodes.FILE_READ_ERROR,
            cause=e
        )

    # Parsing errors
    try:
        workflow = parse_workflow(content)
    except ValueError as e:
        raise WorkflowError(
            f"Invalid workflow format in {workflow_path}",
            workflow_name=Path(workflow_path).stem,
            error_code=ErrorCodes.WORKFLOW_INVALID,
            cause=e,
            suggestions=["Check workflow file syntax", "Use the workflow editor to fix issues"]
        )

    # Execution errors
    try:
        run_workflow(workflow)
    except HardwareError:
        # Re-raise hardware errors as-is
        raise
    except Exception as e:
        raise WorkflowError(
            f"Workflow execution failed: {workflow.name}",
            workflow_name=workflow.name,
            error_code=ErrorCodes.WORKFLOW_FAILED,
            context={
                'step': workflow.current_step,
                'total_steps': workflow.total_steps
            },
            cause=e
        )


# ============================================================================
# EXAMPLE 4: Hardware Control
# ============================================================================

# OLD PATTERN - Boolean returns
def move_stage_old(x, y, z):
    if not is_position_valid(x, y, z):
        return False
    send_move_command(x, y, z)
    return True

# NEW PATTERN - Validation errors
from py2flamingo.core.errors import HardwareError, ValidationError, ErrorCodes

def move_stage_new(x, y, z):
    """Move stage with validation and error handling."""
    # Validate inputs
    if not isinstance(x, (int, float)):
        raise ValidationError(
            "X position must be numeric",
            field_name='x',
            error_code=ErrorCodes.TYPE_ERROR,
            context={'value': x, 'expected_type': 'float'}
        )

    # Check hardware limits
    if not is_position_valid(x, y, z):
        raise HardwareError(
            f"Stage position ({x}, {y}, {z}) exceeds limits",
            component='stage',
            error_code=ErrorCodes.STAGE_LIMIT,
            context={
                'requested_position': {'x': x, 'y': y, 'z': z},
                'limits': get_stage_limits()
            },
            suggestions=[
                "Check stage limits in settings",
                "Use a position within the valid range"
            ]
        )

    # Send command
    try:
        send_move_command(x, y, z)
    except CommandError:
        # Re-raise command errors
        raise
    except Exception as e:
        # Wrap unexpected errors
        raise HardwareError(
            "Stage movement failed",
            component='stage',
            error_code=ErrorCodes.HARDWARE_ERROR,
            context={'position': {'x': x, 'y': y, 'z': z}},
            cause=e
        )


# ============================================================================
# EXAMPLE 5: GUI Error Display
# ============================================================================

# OLD PATTERN - Simple message boxes
def handle_error_old(error):
    QMessageBox.critical(None, "Error", str(error))

# NEW PATTERN - Structured error display
from py2flamingo.core.error_formatting import ErrorFormatter

def handle_error_new(error):
    """Display error in GUI with appropriate detail level."""
    formatter = ErrorFormatter()
    error_info = formatter.format_for_gui(error)

    # Create detailed error dialog
    dialog = QMessageBox()
    dialog.setWindowTitle(error_info['title'])
    dialog.setText(error_info['message'])

    # Add suggestions if available
    if error_info['suggestions']:
        detailed_text = "Suggestions:\n"
        for suggestion in error_info['suggestions']:
            detailed_text += f"• {suggestion}\n"
        dialog.setDetailedText(detailed_text)

    # Set icon based on severity
    if error_info['severity'] == 'critical':
        dialog.setIcon(QMessageBox.Critical)
    elif error_info['severity'] == 'warning':
        dialog.setIcon(QMessageBox.Warning)
    else:
        dialog.setIcon(QMessageBox.Information)

    # Add error code to status bar
    if hasattr(error, 'error_code'):
        status_bar.showMessage(f"Error {error.error_code}: {error.message}", 5000)

    dialog.exec_()


# ============================================================================
# EXAMPLE 6: Service Layer Pattern
# ============================================================================

from py2flamingo.core.errors import wrap_external_error
from py2flamingo.core.error_formatting import get_error_logger

class MicroscopeService:
    """Example service with proper error handling."""

    def __init__(self):
        self.logger = get_error_logger()

    def initialize_hardware(self):
        """Initialize with comprehensive error handling."""
        try:
            # Camera initialization
            try:
                self.camera = Camera()
                self.camera.connect()
            except Exception as e:
                raise HardwareError(
                    "Camera initialization failed",
                    component='camera',
                    error_code=ErrorCodes.CAMERA_ERROR,
                    cause=e,
                    suggestions=["Check camera USB connection", "Restart camera software"]
                )

            # Stage initialization
            try:
                self.stage = Stage()
                self.stage.home()
            except Exception as e:
                raise HardwareError(
                    "Stage initialization failed",
                    component='stage',
                    error_code=ErrorCodes.STAGE_ERROR,
                    cause=e,
                    suggestions=["Check stage power", "Manually home the stage"]
                )

        except FlamingoError:
            # Re-raise our errors
            raise
        except Exception as e:
            # Wrap unexpected errors
            error = wrap_external_error(
                e,
                "Hardware initialization failed",
                HardwareError,
                component='system'
            )
            self.logger.log_and_raise(error)


# ============================================================================
# EXAMPLE 7: Async Operations with Timeout
# ============================================================================

from py2flamingo.core.errors import TimeoutError, ErrorCodes
import asyncio

async def wait_for_response_new(timeout_seconds=5.0):
    """Wait for response with timeout error handling."""
    try:
        response = await asyncio.wait_for(
            receive_data(),
            timeout=timeout_seconds
        )
        return response
    except asyncio.TimeoutError as e:
        raise TimeoutError(
            f"No response received within {timeout_seconds} seconds",
            timeout_seconds=timeout_seconds,
            error_code=ErrorCodes.RESPONSE_TIMEOUT,
            cause=e,
            suggestions=[
                "Check if the microscope is processing a command",
                "Increase timeout duration",
                "Verify connection is stable"
            ]
        )


# ============================================================================
# EXAMPLE 8: Configuration Handling
# ============================================================================

from py2flamingo.core.errors import ConfigurationError, ErrorCodes
import json

def load_configuration_new(config_path):
    """Load configuration with detailed error handling."""
    try:
        with open(config_path) as f:
            config = json.load(f)
    except FileNotFoundError as e:
        # Create default config if missing
        raise ConfigurationError(
            f"Configuration file not found: {config_path}",
            setting_name='config_file',
            error_code=ErrorCodes.CONFIG_NOT_FOUND,
            cause=e,
            suggestions=[
                "Run initial setup to create configuration",
                "Copy default configuration from templates"
            ]
        )
    except json.JSONDecodeError as e:
        raise ConfigurationError(
            f"Invalid JSON in configuration file",
            setting_name='config_file',
            error_code=ErrorCodes.CONFIG_INVALID,
            context={
                'file': str(config_path),
                'line': e.lineno,
                'column': e.colno
            },
            cause=e,
            suggestions=["Fix JSON syntax errors", "Restore from backup"]
        )

    # Validate required settings
    required = ['ip_address', 'port', 'timeout']
    missing = [key for key in required if key not in config]

    if missing:
        raise ConfigurationError(
            f"Missing required configuration: {', '.join(missing)}",
            error_code=ErrorCodes.MISSING_SETTING,
            context={'missing_keys': missing},
            suggestions=["Add missing settings to configuration", "Use configuration wizard"]
        )

    return config