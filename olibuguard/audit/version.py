"""Code version (git commit SHA) recorded with every decision."""

from __future__ import annotations

import subprocess
from functools import cache


@cache
def code_version() -> str:
    """Short git commit SHA of the running code, or 'unknown' (fail-safe)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"
