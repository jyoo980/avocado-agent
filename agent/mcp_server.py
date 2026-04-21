"""MCP server exposing CBMC spec-generation tools for avocado-agent.

Started by Claude Code as a stdio subprocess for each function being processed.
Context is passed via AVOCADO_CONTEXT_FILE; the finish() decision is written to
AVOCADO_RESULT_FILE so spec_agent.py can read it after the claude process exits.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

from loguru import logger
from mcp.server.fastmcp import FastMCP

# Log to stderr only — stdout is the MCP stdio protocol channel.
logger.remove()
logger.add(
    sys.stderr,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
    level="DEBUG",
)

# ---------------------------------------------------------------------------
# Load context at startup (skipped when env vars are absent, e.g. in tests)
# ---------------------------------------------------------------------------

_ctx_file: Path | None = (
    Path(os.environ["AVOCADO_CONTEXT_FILE"]) if os.environ.get("AVOCADO_CONTEXT_FILE") else None
)
_result_file: Path | None = (
    Path(os.environ["AVOCADO_RESULT_FILE"]) if os.environ.get("AVOCADO_RESULT_FILE") else None
)
_ctx: dict = json.loads(_ctx_file.read_text()) if _ctx_file is not None else {}

# ---------------------------------------------------------------------------
# Deserialise helpers
# ---------------------------------------------------------------------------

from util.c_function import CFunction
from util.function_specification import FunctionSpecification, LoopContract
from verification.cbmc_client import CbmcClient
from verification.verification_input import VerificationInput
from verification.verification_result import VerificationStatus


def _fn_from_dict(d: dict) -> CFunction:
    return CFunction(
        name=d["name"],
        file_path=Path(d["file_path"]),
        start_line=d["start_line"],
        end_line=d["end_line"],
    )


def _spec_from_dict(d: dict | None) -> FunctionSpecification | None:
    if d is None:
        return None
    return FunctionSpecification(
        preconditions=d.get("preconditions", []),
        postconditions=d.get("postconditions", []),
        assigns=d.get("assigns", []),
        loop_contracts=[
            LoopContract(
                loop_id=lc["loop_id"],
                invariant=lc["invariant"],
                decreases=lc.get("decreases"),
                assigns=lc.get("assigns", []),
            )
            for lc in d.get("loop_contracts", [])
        ],
    )


def _spec_to_dict(spec: FunctionSpecification) -> dict:
    return {
        "preconditions": spec.preconditions,
        "postconditions": spec.postconditions,
        "assigns": spec.assigns,
        "loop_contracts": [
            {
                "loop_id": lc.loop_id,
                "invariant": lc.invariant,
                "decreases": lc.decreases,
                "assigns": lc.assigns,
            }
            for lc in spec.loop_contracts
        ],
    }


# ---------------------------------------------------------------------------
# Reconstruct objects from context (None when running outside MCP context)
# ---------------------------------------------------------------------------

_current_fn: Optional[CFunction] = _fn_from_dict(_ctx["function"]) if _ctx.get("function") else None
_callees: list[CFunction] = [_fn_from_dict(c) for c in _ctx.get("callees", [])]
_callers: list[CFunction] = [_fn_from_dict(c) for c in _ctx.get("callers", [])]
_all_functions: dict[str, CFunction] = {
    name: _fn_from_dict(d) for name, d in _ctx.get("all_functions", {}).items()
}
_specs: dict[str, FunctionSpecification] = {
    name: spec
    for name, d in _ctx.get("specs", {}).items()
    if (spec := _spec_from_dict(d)) is not None
}

_cbmc_cfg = _ctx.get("cbmc_config", {})
_cbmc_client: Optional[CbmcClient] = (
    CbmcClient(
        output_dir=Path(_cbmc_cfg["output_dir"]),
        unwind_default=_cbmc_cfg["unwind_default"],
        timeout_sec=_cbmc_cfg["timeout_sec"],
    )
    if _cbmc_cfg
    else None
)

# ---------------------------------------------------------------------------
# Mutable per-session state
# ---------------------------------------------------------------------------

_pending_spec: Optional[FunctionSpecification] = None
_last_cbmc_result = None  # VerificationResult | None

# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

mcp = FastMCP("avocado-tools")


@mcp.tool()
def read_function(function_name: str, include_line_numbers: bool = False) -> str:
    """Read the C source code of a function."""
    fn = _all_functions.get(function_name)
    if fn is None:
        return f"Error: function '{function_name}' not found in the call graph."
    logger.debug(f"[{_current_fn.name}] read_function({function_name})")
    return fn.get_source_code(include_line_numbers=include_line_numbers)


@mcp.tool()
def read_spec(function_name: str) -> str:
    """Read the existing CBMC specification for a function."""
    if function_name not in _all_functions:
        return f"Error: function '{function_name}' not found."
    spec = _specs.get(function_name)
    if spec is None:
        return f"No spec exists yet for '{function_name}'."
    logger.debug(f"[{_current_fn.name}] read_spec({function_name})")
    return spec.to_display_string()


@mcp.tool()
def write_spec(
    function_name: str,
    preconditions: list[str],
    postconditions: list[str],
    assigns: list[str],
    loop_contracts: list[dict] | None = None,
) -> str:
    """Propose a CBMC specification for the current function."""
    global _pending_spec
    if function_name not in _all_functions:
        return f"Error: function '{function_name}' not found."
    if function_name != _current_fn.name:
        return f"Error: you can only write a spec for the current function ('{_current_fn.name}')."

    parsed_loops: list[LoopContract] = []
    for lc in loop_contracts or []:
        parsed_loops.append(
            LoopContract(
                loop_id=lc["loop_id"],
                invariant=lc["invariant"],
                decreases=lc.get("decreases"),
                assigns=lc.get("assigns", []),
            )
        )

    _pending_spec = FunctionSpecification(
        preconditions=preconditions,
        postconditions=postconditions,
        assigns=assigns,
        loop_contracts=parsed_loops,
    )
    logger.info(f"[{_current_fn.name}] Spec written:\n{_pending_spec.to_display_string()}")
    return f"Spec for '{function_name}' written successfully. Call run_cbmc to verify it."


@mcp.tool()
def run_cbmc(function_name: str, unwind: int = 5) -> str:
    """Run CBMC verification on the current function using the pending spec."""
    global _last_cbmc_result
    if _pending_spec is None:
        return "Error: no spec written yet. Call write_spec first."
    if function_name != _current_fn.name:
        return f"Error: can only verify the current function ('{_current_fn.name}')."

    callee_specs: dict[str, FunctionSpecification] = {
        callee.name: _specs[callee.name] for callee in _callees if callee.name in _specs
    }

    vinput = VerificationInput(
        function=_current_fn,
        spec=_pending_spec,
        callee_specs=callee_specs,
        unwind=unwind,
    )
    logger.info(f"[{_current_fn.name}] Running CBMC (unwind={unwind})")
    result = _cbmc_client.verify(vinput)
    _last_cbmc_result = result

    status_str = result.status.value.upper()
    details = ""
    if result.failure_descriptions:
        details = "\nFailures:\n" + "\n".join(f"  - {d}" for d in result.failure_descriptions)
    if result.counterexample:
        details += f"\nCounterexample: {result.counterexample.failing_property} @ {result.counterexample.failure_location}"

    hint = ""
    if result.status == VerificationStatus.FAILED:
        raw = result.raw_output.lower()
        if "assignability" in raw:
            hint = "\nHint: An assignability check failed — consider broadening the assigns clause."
        elif "unwinding" in raw:
            hint = f"\nHint: Unwinding assertion failed — try run_cbmc with a higher unwind value (current: {unwind})."

    return f"CBMC result: {status_str}{details}{hint}"


@mcp.tool()
def get_callees(function_name: str) -> str:
    """List the functions called by a given function."""
    if function_name not in _all_functions:
        return f"Error: function '{function_name}' not found."
    if function_name == _current_fn.name:
        if not _callees:
            return f"'{function_name}' has no callees (it is a leaf function)."
        return ", ".join(c.name for c in _callees)
    return f"Callee info for '{function_name}' is not available in this context."


@mcp.tool()
def get_callers(function_name: str) -> str:
    """List the functions that call a given function."""
    if function_name not in _all_functions:
        return f"Error: function '{function_name}' not found."
    if function_name == _current_fn.name:
        if not _callers:
            return f"'{function_name}' has no callers (it is a root function)."
        return ", ".join(c.name for c in _callers)
    return f"Caller info for '{function_name}' is not available in this context."


@mcp.tool()
def get_counterexample(function_name: str) -> str:
    """Get the counterexample trace from the most recent CBMC run."""
    if function_name != _current_fn.name:
        return f"Error: only the current function ('{_current_fn.name}') has a counterexample available."
    if _last_cbmc_result is None:
        return "No CBMC run has been performed yet."
    if _last_cbmc_result.counterexample is None:
        return "No counterexample available (verification may have succeeded or produced no trace)."
    return _last_cbmc_result.counterexample.to_display_string()


@mcp.tool()
def finish(
    decision: str,
    backtrack_callee: str | None = None,
    backtrack_hint: str | None = None,
) -> str:
    """Signal completion with ACCEPT, ASSUME, or BACKTRACK.

    ACCEPT: the pending spec passed CBMC verification.
    ASSUME: accept the spec without full verification (best effort).
    BACKTRACK: a callee's spec is too weak; re-queue it with a hint.
    """
    spec = _pending_spec or FunctionSpecification()
    result = {
        "decision": decision,
        "spec": _spec_to_dict(spec),
        "backtrack_callee": backtrack_callee,
        "backtrack_hint": backtrack_hint,
    }
    if _result_file is not None:
        _result_file.write_text(json.dumps(result))
    logger.info(f"[{_current_fn.name}] Finished: {decision}")
    return f"Finished with decision: {decision}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
