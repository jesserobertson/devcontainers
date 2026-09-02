"""devtemplate.pty package.

CHUNK / DRAIN_TIMEOUT come from the leaf module devtemplate.pty.constants and
are re-exported here eagerly (that module imports nothing of its own).
bridge_to_ssh_process and spawn_pty_process are resolved lazily via PEP 562
__getattr__ so that importing a name from this package - or merely importing one
of its submodules, e.g. devtemplate.pty.constants from devtemplate.forward on
the hot CLI path - does not pull in devtemplate.pty.bridge and, through it,
asyncssh + cryptography. See devtemplate.ssh for the documented lazy-import
boundary this preserves.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from devtemplate.pty.constants import CHUNK, DRAIN_TIMEOUT

__all__ = ["spawn_pty_process", "bridge_to_ssh_process", "CHUNK", "DRAIN_TIMEOUT"]

if TYPE_CHECKING:
    from devtemplate.pty.bridge import bridge_to_ssh_process
    from devtemplate.pty.spawn import spawn_pty_process


def __getattr__(name: str) -> Any:
    if name == "bridge_to_ssh_process":
        from devtemplate.pty.bridge import bridge_to_ssh_process

        return bridge_to_ssh_process
    if name == "spawn_pty_process":
        from devtemplate.pty.spawn import spawn_pty_process

        return spawn_pty_process
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
