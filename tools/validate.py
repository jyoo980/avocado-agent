"""Check a session's private copy against the rules its merge must satisfy.

This is the `avocado-validate` entry point that the inner agent calls from inside its private copy
of the source directory. Each check is a subcommand that prints every violation with the rule it
breaks and a reason the agent can act on, and exits non-zero if there is one.

`reverify` checks the rule *no broken verified specs*: every function that verified before the
session must still verify with the session's edits. It reproduces what the harness does at merge
time (`avocado_verify._merge_and_verify`): compute the merge of the copy into the canonical file,
and, if the merge would carry the agent's edits to other functions, re-run CBMC on those functions
and on their already-verified in-file callers. It is slow -- one CBMC run per affected function --
and is meant to be run once, after the agent's last successful `avocado-run-cbmc` run. It writes
nothing outside a temporary directory and does not count as a verification attempt.

A pass is the tool's best prediction, not a guarantee, because the canonical file can still change
between the agent's run and the real merge.

Usage:
    % avocado-validate reverify --function <FUNCTION_NAME> \
                                --file <PATH_TO_C_FILE_IN_THE_SESSION_COPY> \
                                [-I <PATH_TO_INCLUDE_DIR(S)>]...
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from tools.run_cbmc_and_mutation_testing import verify_function
from tools.util import get_call_graph
from tools.util.contract_merge import find_function_span, functions_affected_by, merge_function
from tools.util.session_state import SNAPSHOT_IGNORE, read_run_state, read_session_info

# Exit codes: the copy passes the check, the copy breaks the rule, or the check could not be made.
EXIT_OK = 0
EXIT_VIOLATIONS = 1
EXIT_CANNOT_CHECK = 2

# The name violations of the `reverify` check are reported under.
_RULE_NO_BROKEN_VERIFIED_SPECS = "no-broken-verified-specs"

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
    reverify = subparsers.add_parser(
        "reverify",
        help="Re-verify the functions that verified before and that your edits can affect.",
        description=(
            "Check that every function that verified before this session still verifies with "
            "your edits. Runs CBMC once per affected function, so it can take several minutes; "
            "run it once, after your last successful avocado-run-cbmc run."
        ),
    )
    reverify.add_argument("--function", required=True, help="The function this session works on.")
    reverify.add_argument(
        "--file", required=True, help="Path to the C file in the session's private copy."
    )
    reverify.add_argument(
        "-I",
        "--include-dir",
        action="append",
        default=[],
        dest="include_dirs",
        metavar="DIR",
        help="Directory to add to the include search path. May be repeated.",
    )
    args = parser.parse_args()

    result = check_reverify(args.function, Path(args.file), include_dirs=args.include_dirs)
    print(result.render())
    sys.exit(result.exit_code)


def check_reverify(
    function: str, session_file: Path, *, include_dirs: list[str] | None = None
) -> CheckResult:
    """Check that the session's edits leave every previously verified function verified.

    Args:
        function (str): The function the session works on.
        session_file (Path): The session's private copy of the source file.
        include_dirs (list[str] | None): Directories forwarded to CBMC's include search path.

    Returns:
        CheckResult: `EXIT_OK` when nothing that verified before stops verifying,
            `EXIT_VIOLATIONS` with one line per function that does, and `EXIT_CANNOT_CHECK` when
            the file is not in a session copy, the run's state is unavailable, or the copy could
            not be merged at all.
    """
    info = read_session_info(session_file)
    if info is None:
        return _cannot_check(
            f"{session_file} is not inside a session's private copy (no session file found "
            "above it), so there is nothing to compare it against"
        )
    state = read_run_state(info.canonical_file)
    if state is None:
        return _cannot_check(
            f"the run's list of verified functions is not available for {info.canonical_file.name}"
        )
    try:
        snapshot = session_file.read_bytes()
        canonical = info.canonical_file.read_bytes()
    except OSError as error:
        return _cannot_check(f"could not read the files to compare: {error}")

    merged, report = merge_function(
        canonical=canonical,
        snapshot=snapshot,
        fork_base=info.fork_base,
        function=function,
        frozen_functions=state.active,
    )
    if not report.merged:
        return _cannot_check(f"this copy would not be merged: {report.reason}")
    notes = [f"note: this edit would not be merged: {entry}" for entry in report.dropped]
    if not report.carried:
        notes.append(
            f"{_RULE_NO_BROKEN_VERIFIED_SPECS}: ok; no other function's definition would "
            "change, so nothing needs re-verifying"
        )
        return CheckResult(EXIT_OK, notes=notes)

    scratch_root = Path(tempfile.mkdtemp(prefix=_SCRATCH_DIR_PREFIX))
    try:
        scratch_dir = scratch_root / info.canonical_file.parent.name
        shutil.copytree(info.canonical_file.parent, scratch_dir, ignore=SNAPSHOT_IGNORE)
        scratch_file = scratch_dir / info.canonical_file.name
        scratch_file.write_bytes(merged)
        call_graph = get_call_graph(str(scratch_file))
        affected = functions_affected_by(
            report.carried, call_graph=call_graph, verified=state.verified, exclude=function
        )
        verdicts = {
            other: verify_function(other, str(scratch_file), include_dirs=include_dirs)
            for other in affected
        }
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)

    violations = []
    for other in affected:
        if other not in state.verified or verdicts[other].is_function_verified:
            continue
        causes = [
            name
            for name in report.carried
            if name == other or name in call_graph.get_callees(other).internal
        ]
        violations.append(
            f"{_RULE_NO_BROKEN_VERIFIED_SPECS}: {other} verified before this session and no "
            f"longer verifies ({verdicts[other]!s}) with your edits to "
            f"{', '.join(_with_line(name, snapshot) for name in causes)}"
        )
    summary = ", ".join(f"{other} {verdicts[other]!s}" for other in affected)
    notes.append(f"re-verified {len(affected)} function(s): {summary}")
    if violations:
        notes.append(
            "Edits to other functions that break a verified function are discarded when this "
            "session is merged. Fix or undo them, then re-run avocado-run-cbmc and this check."
        )
        return CheckResult(EXIT_VIOLATIONS, violations=violations, notes=notes)
    notes.append(f"{_RULE_NO_BROKEN_VERIFIED_SPECS}: ok")
    return CheckResult(EXIT_OK, notes=notes)


def _cannot_check(reason: str) -> CheckResult:
    """Return the result for a check that could not be made.

    Args:
        reason (str): Why the check could not be made.

    Returns:
        CheckResult: An `EXIT_CANNOT_CHECK` result carrying the reason.
    """
    return CheckResult(EXIT_CANNOT_CHECK, notes=[f"cannot check: {reason}"])


def _with_line(function: str, source: bytes) -> str:
    """Return `function` followed by the line its definition starts on in `source`.

    Args:
        function (str): A function name.
        source (bytes): The session's copy of the source file.

    Returns:
        str: `name (line N)`, or the bare name when the definition cannot be located.
    """
    span = find_function_span(source, function)
    if span is None:
        return function
    line = source.count(b"\n", 0, span.start_byte) + 1
    return f"{function} (line {line})"


if __name__ == "__main__":
    main()
