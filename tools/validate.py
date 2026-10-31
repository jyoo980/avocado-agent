"""Check a session's snapshot copy against the rules its merge must satisfy.

This is the `avocado-validate` entry point called by an agent inside its snapshot copy.

`edits` checks the edit rules that need no verifier (rules 1-8 of CLAUDE.md): original code
unchanged, insertions only in contract slots or at file scope, the session's function intact,
reserved-prefix names, no `__CPROVER_assume` in new text, no other file touched, the file compiles,
and no literally false precondition. It takes seconds and is meant to be run before every
`avocado-run-cbmc` run. Rules 1-6 and 8 come from `tools.util.edit_rules.copy_violations`, the
same function the harness calls before it merges a copy. The session's function and fork base
come from the session file that the harness writes (`tools.util.session_state`), so the check is
always made against the function the merge checks.

Usage:
    % avocado-validate edits    --file <PATH_TO_C_FILE_IN_THE_SESSION_COPY> \
                                [-I <PATH_TO_INCLUDE_DIR(S)>]...
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from tools.run_cbmc import run_goto_cc
from tools.util.edit_rules import Violation, copy_violations, ordered_violations
from tools.util.session_state import (
    SESSION_FILE_NAME,
    SessionFileError,
    find_stray_files,
    read_session_info,
    session_root_of,
)

# Exit codes: the copy passes the check, the copy breaks the rule, or the check could not be made.
EXIT_OK = 0
EXIT_VIOLATIONS = 1
EXIT_CANNOT_CHECK = 2

_SCRATCH_DIR_PREFIX = "avocado-validate-"

# At most this many lines of goto-cc's messages are printed with a rule 7 violation.
_MAX_COMPILER_LINES = 40


@dataclass(frozen=True)
class CheckResult:
    """The outcome of one check.

    Attributes:
        exit_code (int): `EXIT_OK`, `EXIT_VIOLATIONS` or `EXIT_CANNOT_CHECK`.
        violations (list[str]): One line per broken rule, each naming the rule and the reason.
        notes (list[str]): Lines that are not violations but that the agent should see.
    """

    exit_code: int
    violations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        """Return the text to print for this result.

        Returns:
            str: The violations, then the notes, one per line.
        """
        return "\n".join([*self.violations, *self.notes])


def main() -> None:
    """Run one check on a session's private copy and exit with its code."""
    parser = argparse.ArgumentParser(
        description=(
            "Check a session's private copy of a C file against the rules its merge must "
            "satisfy. Prints every violation with the rule it breaks and the reason; exits 0 "
            "when there is none, 1 when there is at least one, and 2 when there is none but the "
            "check could not be made in full."
        )
    )
    subparsers = parser.add_subparsers(dest="check", required=True)
    edits = subparsers.add_parser(
        "edits",
        help="Check your edits against the edit rules that need no verifier (rules 1-8).",
        description=(
            "Check your copy of the source file against edit rules 1-8 in CLAUDE.md. Takes "
            "seconds; run it before every avocado-run-cbmc run."
        ),
    )
    edits.add_argument(
        "--file", required=True, help="Path to your session's private copy of the C file."
    )
    edits.add_argument(
        "-I",
        "--include-dir",
        action="append",
        default=[],
        dest="include_dirs",
        metavar="DIR",
        help="Directory to add to the include search path. May be repeated.",
    )
    args = parser.parse_args()

    try:
        result = check_copy_edits(Path(args.file), include_dirs=args.include_dirs)
    except Exception as error:  # ruff: ignore[blind-except] -- or a crash exits 1, like a violation
        traceback.print_exc()
        result = _cannot_check(f"internal error: {error!r}")
    print(result.render())
    sys.exit(result.exit_code)


def check_copy_edits(session_file: Path, *, include_dirs: list[str] | None = None) -> CheckResult:
    """Check the session's copy against the edit rules that need no verifier (rules 1-8).

    The session's function and fork base are read from the session file that the harness wrote
    beside the copied directory.

    Args:
        session_file (Path): The session's private copy of the source file.
        include_dirs (list[str] | None): Directories forwarded to `goto-cc` as `-I` flags.

    Returns:
        CheckResult: `EXIT_VIOLATIONS` with one line per broken rule (followed by goto-cc's
            messages when the file does not compile) when the copy breaks a rule;
            `EXIT_CANNOT_CHECK` when it breaks none of the rules that could be checked but
            `session_file` is not a session's copy of the source file, a file cannot be read, or
            goto-cc cannot be run to completion; and `EXIT_OK` otherwise.
    """
    try:
        info = read_session_info(session_file)
    except SessionFileError as error:
        return _cannot_check(str(error))
    root = session_root_of(session_file)
    if info is None:
        return _cannot_check(
            f"{session_file} is not a session's private copy of the source file (there is no "
            f"{SESSION_FILE_NAME} in {root}), so there is nothing to compare it against"
        )
    expected = root / info.canonical_file.parent.name / info.canonical_file.name
    if session_file.resolve() != expected:
        return _cannot_check(
            f"{session_file} is not the session's copy of the source file; pass --file {expected}"
        )
    try:
        snapshot = session_file.read_bytes()
        stray_files = find_stray_files(session_file, info.canonical_file.parent)
    except OSError as error:
        return _cannot_check(f"could not read the session's copy: {error}")

    violations = copy_violations(info.fork_base, snapshot, info.function, stray_files)
    notes: list[str] = []
    unchecked = None
    if shutil.which("goto-cc") is None:
        unchecked = "goto-cc is not on PATH"
    else:
        with tempfile.TemporaryDirectory(prefix=_SCRATCH_DIR_PREFIX) as scratch_dir:
            compiled = run_goto_cc(
                info.function,
                str(session_file.resolve()),
                include_dirs=[str(Path(directory).resolve()) for directory in include_dirs or []],
                cwd=scratch_dir,
            )
        if compiled is None:
            unchecked = "goto-cc timed out"
        elif compiled.returncode != 0:
            violations.append(
                Violation(
                    7,
                    f"the file does not compile with goto-cc (exit code {compiled.returncode}); "
                    "goto-cc's messages follow",
                )
            )
            notes = _compiler_messages(compiled.stderr + compiled.stdout)

    rendered = [str(entry) for entry in ordered_violations(violations)]
    if unchecked is not None:
        reason = f"{unchecked}, so rule 7 (the file compiles) is unchecked"
        if violations:
            return CheckResult(EXIT_VIOLATIONS, violations=rendered, notes=[f"note: {reason}"])
        return _cannot_check(reason)
    if violations:
        return CheckResult(EXIT_VIOLATIONS, violations=rendered, notes=notes)
    return CheckResult(EXIT_OK, notes=["edit rules 1-8: ok"])


def _compiler_messages(output: str) -> list[str]:
    """Return goto-cc's messages, indented and cut to `_MAX_COMPILER_LINES` lines.

    Args:
        output (str): What goto-cc printed.

    Returns:
        list[str]: The lines to print beneath a rule 7 violation.
    """
    lines = output.strip().splitlines() or ["(goto-cc printed nothing)"]
    if len(lines) > _MAX_COMPILER_LINES:
        omitted = len(lines) - _MAX_COMPILER_LINES
        lines = [*lines[:_MAX_COMPILER_LINES], f"... ({omitted} more lines)"]
    return [f"    {line}" for line in lines]


def _cannot_check(reason: str) -> CheckResult:
    """Return the result for a check that could not be made.

    Args:
        reason (str): Why the check could not be made.

    Returns:
        CheckResult: An `EXIT_CANNOT_CHECK` result carrying the reason.
    """
    return CheckResult(EXIT_CANNOT_CHECK, notes=[f"cannot check: {reason}"])


if __name__ == "__main__":
    main()
