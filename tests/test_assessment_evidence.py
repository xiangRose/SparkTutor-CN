"""Launchers may route learner and environment failures to either stream."""

import pytest

from sparktutor.engine.assessment_evidence import execution_failure_kind
from sparktutor.engine.executor import ExecMode, ExecResult


@pytest.mark.parametrize("stdout,stderr,expected", [
    ("AssertionError: Expected two color groups", "Spark INFO", "learner"),
    ("", "AssertionError: Expected two color groups", "learner"),
    ("ModuleNotFoundError: No module named pyspark", "AssertionError", "infrastructure"),
    ("AssertionError", "JAVA_GATEWAY_EXITED", "infrastructure"),
    ("", "Unknown launcher failure", "unverified"),
])
def test_failure_classification_uses_both_streams_and_prioritizes_environment(stdout, stderr, expected):
    assert execution_failure_kind(ExecResult(ExecMode.LOCAL, 1, stdout, stderr)) == expected


def test_syntax_only_execution_is_not_promoted_to_real_failure_evidence():
    assert execution_failure_kind(ExecResult(ExecMode.DRY_RUN, 1, "SyntaxError", "")) == "dry_run"
