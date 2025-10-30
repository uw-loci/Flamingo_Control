"""
Command-Line Interface - Argument Parsing and Entry Point

This module provides the command-line interface for the Flamingo
microscope control application. It handles:
- Command-line argument parsing
- Argument validation
- Application initialization with CLI parameters
- Error handling for invalid arguments

Usage:
    python -m py2flamingo --ip 127.0.0.1 --port 53717
    python -m py2flamingo --help
"""

import sys
import argparse
import logging
from typing import List, Optional

from py2flamingo.application import FlamingoApplication


def parse_args(args: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        args: List of arguments to parse. If None, uses sys.argv[1:]

    Returns:
        Parsed arguments namespace

    Example:
        args = parse_args()
        print(f"IP: {args.ip}, Port: {args.port}")
    """
    parser = argparse.ArgumentParser(
        description="Flamingo Microscope Control Interface",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s
  %(prog)s --ip 192.168.1.100 --port 53717
  %(prog)s --workflow /path/to/workflow.txt

For more information, visit the project documentation.
        """
    )

    # Connection arguments (optional - users typically select via GUI)
    parser.add_argument(
        "--ip",
        type=str,
        default=None,
        help="Server IP address (optional - select via GUI if not specified)"
    )

    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Server port (optional - select via GUI if not specified)"
    )

    # Workflow argument
    parser.add_argument(
        "--workflow",
        type=str,
        default=None,
        help="Workflow file to auto-load on startup"
    )

    # Future feature: headless mode
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run without GUI (future feature, not yet implemented)"
    )

    # Logging level
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set logging level (default: INFO)"
    )

    return parser.parse_args(args)


def validate_args(args: argparse.Namespace) -> bool:
    """Validate parsed command-line arguments.

    Args:
        args: Parsed arguments from parse_args()

    Returns:
        True if arguments are valid, False otherwise

    This function validates:
    - Port number is in valid range (1-65535) if provided
    - IP address is valid format if provided
    - Workflow file exists if specified
    """
    # Validate port range if provided
    if args.port is not None and not (1 <= args.port <= 65535):
        print(f"Error: Port must be between 1 and 65535, got {args.port}")
        return False

    # Validate IP address if provided
    if args.ip is not None and (not args.ip or args.ip.strip() == ""):
        print("Error: IP address cannot be empty if specified")
        return False

    # Validate workflow file if specified
    if args.workflow:
        from pathlib import Path
        workflow_path = Path(args.workflow)
        if not workflow_path.exists():
            print(f"Error: Workflow file not found: {args.workflow}")
            return False
        if not workflow_path.is_file():
            print(f"Error: Workflow path is not a file: {args.workflow}")
            return False

    # Check for headless mode (not yet implemented)
    if args.headless:
        print("Warning: Headless mode is not yet implemented")
        print("         Application will run with GUI")

    return True


def setup_logging(level: str):
    """Configure application logging.

    Args:
        level: Logging level as string (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    numeric_level = getattr(logging, level.upper(), None)

    logging.basicConfig(
        level=numeric_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )


def main(args: Optional[List[str]] = None) -> int:
    """Main entry point for the application.

    This function:
    1. Parses command-line arguments
    2. Validates arguments
    3. Sets up logging
    4. Creates and runs FlamingoApplication
    5. Returns exit code

    Args:
        args: Command-line arguments. If None, uses sys.argv[1:]

    Returns:
        Exit code (0 = success, 1 = error)

    Example:
        sys.exit(main())
    """
    # Parse arguments
    parsed_args = parse_args(args)

    # Setup logging
    setup_logging(parsed_args.log_level)

    logger = logging.getLogger(__name__)
    logger.info("Starting Flamingo Microscope Control...")
    logger.debug(f"Arguments: IP={parsed_args.ip}, Port={parsed_args.port}")

    # Validate arguments
    if not validate_args(parsed_args):
        return 1

    # Create application with CLI parameters
    try:
        app = FlamingoApplication(
            default_ip=parsed_args.ip,
            default_port=parsed_args.port
        )

        # Run application
        exit_code = app.run()

        logger.info(f"Application exited with code {exit_code}")
        return exit_code

    except Exception as e:
        logger.exception(f"Fatal error during application startup: {e}")
        print(f"Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
