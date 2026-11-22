"""The application version: the one place it is defined.

A leaf module that imports nothing, so code in any layer can read the version
without importing the package root's neighbors (``application``,
``main_window``). ``py2flamingo/__init__.py`` re-exports it as
``py2flamingo.__version__``. Readers look it up at call time
(``_version.__version__``), so a test can pin it in one place.
"""

__version__ = "0.6.2"
