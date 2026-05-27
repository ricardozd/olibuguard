"""File-based kill switch: freeze all new trade entries without restarting the bot.

Creating or deleting the sentinel file is instantaneous and operator-safe.
The strategy adapter checks ``is_active()`` before every entry decision; if the
file exists the trade is vetoed and audited with reason "kill switch active".

Typical operator workflow (Freqtrade running in paper/live):
    task kill            # activates   → bot stops opening new positions
    task resume          # deactivates → bot resumes normal operation

The sentinel file is human-readable: it records who activated it and when.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from pathlib import Path


class KillSwitch:
    """File-based kill switch: active iff the sentinel file exists.

    Args:
        path: Absolute or relative path to the sentinel file.
              Conventional default: ``<user_data_dir>/KILL_SWITCH``.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def is_active(self) -> bool:
        """Return True if the sentinel file exists."""
        return self._path.exists()

    def activate(self, reason: str = "manual") -> None:
        """Create the sentinel file with a human-readable timestamp. Idempotent."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).isoformat()
        self._path.write_text(
            f"kill_switch: active\n"
            f"activated_at: {stamp}\n"
            f"reason: {reason}\n"
        )

    def deactivate(self) -> None:
        """Remove the sentinel file. No-op if already inactive."""
        with contextlib.suppress(FileNotFoundError):
            self._path.unlink()
