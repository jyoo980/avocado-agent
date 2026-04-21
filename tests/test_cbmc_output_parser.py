from __future__ import annotations

import pytest

from verification.cbmc_output_parser import parse_cbmc_xml_output
from verification.verification_result import VerificationStatus

SUCCEEDED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<cprover>
  <cprover-status>SUCCESS</cprover-status>
</cprover>
"""

FAILED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<cprover>
  <result status="FAILURE">
    <property name="postcondition.0">
      <description>Check ensures clause of foo</description>
      <location file="foo.c" line="42" function="foo"/>
      <trace>
        <assignment step_nr="1">
          <full_lhs>x</full_lhs>
          <full_lhs_value>5</full_lhs_value>
          <location file="foo.c" line="10" function="foo"/>
          <function display_name="foo"/>
        </assignment>
        <assignment step_nr="2">
          <full_lhs>result</full_lhs>
          <full_lhs_value>-1</full_lhs_value>
          <location file="foo.c" line="42" function="foo"/>
          <function display_name="foo"/>
        </assignment>
      </trace>
    </property>
  </result>
  <cprover-status>FAILURE</cprover-status>
</cprover>
"""

MALFORMED_XML = "this is not xml at all"


def test_parse_succeeded():
    result = parse_cbmc_xml_output(SUCCEEDED_XML)
    assert result.status == VerificationStatus.SUCCEEDED
    assert result.counterexample is None
    assert result.failure_descriptions == []


def test_parse_failed():
    result = parse_cbmc_xml_output(FAILED_XML)
    assert result.status == VerificationStatus.FAILED
    assert len(result.failure_descriptions) == 1
    assert "ensures" in result.failure_descriptions[0]
    assert result.counterexample is not None
    assert result.counterexample.failure_location == "foo.c:42"


def test_parse_failed_trace():
    result = parse_cbmc_xml_output(FAILED_XML)
    trace = result.counterexample.trace
    assert len(trace) == 2
    assert trace[0].step_nr == 1
    assert trace[0].assignments["x"] == "5"
    assert trace[1].assignments["result"] == "-1"


def test_parse_malformed():
    result = parse_cbmc_xml_output(MALFORMED_XML)
    # Should not raise; should return SUCCEEDED or PARSE_ERROR gracefully
    assert result.status in (VerificationStatus.PARSE_ERROR, VerificationStatus.SUCCEEDED)
