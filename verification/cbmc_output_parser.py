from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from verification.verification_result import (
    CbmcCounterexample,
    CbmcTraceStep,
    VerificationResult,
    VerificationStatus,
)


def parse_cbmc_xml_output(xml_text: str) -> VerificationResult:
    """Parse CBMC --xml-ui output into a VerificationResult."""
    try:
        root = ET.fromstring(_sanitize_xml(xml_text))
    except ET.ParseError as e:
        return VerificationResult(
            status=VerificationStatus.PARSE_ERROR,
            raw_output=xml_text,
            failure_descriptions=[f"XML parse error: {e}"],
        )

    failure_descriptions: list[str] = []
    counterexample: CbmcCounterexample | None = None

    for result_el in root.iter("result"):
        status_str = result_el.get("status", "").upper()
        if status_str == "FAILURE":
            prop_el = result_el.find("property")
            desc = ""
            location = ""
            if prop_el is not None:
                desc_el = prop_el.find("description")
                if desc_el is not None and desc_el.text:
                    desc = desc_el.text.strip()
                loc_el = prop_el.find("location")
                if loc_el is not None:
                    location = f"{loc_el.get('file', '')}:{loc_el.get('line', '')}"
            failure_descriptions.append(desc or "unknown failure")

            if counterexample is None:
                trace = _parse_trace(result_el)
                counterexample = CbmcCounterexample(
                    failing_property=desc,
                    failure_location=location,
                    trace=trace,
                )

    # Also check top-level status summary element
    summary_el = root.find("cprover-status")
    if summary_el is not None:
        top_status = summary_el.text.strip().upper() if summary_el.text else ""
        if top_status == "SUCCESS" and not failure_descriptions:
            return VerificationResult(
                status=VerificationStatus.SUCCEEDED,
                raw_output=xml_text,
            )

    if failure_descriptions:
        return VerificationResult(
            status=VerificationStatus.FAILED,
            failure_descriptions=failure_descriptions,
            counterexample=counterexample,
            raw_output=xml_text,
        )

    # No explicit failure — check if any property tag has FAILURE
    all_statuses = {r.get("status", "").upper() for r in root.iter("result")}
    if "FAILURE" in all_statuses:
        return VerificationResult(
            status=VerificationStatus.FAILED,
            failure_descriptions=failure_descriptions,
            counterexample=counterexample,
            raw_output=xml_text,
        )

    return VerificationResult(
        status=VerificationStatus.SUCCEEDED,
        raw_output=xml_text,
    )


def _parse_trace(result_el: ET.Element) -> list[CbmcTraceStep]:
    steps: list[CbmcTraceStep] = []
    trace_el = result_el.find(".//trace")  # may be nested under <property>
    if trace_el is None:
        return steps

    for step_el in trace_el:
        step_nr_str = step_el.get("step_nr", "0")
        try:
            step_nr = int(step_nr_str)
        except ValueError:
            step_nr = 0

        func = ""
        location = ""
        func_el = step_el.find("function")
        if func_el is not None:
            func = func_el.get("display_name", "") or func_el.text or ""
        loc_el = step_el.find("location")
        if loc_el is not None:
            location = f"{loc_el.get('file', '')}:{loc_el.get('line', '')}"

        assignments: dict[str, str] = {}
        # Each step_el may itself be an <assignment> or contain <assignment> children.
        candidates = step_el.findall("assignment") or (
            [step_el] if step_el.tag == "assignment" else []
        )
        for assign_el in candidates:
            name_el = assign_el.find("full_lhs")
            val_el = assign_el.find("full_lhs_value")
            if name_el is not None and val_el is not None:
                name = name_el.text or ""
                val = val_el.text or ""
                if name:
                    assignments[name.strip()] = val.strip()

        steps.append(CbmcTraceStep(step_nr, func, location, assignments))

    return steps


def _sanitize_xml(text: str) -> str:
    # CBMC sometimes emits multiple root elements; wrap in a synthetic root.
    if not text.strip().startswith("<?xml"):
        return f"<cprover_output>{text}</cprover_output>"
    # Already has XML declaration — strip it and wrap body.
    lines = text.splitlines()
    body_lines = [l for l in lines if not l.strip().startswith("<?xml")]
    return f"<cprover_output>{''.join(body_lines)}</cprover_output>"
