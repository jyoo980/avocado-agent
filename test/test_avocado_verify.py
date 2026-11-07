"""Tests for the main loop of `avocado_verify`."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import avocado_verify
from avocado_verify import GroundTruthVerificationResult


def test_a_helper_that_the_agent_adds_is_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A function that the agent adds during the run is verified in the same run."""
    source = tmp_path / "f.c"
    source.write_text("int f(void) { return 0; }\n")
    verified: list[str] = []

    def fake_verify_via_agent(function: str, **_kwargs: object) -> SimpleNamespace:
        if function == "f":
            # While verifying `f`, the agent extracts a helper `g`.
            source.write_text("int g(void) { return 1; }\nint f(void) { return g(); }\n")
        verified.append(function)
        return SimpleNamespace(
            function=function,
            outcome=GroundTruthVerificationResult.VERIFIED,
            to_record=lambda: {
                "function": function,
                "outcome": str(GroundTruthVerificationResult.VERIFIED),
            },
        )

    monkeypatch.setattr(avocado_verify, "_verify_via_agent", fake_verify_via_agent)
    monkeypatch.setattr(sys, "argv", ["avocado-verify", "--file", str(source)])
    with pytest.raises(SystemExit):
        avocado_verify.main()

    assert verified == ["f", "g"]
    log = tmp_path / "f-avocado-verify.jsonl"
    summary = json.loads(log.read_text().splitlines()[-1])
    assert summary["verified"] == summary["total"] == 2
