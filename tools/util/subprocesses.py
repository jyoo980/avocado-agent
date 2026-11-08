"""Run a subprocess with a timeout that kills every process it started."""

import subprocess
import time
from contextlib import suppress
from subprocess import TimeoutExpired

import psutil
from loguru import logger

# Seconds that `_kill_process_tree` waits for the processes it killed to exit. A process that
# receives SIGKILL exits promptly, but freeing a large address space (as of a CBMC solver) can
# take a moment.
_KILLED_PROCESS_EXIT_TIMEOUT_SEC = 5.0


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
    (i.e., whose parent exited, so that they are no longer descendants). After killing the
    command and its descendants, this function waits up to `_KILLED_PROCESS_EXIT_TIMEOUT_SEC`
    seconds for them to exit, so that they no longer use CPU or memory when it returns. It does
    not wait longer, even if some killed process (possibly the command itself) has not exited.

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
    # `Popen` is not used as a context manager, because on exit that waits for the command without
    # a timeout, which would never return if the command survived SIGKILL.
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=isinstance(command, str),
        cwd=cwd,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except TimeoutExpired:
        _kill_process_tree(process.pid)
        return None
    except BaseException:
        _kill_process_tree(process.pid)
        raise
    finally:
        # Do not read the output pipes to end-of-file: an orphaned descendant can hold them open
        # indefinitely.
        for pipe in (process.stdout, process.stderr):
            if pipe is not None:
                pipe.close()
        # Collect the command's exit status if it has exited, so that it does not remain a zombie.
        # `_kill_process_tree` has already waited for it to exit. If it still has not exited,
        # `subprocess` collects its exit status later.
        process.poll()
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _kill_process_tree(pid: int) -> None:
    """Send SIGKILL to the given process and to every process descended from it, and wait for them.

    Each process is stopped before its children are listed, so that no process can start a child
    that escapes the kill. A process that cannot be stopped still has its children listed and is
    still sent SIGKILL. If an exception interrupts the search for descendants, the processes
    found so far are sent SIGKILL before the exception propagates. This function returns once
    every process that was sent SIGKILL has exited (a zombie counts as exited), or after
    `_KILLED_PROCESS_EXIT_TIMEOUT_SEC` seconds, in which case it logs a warning that lists the
    processes that have not exited.

    Args:
        pid (int): The id of the root process.
    """
    try:
        frontier = [psutil.Process(pid)]
    except psutil.Error:
        return
    found: list[psutil.Process] = []
    killed: list[psutil.Process] = []
    try:
        while frontier:
            process = frontier.pop()
            found.append(process)
            with suppress(psutil.Error):
                process.suspend()
            with suppress(psutil.Error):
                frontier.extend(process.children())
    finally:
        # Even if an exception (such as `KeyboardInterrupt`) interrupts the traversal, kill every
        # process found so far, so that none is left stopped, holding its memory.
        for process in found + frontier:
            with suppress(psutil.Error):
                process.kill()
                killed.append(process)
    deadline = time.monotonic() + _KILLED_PROCESS_EXIT_TIMEOUT_SEC
    while killed := [process for process in killed if not _has_exited(process)]:
        if time.monotonic() >= deadline:
            logger.warning(
                f"processes {[process.pid for process in killed]} had not exited "
                f"{_KILLED_PROCESS_EXIT_TIMEOUT_SEC} seconds after being sent SIGKILL"
            )
            return
        time.sleep(0.01)


def _has_exited(process: psutil.Process) -> bool:
    """Return True if the given process has exited, or if its state cannot be read.

    A zombie (a process that has exited but whose parent has not yet collected its exit status)
    counts as exited. Waiting for a zombie to be collected could take indefinitely long: the
    parent might never collect it, as when the parent is the caller itself, or when an orphan's
    new parent is a container's PID 1 that collects no exit statuses.

    Args:
        process (psutil.Process): a process.

    Returns:
        bool: True if the process has exited or its state cannot be read, False if it is
            running or stopped.
    """
    try:
        return not process.is_running() or process.status() == psutil.STATUS_ZOMBIE
    except psutil.Error:
        return True
