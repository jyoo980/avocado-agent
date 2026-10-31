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

`conflicts` checks rule 10, *no merge conflict*: whether any of the session's edits would be left
out of the merge because of what other sessions did since the copy was taken -- an edit to a
function that another live session is working on, an edit to a function whose contract someone
else has changed meanwhile, or a new name the canonical file now defines differently. It computes
the same merge the harness will (`tools.util.edit_rules.merge_copy`) without committing it, and
takes about a second. It also notes, without failing, each function the session's function calls
whose contract another session has changed in the canonical file meanwhile, and prints the new
contract: the function will be verified against it when the session is merged.

`reverify` checks rule 9, *no broken verified specs*: every function that verified before the
session must still verify with the session's edits. It reproduces what the harness does at merge
time (`avocado_verify._merge_and_verify`): compute the merge of the copy into the canonical file,
and, if the merge would carry the agent's edits to other functions, re-run CBMC on those functions
and on their already-verified in-file callers. It is slow -- one CBMC run per affected function --
and is meant to be run once, after the agent's last successful `avocado-run-cbmc` run. It writes
nothing outside a temporary directory and does not count as a verification attempt.

A pass is the tool's best prediction, not a guarantee, because the canonical file can still change
between the agent's run and the real merge.

Usage:
    % avocado-validate edits    --file <PATH_TO_C_FILE_IN_THE_SESSION_COPY> \
                                [-I <PATH_TO_INCLUDE_DIR(S)>]...
    % avocado-validate conflicts --function <FUNCTION_NAME> \
                                 --file <PATH_TO_C_FILE_IN_THE_SESSION_COPY>
    % avocado-validate reverify --function <FUNCTION_NAME> \
                                --file <PATH_TO_C_FILE_IN_THE_SESSION_COPY> \
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
from tools.run_cbmc_and_mutation_testing import verify_function
from tools.util import get_call_graph
from tools.util.edit_rules import (
    RULE_TITLES,
    Violation,
    check_edits,
    copy_violations,
    frozen_functions_for,
    function_header,
    function_line,
    functions_affected_by,
    functions_with_changed_contracts,
    merge_copy,
    ordered_violations,
)
from tools.util.session_state import (
    SESSION_FILE_NAME,
    SNAPSHOT_IGNORE,
    SessionFileError,
    find_stray_files,
    read_run_state,
    read_session_info,
    rechecked_on_change,
    session_root_of,
)

# Exit codes: the copy passes the check, the copy breaks the rule, or the check could not be made.
EXIT_OK = 0
EXIT_VIOLATIONS = 1
EXIT_CANNOT_CHECK = 2

# The rule the `reverify` check enforces, and how its passing result is labelled.
_REVERIFY_RULE = 9
_REVERIFY_LABEL = f"rule {_REVERIFY_RULE} ({RULE_TITLES[_REVERIFY_RULE]})"

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
    conflicts = subparsers.add_parser(
        "conflicts",
        help="Check whether other sessions' merges would keep any of your edits out.",
        description=(
            "Check your copy against the shared file as it is now. Reports each of your edits "
            "that would be left out of the merge because another session is working on or has "
            "changed the same function, and any new name that now clashes. Also notes each "
            "function yours calls whose contract has changed there. Takes about a second."
        ),
    )
    reverify = subparsers.add_parser(
        "reverify",
        help="Re-verify the functions that verified before and that your edits can affect.",
        description=(
            "Check that every function that verified before this session still verifies with "
            "your edits. Runs CBMC once per affected function, so it can take several minutes; "
            "run it once, after your last successful avocado-run-cbmc run."
        ),
    )
    for subparser in (conflicts, reverify):
        subparser.add_argument(
            "--function", required=True, help="The function this session works on."
        )
        subparser.add_argument(
            "--file", required=True, help="Path to the C file in the session's private copy."
        )
    for subparser in (edits, reverify):
        subparser.add_argument(
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
        if args.check == "conflicts":
            result = check_conflicts(args.function, Path(args.file))
        elif args.check == "edits":
            result = check_copy_edits(Path(args.file), include_dirs=args.include_dirs)
        else:
            result = check_reverify(args.function, Path(args.file), include_dirs=args.include_dirs)
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


def check_conflicts(function: str, session_file: Path) -> CheckResult:
    """Check whether the merge would leave out any of the session's edits (rule 10).

    Args:
        function (str): The function the session works on.
        session_file (Path): The session's private copy of the source file.

    Returns:
        CheckResult: `EXIT_OK` when every edit would be merged, `EXIT_VIOLATIONS` with one line
            per edit that would not be (or the one reason nothing would be), and
            `EXIT_CANNOT_CHECK` when the file is not in a session copy, the run's state is
            unavailable, or the copy breaks the edit rules. Either of the first two also carries
            a note for each callee whose contract changed in the canonical file (see
            `_changed_callee_notes`).
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
            f"the run's list of live sessions is not available for {info.canonical_file.name}"
        )
    try:
        snapshot = session_file.read_bytes()
        canonical = info.canonical_file.read_bytes()
    except OSError as error:
        return _cannot_check(f"could not read the files to compare: {error}")
    if check_edits(info.fork_base, snapshot, function):
        return _cannot_check(
            "this copy breaks the edit rules, so it would not be merged at all; run "
            "`avocado-validate edits` and fix what it reports first"
        )

    _, report = merge_copy(
        canonical=canonical,
        snapshot=snapshot,
        fork_base=info.fork_base,
        function=function,
        frozen_functions=frozen_functions_for(function, state.active),
    )
    label = f"rule 10 ({RULE_TITLES[10]})"
    if not report.merged:
        violation = Violation(
            10, f"none of your work would be merged: {report.reason}; rename what clashes"
        )
        return CheckResult(EXIT_VIOLATIONS, violations=[str(violation)])
    violations = [
        str(
            Violation(
                10,
                f"your edit to {entry} would be left out of the merge; the rest of your work "
                "still merges, so make sure your function verifies without that edit",
            )
        )
        for entry in report.dropped
    ]
    notes = _changed_callee_notes(
        function,
        snapshot=snapshot,
        canonical=canonical,
        changed_since_fork=report.changed_since_fork,
        dropped=report.dropped,
        canonical_file=info.canonical_file,
    )
    if violations:
        return CheckResult(EXIT_VIOLATIONS, violations=violations, notes=notes)
    return CheckResult(EXIT_OK, notes=[*notes, f"{label}: ok"])


def _changed_callee_notes(
    function: str,
    *,
    snapshot: bytes,
    canonical: bytes,
    changed_since_fork: list[str],
    dropped: list[str],
    canonical_file: Path,
) -> list[str]:
    """Return a note for each callee whose contract other sessions changed since the copy.

    The session's function is verified against its callees' contracts as they are in the
    canonical file when the session is merged, not as they are in the copy. A callee is noted
    when its contract changed in the canonical file since the copy was taken and the copy does
    not hold the new contract yet. One whose contract the session edited too is left to the
    violation that already names it.

    Args:
        function (str): The function the session works on.
        snapshot (bytes): The session's copy.
        canonical (bytes): The canonical file as it is now.
        changed_since_fork (list[str]): Functions whose contracts changed in the canonical file
            since the copy was taken (`MergeReport.changed_since_fork`).
        dropped (list[str]): The session's edits the merge would leave out.
        canonical_file (Path): The canonical source file, for its call graph.

    Returns:
        list[str]: One note per such callee, each ending with the callee's new signature and
            contract.
    """
    if not changed_since_fork:
        return []
    call_graph = get_call_graph(str(canonical_file))
    if function not in call_graph:
        return []
    callees = call_graph.get_callees(function).internal
    not_yet_in_copy = set(functions_with_changed_contracts(snapshot, canonical))
    notes = []
    for callee in changed_since_fork:
        if callee == function or callee not in callees or callee not in not_yet_in_copy:
            continue
        if any(entry.startswith(f"function:{callee} ") for entry in dropped):
            continue
        header = function_header(canonical, callee)
        contract = "" if header is None else "\n" + header.decode("utf-8", errors="replace")
        notes.append(
            f"note: another session changed the contract of {callee}, which {function} calls, "
            f"in the shared file since your copy was taken. {function} will be verified against "
            "the new contract when your session is merged, so replace your copy's contract for "
            f"{callee} with it, exactly as it is below, and make {function} verify against it:"
            f"{contract}"
        )
    return notes


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
    if check_edits(info.fork_base, snapshot, function):
        return _cannot_check(
            "this copy breaks the edit rules, so it would not be merged at all; run "
            "`avocado-validate edits` and fix what it reports first"
        )

    merged, report = merge_copy(
        canonical=canonical,
        snapshot=snapshot,
        fork_base=info.fork_base,
        function=function,
        frozen_functions=frozen_functions_for(function, state.active),
    )
    if not report.merged:
        return _cannot_check(f"this copy would not be merged: {report.reason}")
    notes = [f"note: this edit would not be merged: {entry}" for entry in report.dropped]
    if not report.carried:
        notes.append(
            f"{_REVERIFY_LABEL}: ok; no other function's definition would change, so nothing "
            "needs re-verifying"
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
            report.carried,
            call_graph=call_graph,
            candidates=rechecked_on_change(state.verified, state.unverified),
            exclude=function,
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
            str(
                Violation(
                    _REVERIFY_RULE,
                    f"{other} verified before this session and no longer verifies "
                    f"({verdicts[other]!s}) with your edits to "
                    f"{', '.join(_with_line(name, snapshot) for name in causes)}",
                )
            )
        )
    summary = ", ".join(f"{other} {verdicts[other]!s}" for other in affected)
    notes.append(f"re-verified {len(affected)} function(s): {summary}")
    if violations:
        notes.append(
            "Edits to other functions that break a verified function are discarded when this "
            "session is merged. Fix or undo them, then re-run avocado-run-cbmc and this check."
        )
        return CheckResult(EXIT_VIOLATIONS, violations=violations, notes=notes)
    notes.append(f"{_REVERIFY_LABEL}: ok")
    return CheckResult(EXIT_OK, notes=notes)


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


def _with_line(function: str, source: bytes) -> str:
    """Return `function` followed by the line its definition starts on in `source`.

    Args:
        function (str): A function name.
        source (bytes): The session's copy of the source file.

    Returns:
        str: `name (line N)`, or the bare name when the definition cannot be located.
    """
    line = function_line(source, function)
    if line is None:
        return function
    return f"{function} (line {line})"


if __name__ == "__main__":
    main()
