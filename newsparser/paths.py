"""Shared filesystem path conventions.

``workspace_dir()`` is the single source for the ``WORKSPACE_DIR`` root that was
previously copied verbatim across ~10 modules. It reads the env var at call time
so tests can monkeypatch ``WORKSPACE_DIR`` per-test.
"""

import os
from pathlib import Path


def workspace_dir() -> Path:
    """The workspace root — ``$WORKSPACE_DIR`` (default ``workspace``)."""
    return Path(os.environ.get("WORKSPACE_DIR", "workspace"))
