"""Tests for running subprocesses with a timeout."""

import os
import time
from pathlib import Path

from tools.util.subprocesses import run_command


def test_run_command_kills_every_descendant_on_timeout(tmp_path: Path) -> None:
    marker = tmp_path / "survived"
    # Killing only the shell would leave the background subshell running to create the marker.
    completed = run_command(f"(sleep 1; touch {marker}) & sleep 10", timeout=0.3)
    assert completed is None
    time.sleep(1.5)
    assert not marker.exists()


def test_run_command_returns_on_timeout_even_if_an_orphan_holds_its_output() -> None:
    # The subshell exits at once, orphaning the `sleep 5`, which is then not killed but keeps
    # the command's standard output open.
    start = time.monotonic()
    completed = run_command("(sleep 5 &); sleep 10", timeout=0.3)
    assert completed is None
    assert time.monotonic() - start < 3


def test_run_command_stays_in_the_callers_process_group() -> None:
    # A signal sent to the caller's process group, such as the SIGINT from Ctrl-C, must reach the
    # command too.
    completed = run_command("ps -o pgid= -p $$", timeout=10)
    assert completed is not None
    assert int(completed.stdout) == os.getpgrp()
