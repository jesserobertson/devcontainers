"""Guard: importing the CLI must not transitively load asyncssh (a documented
lazy-import boundary - see devtemplate.ssh). Runs in a subprocess so a prior
test that already imported asyncssh can't mask a regression."""

from __future__ import annotations

import subprocess
import sys


def test_cli_import_does_not_pull_in_asyncssh():
    code = (
        "import devtemplate.cli, sys; sys.exit(1 if 'asyncssh' in sys.modules else 0)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, (
        "importing devtemplate.cli loaded asyncssh transitively "
        f"(stderr: {result.stderr})"
    )
