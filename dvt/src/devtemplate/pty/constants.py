"""Small constants shared across devtemplate.pty, devtemplate.sshd, and
devtemplate.forward. A leaf module with no imports of its own so importers
that only need a chunk size (devtemplate.forward, on the hot CLI path) don't
transitively pull in asyncssh via devtemplate.pty.bridge."""

from __future__ import annotations

__all__ = ["CHUNK", "DRAIN_TIMEOUT"]

CHUNK = 4096
"""Read/write size for the blocking-I/O byte pumps - every byte pump touching
a pty session, shared with devtemplate.sshd, whose plain-pipe session path
uses the identical value for the identical reason (interactive terminal
traffic, latency over throughput)."""

DRAIN_TIMEOUT = 5.0
"""Seconds to let a bridge flush its final output on shutdown before it's
abandoned with the process: how long to wait for a blocking pump thread to
notice its socket end closed and flush its last output, after the async side
has finished. Shared with devtemplate.sshd for the same reason as CHUNK above
- both packages bridge blocking OS-level I/O into asyncio via a socketpair-
plus-daemon-thread shape, and both need the same drain budget."""
