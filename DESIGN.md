# avocado-agent: Agentic CBMC Specification Generator

## Context

CBMC is a bounded model checker for C that verifies programs against formal contracts (`__CPROVER_requires`, `__CPROVER_ensures`, `__CPROVER_assigns`, `__CPROVER_loop_invariant`). Writing these contracts by hand is tedious and error-prone. This system automates contract generation using an LLM agent that can read source, propose specs, invoke CBMC, parse failures, and revise — starting from leaf functions and working toward callers.

The leaf-first order matters: CBMC's `--replace-call-with-contract` flag lets callers treat verified callee contracts as black-box abstractions, so callee specs must be verified before callers can use them compositionally.

---

## Architecture Overview

**Single Claude Code agent per function**, orchestrated by a plain-Python supervisor that manages a global workstack and backtracking logic.

For each function, the supervisor invokes the `claude` CLI as a subprocess. Claude Code is given a system prompt (CBMC domain knowledge) and an MCP server (`agent/mcp_server.py`) that exposes the 8 tools below. Claude Code handles the agentic tool-use loop natively — the Python layer only serializes context in, and reads a decision JSON out.

---

## Module Structure

```
avocado-agent/
├── main.py                            # CLI: --input-path, --output-dir, --unwind, etc.
├── pyproject.toml
├── Dockerfile                         # ubuntu:24.04 + cbmc (apt) + python 3.13 (uv)
│
├── util/
│   ├── c_function.py                  # CFunction dataclass (name, file, line range)
│   ├── c_function_graph.py            # CFunctionGraph (networkx DiGraph, topo sort)
│   ├── function_specification.py      # FunctionSpecification + LoopContract dataclasses
│   ├── tree_sitter_util.py            # C source parsing via tree-sitter
│   └── function_util.py              # Inject specs into C source files
│
├── verification/
│   ├── cbmc_client.py                 # goto-cc → goto-instrument → cbmc pipeline
│   ├── cbmc_output_parser.py          # --xml-ui XML parser → VerificationResult
│   ├── verification_input.py          # VerificationInput (function + spec + callee specs)
│   ├── verification_result.py         # VerificationResult + VerificationStatus enum
│   └── verification_cache.py          # diskcache keyed on hash(VerificationInput)
│
├── proof_state/
│   ├── proof_state.py                 # ProofState (immutable, hashable) + WorkItem
│   ├── proof_state_stepper.py         # Transitions: ACCEPT / ASSUME / BACKTRACK
│   └── next_step.py                   # AgentDecision dataclass
│
├── agent/
│   ├── spec_agent.py                  # Serialises context → invokes claude CLI → reads decision
│   └── mcp_server.py                  # FastMCP server: 8 tools + in-process session state
│
└── prompts/
    ├── system_prompt.txt              # CBMC domain knowledge (~15K tokens)
    ├── spec_generation_prompt.txt     # Initial user message template
    └── backtrack_hint_prompt.txt      # Injected when function is revisited after backtrack
```

---

## Agent Design

### Invocation (`spec_agent.py`)

`SpecAgent.run()` performs three steps:

1. **Serialise context** — dumps a JSON snapshot to a temp file containing the current function, its callees/callers, all known specs, and CBMC config.
2. **Invoke claude** — runs `claude -p <prompt> --system-prompt <system> --mcp-config <config> --model <model> --allowedTools <tools> --max-turns 30` as a subprocess with inherited stdout/stderr (so the user sees Claude's output live).
3. **Read decision** — after the subprocess exits, reads the result JSON written by the `finish` tool and returns an `AgentDecision`.

### MCP Server (`mcp_server.py`)

Launched by Claude Code as a stdio subprocess once per function. Reads the context JSON at startup and maintains mutable in-process state across tool calls (`_pending_spec`, `_last_cbmc_result`).

The server uses `FastMCP` from the `mcp` package. Tools are regular Python functions decorated with `@mcp.tool()`.

### Tool Set

| Tool | Purpose |
|------|---------|
| `read_function(name, include_line_numbers)` | Return source of named function |
| `read_spec(name)` | Return current spec for function (or null) |
| `write_spec(name, preconditions, postconditions, assigns, loop_contracts)` | Propose a spec (does not run CBMC) |
| `run_cbmc(name, unwind)` | Run CBMC, return status + failure descriptions |
| `get_callees(name)` | Return names of functions this function calls |
| `get_callers(name)` | Return names of functions that call this function |
| `get_counterexample(name)` | Return structured trace from last CBMC run |
| `finish(decision, backtrack_callee?, backtrack_hint?)` | Signal done: ACCEPT / ASSUME / BACKTRACK; writes result JSON |

### FunctionSpecification schema (for `write_spec`):

```python
@dataclass
class LoopContract:
    loop_id: int           # 1-indexed loop number in function
    invariant: str         # expression for __CPROVER_loop_invariant
    decreases: str | None  # expression for __CPROVER_decreases
    assigns: list[str]     # loop-level write set

@dataclass
class FunctionSpecification:
    preconditions: list[str]   # __CPROVER_requires expressions
    postconditions: list[str]  # __CPROVER_ensures expressions (may use __CPROVER_return_value, __CPROVER_old)
    assigns: list[str]         # function-level __CPROVER_assigns
    loop_contracts: list[LoopContract]
```

---

## State Machine (per function)

```
UNPROCESSED
    │
    ▼
GENERATING_SPEC ──► VERIFYING ──────────► SUCCEEDED
                         │
                         ▼
                    REPAIRING (up to N attempts)
                         │
                         ▼
                  DECIDING_NEXT_STEP
                    /           \
               ASSUMED       BACKTRACK_TO_CALLEE ──► (push callee, retry)
```

`ProofState` is **immutable and hashable**. The supervisor maintains a `deque[ProofState]` and a `seen: set[ProofState]` for cycle detection.

---

## CBMC Verification Pipeline

For each function, CBMC verification runs as a four-step shell pipeline:

```bash
# 1. Compile to goto binary
goto-cc -o fn.goto stubs.c source.c

# 2. Instrument loops
goto-instrument --partial-loops --unwind N [--apply-loop-contracts] fn.goto fn.goto

# 3. Replace callees + enforce contract on target function
goto-instrument \
  --replace-call-with-contract callee1 \
  --replace-call-with-contract callee2 \
  --enforce-contract fn \
  fn.goto checking-fn.goto

# 4. Run CBMC with machine-parseable XML output
cbmc checking-fn.goto --function fn --depth 100 --xml-ui
```

XML output is parsed by `cbmc_output_parser.py` using `xml.etree.ElementTree`.

---

## Backtracking Algorithm

```python
def run(input_path):
    graph = CFunctionGraph(input_path)
    functions = graph.topological_order()      # leaves first
    initial_state = ProofState(functions)

    worklist = deque([initial_state])
    seen = {initial_state}

    while worklist:
        state = worklist.popleft()
        fn = state.peek_workstack()

        decision = SpecAgent(fn, state).run()
        next_state = stepper.transition(state, decision)

        if next_state not in seen:
            seen.add(next_state)
            if next_state.is_done():
                emit_results(next_state)
            else:
                worklist.append(next_state)
```

### Cycle prevention in `ProofStateStepper.transition()`:

```python
if decision.type == "BACKTRACK":
    callee = graph.get(decision.backtrack_callee)
    if callee is None:                          # external function → can't backtrack
        return state.assume(fn, spec)
    if state.workstack_contains(callee, decision.backtrack_hint):  # cycle
        return state.assume(fn, spec)
    if state.backtrack_count(fn) >= MAX_BACKTRACKS:
        return state.assume(fn, spec)
    return state.push_callee(callee, hint=decision.backtrack_hint)
```

---

## Key Design Decisions

1. **Claude Code as the agent runtime** — instead of manually driving the tool-use loop via `messages.create()`, the `claude` CLI handles multi-turn tool use natively. The Python layer only manages state and orchestration.
2. **MCP server per invocation** — the MCP server is launched as a stdio subprocess by Claude Code for each function. In-process mutable state (`_pending_spec`, `_last_cbmc_result`) is safe because the server lifetime is exactly one function's agent session.
3. **Single agent per function** — full context in one conversation window, no coordination overhead.
4. **Immutable `ProofState`** — enables `seen`-set cycle detection without explicit loop counters.
5. **`--replace-call-with-contract`** for compositional verification — callee specs become abstract axioms, preventing state explosion in callers.
6. **`--xml-ui`** for structured CBMC output — machine-parseable counterexample traces fed directly to the LLM.
7. **`diskcache` keyed on `hash(VerificationInput)`** — callee spec changes automatically bust the caller's cache without manual invalidation.
8. **Recursive functions** (SCCs in call graph) — `WorkItem.assume_without_verification=True` generates a spec but forces `finish(ASSUME)`.

---

## Verification Strategy

End-to-end test: pick a small C file with 3–5 functions (e.g., a linked list insert/search/delete), run `main.py --input-path ./test_input --output-dir ./out`, and inspect:

1. `./out/` for annotated C files with injected CBMC contracts
2. Logs showing topological processing order (leaves first)
3. At least one successful `--enforce-contract` CBMC run in the pipeline
4. One backtrack event logged if a callee spec is too weak

Unit tests:
- `cbmc_output_parser.py` — feed sample CBMC XML, assert parsed fields
- `proof_state_stepper.py` — assert cycle detection and backtrack count limits
- `test_tool_executor.py` — mock `CbmcClient`, verify MCP server tool logic directly
