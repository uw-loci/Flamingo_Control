"""
Entry Point - Module Execution

This module serves as the entry point when running the package as a module:
    python -m py2flamingo

It simply imports and calls the main() function from the CLI module.
All command-line argument parsing and application initialization logic
is in cli.py.
"""

from py2flamingo.cli import main

if __name__ == "__main__":
    main()
