from __future__ import annotations

import re
import shutil
from pathlib import Path

from util.c_function import CFunction
from util.function_specification import FunctionSpecification, LoopContract


def inject_function_contract(
    fn: CFunction,
    spec: FunctionSpecification,
    output_dir: Path,
) -> Path:
    """Write a copy of fn's source file to output_dir with CBMC contracts injected.

    Function-level contracts are inserted as annotations immediately before the
    function signature. Loop contracts are inserted inside loop bodies.
    Returns the path to the modified file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / fn.file_path.name

    # Start from the original if the output file doesn't exist yet; otherwise
    # work on the already-modified output (multiple functions in the same file).
    src = out_path if out_path.exists() else fn.file_path
    lines = src.read_text().splitlines(keepends=True)

    lines = _inject_function_contract(lines, fn, spec)
    if spec.loop_contracts:
        lines = _inject_loop_contracts(lines, fn, spec.loop_contracts)

    out_path.write_text("".join(lines))
    return out_path


def _inject_function_contract(
    lines: list[str], fn: CFunction, spec: FunctionSpecification
) -> list[str]:
    annotations: list[str] = []
    for pre in spec.preconditions:
        annotations.append(f"__CPROVER_requires({pre})\n")
    for post in spec.postconditions:
        annotations.append(f"__CPROVER_ensures({post})\n")
    for target in spec.assigns:
        annotations.append(f"__CPROVER_assigns({target})\n")

    if not annotations:
        return lines

    insert_at = fn.start_line - 1  # 0-indexed
    return lines[:insert_at] + annotations + lines[insert_at:]


def _inject_loop_contracts(
    lines: list[str], fn: CFunction, loop_contracts: list[LoopContract]
) -> list[str]:
    """Insert loop invariants/decreases immediately after each loop's opening brace."""
    loop_pattern = re.compile(r"^\s*(for|while|do)\b")
    brace_pattern = re.compile(r"\{")

    # Offset accumulates as we insert lines above earlier loops.
    offset = 0
    loop_idx = 0

    fn_lines_range = range(fn.start_line - 1, fn.end_line)

    for original_line_no in fn_lines_range:
        adjusted = original_line_no + offset
        if adjusted >= len(lines):
            break
        line = lines[adjusted]

        if loop_pattern.match(line) and loop_idx < len(loop_contracts):
            lc = loop_contracts[loop_idx]
            # Find the opening brace — it may be on the same line or the next.
            brace_line = adjusted
            while brace_line < len(lines) and "{" not in lines[brace_line]:
                brace_line += 1

            if brace_line < len(lines):
                indent = (
                    _detect_indent(lines[brace_line + 1]) if brace_line + 1 < len(lines) else "  "
                )
                inserts: list[str] = []
                inserts.append(f"{indent}__CPROVER_loop_invariant({lc.invariant});\n")
                if lc.decreases:
                    inserts.append(f"{indent}__CPROVER_decreases({lc.decreases});\n")
                for a in lc.assigns:
                    inserts.append(f"{indent}__CPROVER_assigns({a});\n")

                insert_pos = brace_line + 1
                lines = lines[:insert_pos] + inserts + lines[insert_pos:]
                offset += len(inserts)
                loop_idx += 1

    return lines


def _detect_indent(line: str) -> str:
    stripped = line.lstrip()
    return line[: len(line) - len(stripped)] if stripped else "  "


def copy_source_file(src: Path, output_dir: Path) -> Path:
    """Copy a source file to output_dir unchanged."""
    output_dir.mkdir(parents=True, exist_ok=True)
    dst = output_dir / src.name
    shutil.copy2(src, dst)
    return dst
