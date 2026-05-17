"""Mutation-testing orchestration for CBMC-annotated C functions.

Given a function with a CBMC contract, generate body mutants (operator swaps via
`eval.mutants.mutate_function.get_mutants`), run CBMC on each, and report what
fraction the spec "kills." A mutant is killed iff CBMC fails on it; a surviving
mutant indicates the spec is too weak to catch that perturbation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import starmap
from pathlib import Path

from eval.mutants.mutate_function import Mutant, get_mutants
from eval.mutants.util import check_expected_cbmc_return_code
from tools.run_cbmc import run_cbmc


@dataclass(frozen=True)
class MutantVerificationResult:
    """Verification result for a single mutant.

    Attributes:
        mutant (Mutant): The mutant to verify.
        killed (bool): True iff this mutant was killed.
        returncode (int): The return code of the CBMC process used to verify this mutant.
    """

    mutant: Mutant
    killed: bool
    returncode: int


@dataclass(frozen=True)
class MutationScore:
    """Mutation-testing related statistics for one function.

    Attributes
    ----------
        file (str): The file in which this function is declared.
        function (str): The name of this function.
        num_mutants (int): The total number of mutants for this function.
        num_killed (int): The number of killed mutants.
        num_survived (int): The number of surviving mutants.
        kill_score (float): The kill score (i.e., killed / num_mutants).
        results (list[MutantVerificationResult]): The verification result for each mutant.
    """

    file: str
    function: str
    num_mutants: int
    num_killed: int
    num_survived: int
    kill_score: float
    results: list[MutantVerificationResult] = field(default_factory=list)

    def summary(self) -> dict[str, str | int | float]:
        """Return a summary of this mutation score.

        Returns:
            dict[str, str | int | float]: A summary of this mutation score.
        """
        return {
            "kind": "mutation_summary",
            "file": self.file,
            "function": self.function,
            "total": self.num_mutants,
            "killed": self.num_killed,
            "survived": self.num_survived,
            "kill_score": f"{self.kill_score:.4f}",
        }


def generate_mutants_and_compute_score(
    file_path: str,
    target_function: str,
    *,
    include_dirs: list[str] | None = None,
    workspace: Path | None = None,
    keep_artifacts: bool = False,
) -> MutationScore | None:
    """Score body-mutation kill rate for `target_function` in `file_path`.

    Mutant `.c` files are written next to the original source by default to simplify compilation
    and instrumentation with CBMC. Mutants are removed unless keep_artifacts is set to `True`.

    This function returns None if the original, unmutated function does not verify in the first
    place.

    Args:
        file_path (str): Path to the C source defining the function.
        target_function (str): The function for which to generate mutants.
        include_dirs (list[str] | None): Directories forwarded to `run_cbmc()` as `-I` flags
            for both the original-function verification and every mutant run.
        workspace (Path | None): Directory to write mutant files into. Defaults to the
            source file's directory.
        keep_artifacts (bool): When True, mutant `.c` files are kept for inspection.

    Returns:
        MutationScore | None: Aggregated counts plus per-mutant verification results, or None if
            the unmutated function does not verify.
    """
    source_path = Path(file_path).resolve()
    workspace = workspace or source_path.parent
    workspace.mkdir(parents=True, exist_ok=True)

    _, returncode = run_cbmc(target_function, file_path, include_dirs=include_dirs)
    check_expected_cbmc_return_code(returncode)
    if returncode != 0:
        return None

    mutants = get_mutants(str(source_path), target_function)
    paths_to_mutants = {
        _get_path_for_mutated_source(workspace, source_path, i): mutant
        for i, mutant in enumerate(mutants)
    }
    mutant_vresults: list[MutantVerificationResult] = []
    try:
        mutant_vresults = list(
            starmap(
                lambda path, mutant: _verify_mutant(path, mutant, include_dirs),
                paths_to_mutants.items(),
            )
        )
    finally:
        if not keep_artifacts:
            for path in paths_to_mutants:
                path.unlink(missing_ok=True)

    total = len(mutant_vresults)
    killed = sum(1 for r in mutant_vresults if r.killed)
    survived = total - killed
    kill_rate = (killed / total) if total else 0.0
    return MutationScore(
        file=str(source_path),
        function=target_function,
        num_mutants=total,
        num_killed=killed,
        num_survived=survived,
        kill_score=round(kill_rate, 4),
        results=mutant_vresults,
    )


def _verify_mutant(
    path_to_write_mutant: Path,
    mutant: Mutant,
    include_dirs: list[str] | None,
) -> MutantVerificationResult:
    """Return the result of verifying a mutant.

    Args:
        path_to_write_mutant (Path): The path to which the mutated source is written.
        mutant (Mutant): The mutant.
        include_dirs (list[str] | None): Include directories forwarded to `run_cbmc()`.

    Returns:
        MutantVerificationResult: The result of verifying a mutant.
    """
    path_to_write_mutant.write_text(mutant.mutant_source, encoding="utf-8")
    _, returncode = run_cbmc(
        function_to_verify=mutant.function,
        file_containing_function_to_verify=str(path_to_write_mutant),
        include_dirs=include_dirs,
    )
    return MutantVerificationResult(mutant, killed=returncode != 0, returncode=returncode)


def _get_path_for_mutated_source(
    workspace_path: Path, path_to_original_source: Path, index: int
) -> Path:
    """Return the path to which to write a mutated source file.

    For example, given the path `/app/test/data/foo.c`, return `/app/test/data/foo__mutant_1.c`

    Args:
        workspace_path (Path): The directory under which mutation testing occurs.
        path_to_original_source (Path): The path to the original source file.
        index (int): The index of the mutant, used as a identifier for the mutant source path.

    Returns:
        Path: The path to which to write a mutated source file.
    """
    return (
        workspace_path
        / f"{path_to_original_source.stem}__mutant_{index}{path_to_original_source.suffix}"
    )
