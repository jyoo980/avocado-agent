from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from loguru import logger

from proof_state.next_step import AgentDecision
from proof_state.proof_state import ProofState, WorkItem
from util.c_function_graph import CFunctionGraph
from util.function_specification import FunctionSpecification, LoopContract
from verification.cbmc_client import CbmcClient

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
_PROJECT_ROOT = Path(__file__).parent.parent

_MCP_SERVER_NAME = "avocado-tools"
_ALL_TOOLS = ",".join(
    f"mcp__{_MCP_SERVER_NAME}__{tool}"
    for tool in [
        "read_function",
        "read_spec",
        "write_spec",
        "run_cbmc",
        "get_callees",
        "get_callers",
        "get_counterexample",
        "finish",
    ]
)


def _load_prompt(filename: str) -> str:
    return (_PROMPTS_DIR / filename).read_text()


def _fn_to_dict(fn) -> dict:
    return {
        "name": fn.name,
        "file_path": str(fn.file_path),
        "start_line": fn.start_line,
        "end_line": fn.end_line,
    }


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


def _decision_from_dict(d: dict) -> AgentDecision:
    spec_data = d.get("spec") or {}
    spec = FunctionSpecification(
        preconditions=spec_data.get("preconditions", []),
        postconditions=spec_data.get("postconditions", []),
        assigns=spec_data.get("assigns", []),
        loop_contracts=[
            LoopContract(
                loop_id=lc["loop_id"],
                invariant=lc["invariant"],
                decreases=lc.get("decreases"),
                assigns=lc.get("assigns", []),
            )
            for lc in spec_data.get("loop_contracts", [])
        ],
    )
    return AgentDecision(
        type=d["decision"],
        spec=spec,
        backtrack_callee=d.get("backtrack_callee"),
        backtrack_hint=d.get("backtrack_hint"),
    )


class SpecAgent:
    def __init__(
        self,
        function_graph: CFunctionGraph,
        cbmc_client: CbmcClient,
        model: str = "claude-sonnet-4-6",
    ) -> None:
        self._graph = function_graph
        self._cbmc_client = cbmc_client
        self._model = model
        self._system_prompt = _load_prompt("system_prompt.txt")

    def run(self, work_item: WorkItem, proof_state: ProofState) -> AgentDecision:
        fn = work_item.function
        logger.info(f"[{fn.name}] Starting spec agent (hint={work_item.hint!r})")

        context = self._build_context(work_item, proof_state)
        initial_message = self._build_initial_message(work_item)

        with tempfile.TemporaryDirectory(prefix="avocado_agent_") as tmpdir:
            tmp = Path(tmpdir)
            ctx_file = tmp / "context.json"
            result_file = tmp / "result.json"
            mcp_cfg_file = tmp / "mcp_config.json"

            ctx_file.write_text(json.dumps(context))
            mcp_cfg_file.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            _MCP_SERVER_NAME: {
                                "command": sys.executable,
                                "args": ["-m", "agent.mcp_server"],
                                "env": {
                                    "PYTHONPATH": str(_PROJECT_ROOT),
                                    "AVOCADO_CONTEXT_FILE": str(ctx_file),
                                    "AVOCADO_RESULT_FILE": str(result_file),
                                },
                            }
                        }
                    }
                )
            )

            # stdout/stderr are inherited so the user can monitor claude's
            # output and MCP server logs in real time.
            subprocess.run(
                [
                    "claude",
                    "-p",
                    initial_message,
                    "--system-prompt",
                    self._system_prompt,
                    "--mcp-config",
                    str(mcp_cfg_file),
                    "--model",
                    self._model,
                    "--allowedTools",
                    _ALL_TOOLS,
                    "--max-turns",
                    "30",
                ],
                timeout=600,
            )

            if result_file.exists():
                decision = _decision_from_dict(json.loads(result_file.read_text()))
                logger.info(f"[{fn.name}] Agent decision: {decision.type}")
                return decision

        logger.warning(f"[{fn.name}] Agent exited without calling finish() — forcing ASSUME")
        return AgentDecision(type="ASSUME", spec=FunctionSpecification())

    def _build_context(self, work_item: WorkItem, proof_state: ProofState) -> dict:
        fn = work_item.function
        all_fns = {f.name: _fn_to_dict(f) for f in self._graph.all_functions()}
        specs = {
            f.name: _spec_to_dict(s)
            for f in self._graph.all_functions()
            if (s := proof_state.get_specification(f)) is not None
        }
        return {
            "function": _fn_to_dict(fn),
            "callees": [_fn_to_dict(c) for c in self._graph.get_callees(fn)],
            "callers": [_fn_to_dict(c) for c in self._graph.get_callers(fn)],
            "all_functions": all_fns,
            "specs": specs,
            "cbmc_config": {
                "output_dir": str(self._cbmc_client._output_dir),
                "unwind_default": self._cbmc_client._unwind_default,
                "timeout_sec": self._cbmc_client._timeout_sec,
            },
        }

    def _build_initial_message(self, work_item: WorkItem) -> str:
        fn = work_item.function
        spec_template = _load_prompt("spec_generation_prompt.txt")
        backtrack_section = ""
        if work_item.hint:
            backtrack_template = _load_prompt("backtrack_hint_prompt.txt")
            backtrack_section = backtrack_template.format(
                function_name=fn.name,
                hint=work_item.hint,
            )
        return spec_template.format(
            function_name=fn.name,
            backtrack_hint_section=backtrack_section,
        )
