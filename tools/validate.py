"""Check a session's snapshot copy against the rules its merge must satisfy.

This is the `avocado-validate` entry point called by an agent inside its snapshot copy.

`edits` checks the edit rules that need no verifier (rules 1-8 of CLAUDE.md): original code
unchanged, insertions only in contract slots or at file scope, the session's function intact,
reserved-prefix names, no `__CPROVER_assume` in new text, no other file touched, the file compiles,
and no literally false precondition. It takes seconds and is meant to be run before every
`avocado-run-cbmc` run. Rules 1-5 and 8 come from `tools.util.edit_rules.check_edits`, the same
function the harness calls before it merges a copy.

Usage:
    % avocado-validate edits    --function <FUNCTION_NAME> \
                                --file <PATH_TO_C_FILE_IN_THE_SESSION_COPY> \
                                [-I <PATH_TO_INCLUDE_DIR(S)>]...
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from tools.run_cbmc import compile_with_goto_cc
from tools.util.edit_rules import Violation, check_edits, stray_file_violations
from tools.util.session_state import find_stray_files, read_session_info

# Exit codes: the copy passes the check, the copy breaks the rule, or the check could not be made.
EXIT_OK = 0
EXIT_VIOLATIONS = 1
EXIT_CANNOT_CHECK = 2

_SCRATCH_DIR_PREFIX = "avocado-validate-"


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
            "when there is none, 1 when there is one, 2 when the check could not be made."
        )
    )
    subparsers = parser.add_subparsers(dest="check", required=True)
    edits = subparsers.add_parser(
        "edits",
        help="Check your edits against the edit rules that need no verifier (rules 1-8).",
        description=(
            "Check that your copy changes no original code, inserts text only in contract slots "
            "or at file scope, uses the reserved prefix for new names, touches no other file and "
            "still compiles. Takes seconds; run it before every avocado-run-cbmc run."
        ),
    )
    edits.add_argument("--function", required=True, help="The function this session works on.")
    edits.add_argument(
        "--file", required=True, help="Path to the C file in the session's private copy."
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

    result = check_copy_edits(args.function, Path(args.file), include_dirs=args.include_dirs)
    print(result.render())
    sys.exit(result.exit_code)


def check_copy_edits(
    function: str, session_file: Path, *, include_dirs: list[str] | None = None
) -> CheckResult:
    """Check the session's copy against the edit rules that need no verifier (rules 1-8).

    Args:
        function (str): The function the session works on.
        session_file (Path): The session's private copy of the source file.
        include_dirs (list[str] | None): Directories forwarded to `goto-cc` as `-I` flags.

    Returns:
        CheckResult: `EXIT_OK` when the copy breaks no rule, `EXIT_VIOLATIONS` with one line per
            broken rule, and `EXIT_CANNOT_CHECK` when the file is not in a session copy or cannot
            be read.
    """
    info = read_session_info(session_file)
    if info is None:
        return _cannot_check(
            f"{session_file} is not inside a session's private copy (no session file found "
            "above it), so there is nothing to compare it against"
        )
    try:
        snapshot = session_file.read_bytes()
    except OSError as error:
        return _cannot_check(f"could not read {session_file}: {error}")

    violations = check_edits(info.fork_base, snapshot, function)
    violations += stray_file_violations(find_stray_files(session_file, info.canonical_file.parent))
    with tempfile.TemporaryDirectory(prefix=_SCRATCH_DIR_PREFIX) as scratch_dir:
        returncode = compile_with_goto_cc(
            function,
            str(session_file.resolve()),
            include_dirs=[str(Path(directory).resolve()) for directory in include_dirs or []],
            cwd=scratch_dir,
        )
    if returncode != 0:
        violations.append(
            Violation(
                7,
                f"the file does not compile with goto-cc (exit code {returncode}); run "
                "avocado-run-cbmc to see the compiler's message",
            )
        )
    if violations:
        return CheckResult(EXIT_VIOLATIONS, violations=[str(entry) for entry in violations])
    return CheckResult(EXIT_OK, notes=["edit rules 1-8: ok"])


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
