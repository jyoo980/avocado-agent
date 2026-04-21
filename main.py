from __future__ import annotations

import argparse
import sys
from collections import deque
from pathlib import Path

from loguru import logger

from agent.spec_agent import SpecAgent
from proof_state.proof_state import FunctionStatus, ProofState
from proof_state.proof_state_stepper import ProofStateStepper
from util.c_function_graph import CFunctionGraph
from verification.cbmc_client import CbmcClient
from verification.verification_cache import VerificationCache


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Agentic CBMC specification generator for C programs"
    )
    parser.add_argument(
        "--input-path",
        type=Path,
        required=True,
        help="Path to the C repository to verify",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./output"),
        help="Directory to write annotated C files with verified specs",
    )
    parser.add_argument(
        "--model",
        default="claude-sonnet-4-6",
        help="Claude model to use for spec generation",
    )
    parser.add_argument(
        "--unwind",
        type=int,
        default=5,
        help="Default loop unwind bound for CBMC",
    )
    parser.add_argument(
        "--timeout-sec",
        type=int,
        default=120,
        help="Per-function CBMC timeout in seconds",
    )
    parser.add_argument(
        "--disable-verifier-cache",
        action="store_true",
        help="Disable disk cache for CBMC results",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("./.avocado_cache"),
        help="Directory for disk caches",
    )
    parser.add_argument(
        "--skip-succeeded",
        action="store_true",
        help="Skip functions that already have a verified spec in the cache",
    )
    return parser.parse_args()


def setup_logging() -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
        level="INFO",
    )


def write_results(proof_state: ProofState, output_dir: Path) -> None:
    """Write a summary of all verified specs to output_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_lines: list[str] = ["# Verification Summary\n\n"]

    statuses = proof_state.all_statuses()
    specs = proof_state.all_specs()

    succeeded = [(fn, specs.get(fn)) for fn, s in statuses.items() if s == FunctionStatus.SUCCEEDED]
    assumed = [(fn, specs.get(fn)) for fn, s in statuses.items() if s == FunctionStatus.ASSUMED]
    unprocessed = [fn for fn, s in statuses.items() if s == FunctionStatus.UNPROCESSED]

    summary_lines.append(f"## Results\n\n")
    summary_lines.append(f"- Verified (CBMC): {len(succeeded)}\n")
    summary_lines.append(f"- Assumed (no proof): {len(assumed)}\n")
    summary_lines.append(f"- Unprocessed: {len(unprocessed)}\n\n")

    if succeeded:
        summary_lines.append("## Verified Functions\n\n")
        for fn, spec in sorted(succeeded, key=lambda x: x[0].name):
            summary_lines.append(f"### `{fn.name}` ({fn.file_path.name}:{fn.start_line})\n\n")
            if spec:
                summary_lines.append(f"```\n{spec.to_display_string()}\n```\n\n")

    if assumed:
        summary_lines.append("## Assumed Functions (not CBMC-verified)\n\n")
        for fn, spec in sorted(assumed, key=lambda x: x[0].name):
            summary_lines.append(f"### `{fn.name}` ({fn.file_path.name}:{fn.start_line})\n\n")
            if spec:
                summary_lines.append(f"```\n{spec.to_display_string()}\n```\n\n")

    (output_dir / "summary.md").write_text("".join(summary_lines))
    logger.info(f"Results written to {output_dir}/summary.md")


def run_supervisor(
    input_path: Path,
    output_dir: Path,
    model: str,
    unwind: int,
    timeout_sec: int,
    disable_verifier_cache: bool,
    cache_dir: Path,
) -> ProofState:
    graph = CFunctionGraph(input_path)
    functions = graph.topological_order()

    if not functions:
        logger.error("No functions found — nothing to verify.")
        sys.exit(1)

    logger.info(f"Found {len(functions)} functions. Processing leaves-first.")

    cache = None if disable_verifier_cache else VerificationCache(cache_dir / "verifier")
    cbmc_client = CbmcClient(
        output_dir=output_dir,
        cache=cache,
        unwind_default=unwind,
        timeout_sec=timeout_sec,
    )
    agent = SpecAgent(
        function_graph=graph,
        cbmc_client=cbmc_client,
        model=model,
    )
    stepper = ProofStateStepper(function_graph=graph)

    initial_state = ProofState(functions)
    worklist: deque[ProofState] = deque([initial_state])
    seen: set[ProofState] = {initial_state}

    final_state: ProofState = initial_state

    while worklist:
        state = worklist.popleft()

        if state.is_workstack_empty():
            final_state = state
            continue

        work_item = state.peek_workstack()
        fn = work_item.function

        logger.info(f"Processing '{fn.name}' | {state.summary()}")

        decision = agent.run(work_item, state)
        next_state = stepper.transition(state, decision)

        if next_state not in seen:
            seen.add(next_state)
            worklist.append(next_state)

        if next_state.is_workstack_empty():
            final_state = next_state

    if cache is not None:
        cache.close()

    return final_state


def main() -> None:
    setup_logging()
    args = parse_args()

    final_state = run_supervisor(
        input_path=args.input_path,
        output_dir=args.output_dir,
        model=args.model,
        unwind=args.unwind,
        timeout_sec=args.timeout_sec,
        disable_verifier_cache=args.disable_verifier_cache,
        cache_dir=args.cache_dir,
    )

    write_results(final_state, args.output_dir)

    statuses = final_state.all_statuses()
    n_succeeded = sum(1 for s in statuses.values() if s == FunctionStatus.SUCCEEDED)
    n_assumed = sum(1 for s in statuses.values() if s == FunctionStatus.ASSUMED)
    logger.info(f"Done. {n_succeeded} verified, {n_assumed} assumed.")


if __name__ == "__main__":
    main()
