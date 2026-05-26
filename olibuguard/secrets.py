"""Secret access via the system keyring (DPAPI on Windows, Keychain on macOS).

Exchange API keys never live in plaintext files (section 5.6).
"""

from __future__ import annotations

import keyring

_SERVICE = "olibuguard"


def get_secret(name: str) -> str | None:
    return keyring.get_password(_SERVICE, name)


def set_secret(name: str, value: str) -> None:
    keyring.set_password(_SERVICE, name, value)
