"""Keep Windows from sleeping while the supervisor runs.

This is the per-thread request media players use (SetThreadExecutionState):
it changes no power setting, does not keep the display on, and ends when the
supervisor resets it or exits.
"""

from __future__ import annotations

import sys


ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def keep_awake(enabled: bool) -> bool:
    """Returns False when the request is unsupported or rejected."""
    if sys.platform != "win32":
        return False
    import ctypes

    flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if enabled else 0)
    return ctypes.windll.kernel32.SetThreadExecutionState(flags) != 0
