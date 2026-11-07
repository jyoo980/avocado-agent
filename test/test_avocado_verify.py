"""Tests for the main loop of `avocado_verify`."""

import json
import sys
from pathlib import Path

import pytest

import avocado_verify
from avocado_verify import FunctionVerificationResult, GroundTruthVerificationResult
from tools.run_cbmc import RunCbmcResult
from tools.util.edit_rules import MergeReport


def test_a_helper_that_the_agent_adds_is_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A function that the agent adds during the run is verified in the same run."""
    source = tmp_path / "f.c"
    source.write_text("int f(void) { return 0; }\n")
    verified: list[str] = []

    def fake_verify_via_agent(function: str, **_kwargs: object) -> FunctionVerificationResult:
        transplanted = []
        if function == "f":
            # While verifying `f`, the agent extracts a helper `g`, which the merge transplants.
            source.write_text("int g(void) { return 1; }\nint f(void) { return g(); }\n")
            transplanted = ["function:g"]
        verified.append(function)
        return FunctionVerificationResult(
            function=function,
            outcome=GroundTruthVerificationResult.VERIFIED,
            claude_sessions=[],
            cbmc=RunCbmcResult(
                function=function, failed_step=None, timed_out=False, returncode=0, response="ok"
            ),
            internal_callees=[],
            verification_attempts=1,
            agent_sessions=1,
            merge=MergeReport(True, transplanted=transplanted),
        )

    monkeypatch.setattr(avocado_verify, "_verify_via_agent", fake_verify_via_agent)
    monkeypatch.setattr(sys, "argv", ["avocado-verify", "--file", str(source)])
    with pytest.raises(SystemExit):
        avocado_verify.main()

    assert verified == ["f", "g"]
    log = tmp_path / "f-avocado-verify.jsonl"
    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert records[0]["function"] == "f" and records[0]["internal_callees"] == ["g"]
    summary = records[-1]
    assert summary["verified"] == summary["total"] == 2
