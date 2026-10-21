"""Host supplied provider hooks.

The roundtable is embedded in the Buffett backend.  These hooks keep vendor
credentials and event loops in the host while preserving the panel's sync
provider interfaces.  They are deliberately process local and never persist
secrets.
"""
from __future__ import annotations

from typing import Any, Callable

NumcatQuery = Callable[[str, dict, str], dict]
Available = Callable[[], bool]

_numcat_query: NumcatQuery | None = None
_numcat_available: Available | None = None


def configure_shared_numcat(query_sync: NumcatQuery | None,
                            available_callback: Available | None = None) -> None:
    """Install the host's synchronous NumCat gateway bridge."""
    global _numcat_query, _numcat_available
    _numcat_query = query_sync
    _numcat_available = available_callback


def shared_numcat() -> tuple[NumcatQuery | None, Available | None]:
    return _numcat_query, _numcat_available


def clear_shared_numcat() -> None:
    configure_shared_numcat(None, None)
