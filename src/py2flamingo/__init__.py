"""Py2Flamingo: control software for Flamingo light-sheet microscopes.

Importing the package loads nothing else; the application starts from
``python -m py2flamingo`` (``cli.py`` -> ``application.py``).
"""

from py2flamingo._version import __version__

__all__ = ["__version__"]
