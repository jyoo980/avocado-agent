# avocado-agent

Agentic CBMC specification generator for C programs. Given a C repository, it automatically generates and verifies [CBMC](https://www.cprover.org/cbmc/) contracts (`__CPROVER_requires`, `__CPROVER_ensures`, `__CPROVER_assigns`, loop invariants) for every function — starting from leaves of the call graph and working toward callers, with backtracking when a callee's postcondition turns out to be too weak.

## How it works

1. Builds a whole-program call graph from the input C files using tree-sitter.
2. Processes functions in topological order (callees before callers).
3. For each function, the [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI is invoked with a system prompt and an MCP server that exposes tools for reading source, inspecting callee specs, proposing a spec, running CBMC, and inspecting counterexamples. Claude Code handles the agentic tool-use loop natively.
4. If CBMC verifies the spec, it is marked **ACCEPTED**. If the agent determines a callee's postcondition is too weak, it **backtracks** — re-queuing the callee with a hint — and retries. Functions that cannot be verified after exhausting retries are marked **ASSUMED**.
5. Annotated C files and a Markdown summary are written to the output directory.

## Prerequisites

### Claude Code CLI

Install the Claude Code CLI and authenticate:

```bash
npm install -g @anthropic-ai/claude-code
claude login
```

Or download the [desktop app](https://claude.ai/download) which bundles the CLI.

### CBMC (local use only — not needed when using Docker)

Install CBMC 6.x (includes `goto-cc` and `goto-instrument`):

```bash
# macOS (Homebrew)
brew install cbmc

# Ubuntu / Debian
wget https://github.com/diffblue/cbmc/releases/download/cbmc-6.7.1/ubuntu-24.04-cbmc-6.7.1-Linux-amd64.deb
sudo dpkg -i ubuntu-24.04-cbmc-6.7.1-Linux-amd64.deb
```

Verify:

```bash
cbmc --version
goto-cc --version
```

### Python 3.13+

```bash
python --version   # should be 3.13 or newer
```

## Installation

```bash
uv sync
```

Or with pip:

```bash
pip install mcp diskcache networkx "tree-sitter>=0.25.2" tree-sitter-c loguru
```

## Usage

```bash
python main.py --input-path /path/to/your/c/repo --output-dir ./output
```

Results are written to `./output/summary.md`, which lists every function with its verification status and the generated spec.

### All options

```
--input-path PATH        Path to the C repository to verify (required)
--output-dir PATH        Directory to write results (default: ./output)
--model MODEL            Claude model to use (default: claude-sonnet-4-6)
--unwind N               Loop unwind bound for CBMC (default: 5)
--timeout-sec N          Per-function CBMC timeout in seconds (default: 120)
--disable-verifier-cache Disable disk cache for CBMC results
--cache-dir PATH         Directory for disk caches (default: ./.avocado_cache)
--skip-succeeded         Skip functions already verified in a previous run
```

### Examples

Run on a small project with a higher unwind bound:

```bash
python main.py --input-path ./examples/linked_list --output-dir ./output --unwind 10
```

Re-run after changes, skipping already-verified functions:

```bash
python main.py --input-path ./examples/linked_list --output-dir ./output --skip-succeeded
```

Use a faster model for exploration:

```bash
python main.py --input-path ./myrepo --model claude-haiku-4-5-20251001
```

## Docker

The Docker image bundles CBMC (installed from Ubuntu 24.04 packages) — no local CBMC installation needed.

> **Apple Silicon:** Docker Desktop uses Rosetta 2 to run the amd64 image. Add `--platform linux/amd64` to both `build` and `run`.

The `claude` CLI is **not** bundled in the image — you need to install it separately or mount it from the host. The image passes `ANTHROPIC_API_KEY` through to the `claude` subprocess.

```bash
# Build
docker build -t avocado-agent .                        # x86_64
docker build --platform linux/amd64 -t avocado-agent . # Apple Silicon

# Run
docker run --rm \
  -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
  -v /path/to/your/c/repo:/input:ro \
  -v $(pwd)/output:/output \
  avocado-agent --input-path /input --output-dir /output
```

## Output

`output/summary.md` contains:

- **Verified functions** — CBMC confirmed the generated spec matches the implementation.
- **Assumed functions** — A spec was generated but could not be CBMC-verified (accepted without proof).

Each entry shows the generated contract:

```
### `array_sum` (array.c:12)

Preconditions:
  __CPROVER_requires(n >= 0)
  __CPROVER_requires(__CPROVER_is_fresh(arr, n * sizeof(*arr)))
Postconditions:
  __CPROVER_ensures(__CPROVER_return_value >= 0)
Assigns:
Loop contracts:
  Loop 1:
    invariant: 0 <= i && i <= n
    decreases: n - i
    assigns: i
```

## Running tests

```bash
uv run pytest tests/ -v
```

## Project structure

```
avocado-agent/
├── main.py                      # CLI entry point and supervisor loop
├── util/
│   ├── c_function.py            # CFunction dataclass
│   ├── c_function_graph.py      # Call graph construction (tree-sitter + networkx)
│   ├── function_specification.py # FunctionSpecification and LoopContract types
│   ├── tree_sitter_util.py      # C parsing and call-site extraction
│   └── function_util.py         # Contract injection into C source files
├── verification/
│   ├── cbmc_client.py           # goto-cc / goto-instrument / cbmc pipeline
│   ├── cbmc_output_parser.py    # CBMC --xml-ui output parser
│   ├── verification_input.py    # Cache key computation
│   ├── verification_result.py   # Result types and counterexample traces
│   └── verification_cache.py    # diskcache-backed result cache
├── proof_state/
│   ├── proof_state.py           # Immutable, hashable ProofState and WorkItem
│   ├── proof_state_stepper.py   # State transitions and backtracking logic
│   └── next_step.py             # AgentDecision dataclass
├── agent/
│   ├── spec_agent.py            # Invokes claude CLI as subprocess; reads decision from result JSON
│   └── mcp_server.py            # FastMCP server exposing 8 tools to Claude Code
├── prompts/
│   ├── system_prompt.txt        # CBMC domain knowledge passed as system prompt
│   ├── spec_generation_prompt.txt
│   └── backtrack_hint_prompt.txt
└── tests/
    ├── test_cbmc_output_parser.py
    ├── test_proof_state_stepper.py
    └── test_tool_executor.py
```
