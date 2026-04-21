from __future__ import annotations

import shlex
import subprocess
import tempfile
from pathlib import Path

from loguru import logger

from util.function_util import inject_function_contract
from verification.cbmc_output_parser import parse_cbmc_xml_output
from verification.verification_cache import VerificationCache
from verification.verification_input import VerificationInput
from verification.verification_result import VerificationResult, VerificationStatus


class CbmcClient:
    def __init__(
        self,
        output_dir: Path,
        cache: VerificationCache | None = None,
        unwind_default: int = 5,
        timeout_sec: int = 120,
    ) -> None:
        self._output_dir = output_dir
        self._cache = cache
        self._unwind_default = unwind_default
        self._timeout_sec = timeout_sec

    def verify(self, vinput: VerificationInput) -> VerificationResult:
        if self._cache is not None:
            cached = self._cache.get(vinput)
            if cached is not None:
                logger.debug(f"Cache hit for {vinput.function.name}")
                return cached

        result = self._run_pipeline(vinput)

        if self._cache is not None:
            self._cache.set(vinput, result)

        return result

    def _run_pipeline(self, vinput: VerificationInput) -> VerificationResult:
        fn = vinput.function
        logger.info(f"Running CBMC verification for '{fn.name}'")

        with tempfile.TemporaryDirectory(prefix="avocado_") as tmpdir:
            tmp = Path(tmpdir)
            annotated_src = inject_function_contract(fn, vinput.spec, tmp)

            # Also inject callee specs into the same file if needed.
            # For simplicity, we write stubs for callee contracts in a separate file.
            stub_file = self._write_callee_stubs(vinput, tmp)

            goto_bin = tmp / f"{fn.name}.goto"
            checking_bin = tmp / f"checking-{fn.name}.goto"

            # Step 1: Compile to goto binary
            compile_cmd = self._build_compile_cmd(annotated_src, stub_file, goto_bin, fn.name)
            compile_result = self._run_cmd(compile_cmd, tmp)
            if compile_result.returncode != 0:
                logger.warning(f"goto-cc compile error for '{fn.name}':\n{compile_result.stderr}")
                return VerificationResult(
                    status=VerificationStatus.COMPILE_ERROR,
                    raw_output=compile_result.stderr,
                    failure_descriptions=[compile_result.stderr[:500]],
                )

            # Step 2: Instrument loops
            instrument_cmd = self._build_instrument_cmd(
                goto_bin, goto_bin, vinput.spec.has_loop_contracts(), vinput.unwind
            )
            self._run_cmd(instrument_cmd, tmp)

            # Step 3: Enforce contract + replace callees
            replace_args = [f"--replace-call-with-contract {name}" for name in vinput.callee_specs]
            enforce_cmd = self._build_enforce_cmd(goto_bin, checking_bin, fn.name, replace_args)
            self._run_cmd(enforce_cmd, tmp)

            # Step 4: Run CBMC
            cbmc_cmd = f"cbmc {checking_bin} --function {fn.name} --depth 100 --xml-ui"
            cbmc_result = self._run_cmd(cbmc_cmd, tmp)

            raw_output = cbmc_result.stdout + cbmc_result.stderr

            if cbmc_result.returncode == 10:  # CBMC convention: 10 = verification failure
                return parse_cbmc_xml_output(raw_output)
            if cbmc_result.returncode not in (0, 10):
                # Non-standard exit: could be parse error or internal failure
                return VerificationResult(
                    status=VerificationStatus.PARSE_ERROR,
                    raw_output=raw_output,
                    failure_descriptions=[raw_output[:500]],
                )

            return parse_cbmc_xml_output(raw_output)

    def _write_callee_stubs(self, vinput: VerificationInput, tmp: Path) -> Path | None:
        """Write a C stub file declaring each callee with its contract annotations."""
        if not vinput.callee_specs:
            return None

        lines: list[str] = ["#include <stdlib.h>\n"]
        for callee_name, callee_spec in vinput.callee_specs.items():
            for pre in callee_spec.preconditions:
                lines.append(f"__CPROVER_requires({pre})\n")
            for post in callee_spec.postconditions:
                lines.append(f"__CPROVER_ensures({post})\n")
            for target in callee_spec.assigns:
                lines.append(f"__CPROVER_assigns({target})\n")
            # Stub body — CBMC replaces it with the contract anyway
            lines.append(f"void {callee_name}(void);\n\n")

        stub_path = tmp / "_stubs.c"
        stub_path.write_text("".join(lines))
        return stub_path

    def _build_compile_cmd(self, src: Path, stub: Path | None, out: Path, fn_name: str) -> str:
        parts = ["goto-cc", f"-o {out}"]
        if stub is not None:
            parts.append(str(stub))
        parts.append(str(src))
        return " ".join(parts)

    def _build_instrument_cmd(
        self, src: Path, dst: Path, has_loop_contracts: bool, unwind: int
    ) -> str:
        parts = ["goto-instrument", f"--partial-loops", f"--unwind {unwind}"]
        if has_loop_contracts:
            parts.append("--apply-loop-contracts")
        parts += [str(src), str(dst)]
        return " ".join(parts)

    def _build_enforce_cmd(
        self, src: Path, dst: Path, fn_name: str, replace_args: list[str]
    ) -> str:
        parts = (
            ["goto-instrument"]
            + replace_args
            + [f"--enforce-contract {fn_name}", str(src), str(dst)]
        )
        return " ".join(parts)

    def _run_cmd(self, cmd: str, cwd: Path) -> subprocess.CompletedProcess:
        logger.debug(f"$ {cmd}")
        try:
            return subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=self._timeout_sec,
            )
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(
                args=cmd, returncode=124, stdout="", stderr="Timeout"
            )
