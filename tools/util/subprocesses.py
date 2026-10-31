"""Run a subprocess with a timeout that kills every process it started."""

import subprocess
from contextlib import suppress
from subprocess import TimeoutExpired

import psutil


def run_command(
    command: str | list[str], timeout: float, cwd: str | None = None
) -> subprocess.CompletedProcess[str] | None:
    """Run a command, capturing its output, and kill it and all its descendants on timeout.

    Every pipeline subprocess (`goto-cc`, `goto-instrument`, `cbmc`) should be run through this
    function. The command reads no standard input. It stays in the caller's process group, so a
    signal sent to that group (such as the SIGINT from Ctrl-C, or the SIGKILL from a harness that
    kills a whole process group) reaches it and its descendants too. On timeout, or if an
    exception (such as `KeyboardInterrupt`) interrupts the wait, the command and every process
    descended from it are killed, not just the immediate child. Two kinds of descendant are not
    killed: those still running after the command exits normally, and those already orphaned
    (i.e., whose parent exited, so that they are no longer descendants).

    Args:
        command (str | list[str]): The command to run. A string is run by the shell; a list is
            an argument vector that is run directly.
        timeout (float): Seconds after which the command and every process it started are killed.
        cwd (str | None): Working directory for the subprocess. When None, the subprocess
            inherits the current working directory.

    Returns:
        subprocess.CompletedProcess[str] | None: The finished subprocess, with its captured stdout
            and stderr, or None if it hit the timeout.
    """
    with subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=isinstance(command, str),
        cwd=cwd,
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except TimeoutExpired:
            _kill_process_tree(process.pid)
            process.communicate()
            return None
        except BaseException:
            _kill_process_tree(process.pid)
            raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _kill_process_tree(pid: int) -> None:
    """Send SIGKILL to the given process and to every process descended from it.

    Each process is stopped before its children are listed, so that no process can start a child
    that escapes the kill.

    Args:
        pid (int): The id of the root process.
    """
    try:
        frontier = [psutil.Process(pid)]
    except psutil.Error:
        return
    stopped: list[psutil.Process] = []
    while frontier:
        process = frontier.pop()
        try:
            process.suspend()
        except psutil.Error:
            continue
        stopped.append(process)
        with suppress(psutil.Error):
            frontier.extend(process.children())
    for process in stopped:
        with suppress(psutil.Error):
            process.kill()
