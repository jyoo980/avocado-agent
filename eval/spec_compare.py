#!/usr/bin/env -S uv run --quiet python3

"""Ground-truth spec comparison (M6.1 + M3.4).

Two complementary metrics:

* `compare_via_verification` (M6.1 lite) — run CBMC on each version of the function under
  its own contract and classify the outcomes: {both_verify, only_a_verifies, only_b_verifies,
  neither}. Cheap and always applicable.

* `compare_preconditions` (M3.4) — generate a harness file with two assertion functions:
    void _check_a_implies_b(<params>) { __CPROVER_assume(<a_pre>); __CPROVER_assert(<b_pre>); }
    void _check_b_implies_a(<params>) { __CPROVER_assume(<b_pre>); __CPROVER_assert(<a_pre>); }
  Run CBMC on each and combine into one of {equivalent, a_stronger, b_stronger, incomparable}.

Postcondition implication is harder because `__CPROVER_old` and `__CPROVER_return_value`
are bound to a specific call. That is a follow-up — for now `compare_via_verification`
covers the postcondition side via end-to-end CBMC outcomes.

Usage:
    eval/spec_compare.py --function <NAME> --file-a <PATH> --file-b <PATH> [--jsonl PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.run_cbmc import run_cbmc
from tools.spec_extract import FunctionSpec, extract_function_spec


@dataclass(frozen=True)
class VerificationComparison:
    """Outcome of running CBMC on each version of the function."""

    function: str
    file_a: str
    file_b: str
    a_passes: bool
    b_passes: bool
    classification: str  # "both_verify" | "only_a_verifies" | "only_b_verifies" | "neither"


@dataclass(frozen=True)
class PreconditionComparison:
    """Outcome of harness-based precondition implication checking."""

    function: str
    file_a: str
    file_b: str
    a_implies_b: bool
    b_implies_a: bool
    classification: str  # "equivalent" | "a_stronger" | "b_stronger" | "incomparable"
    harness_path: str | None = None


def compare_via_verification(
    file_a: str, file_b: str, function_name: str
) -> VerificationComparison:
    """Run CBMC on both versions of `function_name` and classify the outcomes (M6.1 lite).

    Args:
        file_a (str): Path to the first version of the C source.
        file_b (str): Path to the second version of the C source.
        function_name (str): The function present in both files.

    Returns:
        VerificationComparison: Per-side pass flags plus a four-way classification.
    """
    _, rc_a = run_cbmc(
        function_to_verify=function_name,
        file_containing_function_to_verify=file_a,
    )
    _, rc_b = run_cbmc(
        function_to_verify=function_name,
        file_containing_function_to_verify=file_b,
    )
    a_passes = rc_a == 0
    b_passes = rc_b == 0
    if a_passes and b_passes:
        classification = "both_verify"
    elif a_passes:
        classification = "only_a_verifies"
    elif b_passes:
        classification = "only_b_verifies"
    else:
        classification = "neither"
    return VerificationComparison(
        function=function_name,
        file_a=file_a,
        file_b=file_b,
        a_passes=a_passes,
        b_passes=b_passes,
        classification=classification,
    )


def compare_preconditions(
    file_a: str,
    file_b: str,
    function_name: str,
    workspace: Path | None = None,
    keep_artifacts: bool = False,
) -> PreconditionComparison | None:
    """Compare preconditions via a harness (M3.4).

    Args:
        file_a (str): Path to the first C version.
        file_b (str): Path to the second C version.
        function_name (str): The function present in both files.
        workspace (Path | None): Where to drop the harness file.
        keep_artifacts (bool): When True, the harness file is preserved.

    Returns:
        PreconditionComparison | None: The comparison result, or None when either side has
            no `__CPROVER_requires` clauses to compare.
    """
    spec_a = extract_function_spec(file_a, function_name)
    spec_b = extract_function_spec(file_b, function_name)
    if spec_a is None or spec_b is None:
        return None
    if not spec_a.requires or not spec_b.requires:
        return None

    workspace = workspace or Path(file_a).resolve().parent
    workspace.mkdir(parents=True, exist_ok=True)
    harness_source = build_precondition_harness(file_a, spec_a, spec_b)
    harness_path = workspace / f"{Path(file_a).stem}__pre_implication_harness.c"
    harness_path.write_text(harness_source, encoding="utf-8")

    try:
        _, rc_a_to_b = run_cbmc(
            function_to_verify="_check_a_implies_b",
            file_containing_function_to_verify=str(harness_path),
        )
        _, rc_b_to_a = run_cbmc(
            function_to_verify="_check_b_implies_a",
            file_containing_function_to_verify=str(harness_path),
        )
    finally:
        if not keep_artifacts:
            harness_path.unlink(missing_ok=True)

    a_implies_b = rc_a_to_b == 0
    b_implies_a = rc_b_to_a == 0
    if a_implies_b and b_implies_a:
        classification = "equivalent"
    elif a_implies_b:
        classification = "a_stronger"  # a's pre admits ⊆ inputs of b's pre
    elif b_implies_a:
        classification = "b_stronger"
    else:
        classification = "incomparable"
    return PreconditionComparison(
        function=function_name,
        file_a=file_a,
        file_b=file_b,
        a_implies_b=a_implies_b,
        b_implies_a=b_implies_a,
        classification=classification,
        harness_path=str(harness_path) if keep_artifacts else None,
    )


def build_precondition_harness(base_file: str, spec_a: FunctionSpec, spec_b: FunctionSpec) -> str:
    """Return the harness source: original file + two `_check_*_implies_*` functions appended.

    The original file is included verbatim so any type definitions (typedefs, structs,
    headers) referenced by the function's parameters resolve. The harness functions take
    the same parameter list.

    Args:
        base_file (str): Path to the source whose contents form the prefix of the harness.
        spec_a (FunctionSpec): The "a" side of the comparison (its preconditions are
            assumed in `_check_a_implies_b`).
        spec_b (FunctionSpec): The "b" side of the comparison.

    Returns:
        str: The full harness source, ready to write to disk and verify with CBMC.
    """
    base_source = Path(base_file).read_text(encoding="utf-8")
    param_decl = ", ".join(p.declarator_text for p in spec_a.parameters) or "void"
    pre_a = " && ".join(f"({clause})" for clause in spec_a.requires)
    pre_b = " && ".join(f"({clause})" for clause in spec_b.requires)
    harness = (
        f"\n\n"
        f"void _check_a_implies_b({param_decl}) {{\n"
        f"    __CPROVER_assume({pre_a});\n"
        f'    __CPROVER_assert({pre_b}, "a_pre does not imply b_pre");\n'
        f"}}\n\n"
        f"void _check_b_implies_a({param_decl}) {{\n"
        f"    __CPROVER_assume({pre_b});\n"
        f'    __CPROVER_assert({pre_a}, "b_pre does not imply a_pre");\n'
        f"}}\n"
    )
    return base_source + harness


def main() -> int:
    """CLI entry point: compare two contract versions of the same function and emit JSONL.

    Returns:
        int: 0 on success.
    """
    parser = argparse.ArgumentParser(
        description="Compare two CBMC contract versions of the same function."
    )
    parser.add_argument("--function", required=True)
    parser.add_argument("--file-a", required=True, dest="file_a")
    parser.add_argument("--file-b", required=True, dest="file_b")
    parser.add_argument(
        "--mode",
        choices=["verification", "precondition", "both"],
        default="both",
        help="Which comparison(s) to run.",
    )
    parser.add_argument("--jsonl", default=None)
    parser.add_argument("--keep-artifacts", action="store_true")
    args = parser.parse_args()

    output_lines: list[str] = []
    if args.mode in ("verification", "both"):
        v = compare_via_verification(args.file_a, args.file_b, args.function)
        output_lines.append(json.dumps({"kind": "verification_comparison", **asdict(v)}))
    if args.mode in ("precondition", "both"):
        p = compare_preconditions(
            args.file_a,
            args.file_b,
            args.function,
            keep_artifacts=args.keep_artifacts,
        )
        if p is None:
            output_lines.append(
                json.dumps(
                    {
                        "kind": "precondition_comparison",
                        "function": args.function,
                        "file_a": args.file_a,
                        "file_b": args.file_b,
                        "skipped": "missing_requires",
                    }
                )
            )
        else:
            output_lines.append(json.dumps({"kind": "precondition_comparison", **asdict(p)}))

    body = "\n".join(output_lines) + "\n"
    if args.jsonl:
        Path(args.jsonl).write_text(body, encoding="utf-8")
    else:
        sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
