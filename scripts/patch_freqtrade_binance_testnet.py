#!/usr/bin/env python3
"""Patch Freqtrade so Binance can use the futures testnet (testnet.binancefuture.com).

WHY THIS EXISTS
---------------
Freqtrade marks Binance with ``supports_demo_trading=False`` and exposes no sandbox
flag, so there is no config-only way to reach the Binance futures testnet.  ccxt
*does* support it via ``set_sandbox_mode(True)`` (verified: it routes to
testnet.binancefuture.com and sets ``apiBackup`` so ``fetch_currencies`` skips the
sapi endpoint that otherwise breaks ``load_markets``).  This script injects that
call into ``Exchange._init_ccxt`` — right after the ccxt client is built and before
markets are loaded — gated on the ``OLIBUGUARD_BINANCE_TESTNET`` env var.

SAFETY
------
* The injected code only acts when OLIBUGUARD_BINANCE_TESTNET is truthy AND the
  exchange is Binance.  With no env var, behaviour is 100% unchanged.
* With Binance *testnet* API keys it is physically impossible to reach production:
  testnet keys fail auth against the live API.  Worst case is a connection error,
  never a real-money order.
* Idempotent: re-running does nothing if already patched.
* Reversible: ``--revert`` removes the block cleanly.

USAGE
-----
    python scripts/patch_freqtrade_binance_testnet.py          # apply
    python scripts/patch_freqtrade_binance_testnet.py --revert  # remove
    python scripts/patch_freqtrade_binance_testnet.py --check   # report only

Re-run after any ``uv sync`` / Freqtrade upgrade (the patch lives in site-packages,
which is not version-controlled; this script is).
"""

from __future__ import annotations

import sys
from pathlib import Path

BEGIN = "# >>> OLIBUGUARD BINANCE TESTNET PATCH >>>"
END = "# <<< OLIBUGUARD BINANCE TESTNET PATCH <<<"

# Anchor: the enable_demo_trading block immediately followed by `return api`,
# inside Exchange._init_ccxt.  Unique in the file.
ANCHOR = (
    "            api.enable_demo_trading(True)\n"
    "\n"
    "        return api\n"
)

PATCH_BODY = (
    "            api.enable_demo_trading(True)\n"
    "\n"
    f"        {BEGIN}\n"
    "        # Route Binance to the futures testnet when OLIBUGUARD_BINANCE_TESTNET is set.\n"
    "        # See scripts/patch_freqtrade_binance_testnet.py for rationale + safety.\n"
    "        import os as _olibuguard_os\n"
    "\n"
    '        if name.lower() == "binance" and _olibuguard_os.environ.get(\n'
    '            "OLIBUGUARD_BINANCE_TESTNET", ""\n'
    '        ).strip().lower() in ("1", "true", "yes", "on"):\n'
    "            api.set_sandbox_mode(True)\n"
    "            logger.warning(\n"
    '                "OLIBUGUARD_BINANCE_TESTNET active: Binance %s API routed to "\n'
    '                "testnet.binancefuture.com (set_sandbox_mode)",\n'
    '                "sync" if sync else "async",\n'
    "            )\n"
    f"        {END}\n"
    "\n"
    "        return api\n"
)


def _exchange_py() -> Path:
    import freqtrade.exchange.exchange as mod

    return Path(mod.__file__)


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "--apply"
    path = _exchange_py()
    src = path.read_text(encoding="utf-8")
    patched = BEGIN in src

    if mode == "--check":
        print(f"{'PATCHED' if patched else 'NOT patched'}: {path}")
        return 0

    if mode == "--revert":
        if not patched:
            print(f"Nothing to revert (not patched): {path}")
            return 0
        new = src.replace(PATCH_BODY, ANCHOR)
        if new == src:
            print("ERROR: patch markers present but block did not match; revert manually.")
            return 1
        path.write_text(new, encoding="utf-8")
        print(f"✓ Reverted patch in {path}")
        return 0

    # --apply (default)
    if patched:
        print(f"✓ Already patched (idempotent no-op): {path}")
        return 0
    if ANCHOR not in src:
        print(
            "ERROR: anchor not found — Freqtrade's _init_ccxt may have changed.\n"
            f"  File: {path}\n"
            "  Inspect the file and update ANCHOR in this script."
        )
        return 1
    new = src.replace(ANCHOR, PATCH_BODY, 1)
    path.write_text(new, encoding="utf-8")
    print(f"✓ Applied Binance-testnet patch to {path}")
    print("  Activate at runtime with: OLIBUGUARD_BINANCE_TESTNET=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
