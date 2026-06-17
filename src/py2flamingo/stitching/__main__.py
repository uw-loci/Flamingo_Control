"""Compatibility entry point — delegates to the standalone package.

The stitching CLI now lives in ``flamingo_stitcher`` (single source of truth).
This thin shim keeps the historical ``python -m py2flamingo.stitching ...``
invocation working by forwarding to ``flamingo_stitcher.__main__.main``.
"""

from flamingo_stitcher.__main__ import main

if __name__ == "__main__":
    main()
