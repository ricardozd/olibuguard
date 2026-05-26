"""Acceso a secretos vía keyring del sistema (DPAPI en Windows, Keychain en mac).

Las API keys NO viven en archivos planos (sección 5.6).
"""

from __future__ import annotations

import keyring

_SERVICE = "olibuguard"


def get_secret(name: str) -> str | None:
    return keyring.get_password(_SERVICE, name)


def set_secret(name: str, value: str) -> None:
    keyring.set_password(_SERVICE, name, value)
