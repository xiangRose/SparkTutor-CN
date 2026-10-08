"""Conservative attribution: environment failures are not learner mistakes."""

import re


def execution_failure_kind(result) -> str:
    if result is None:
        return "unverified"
    if result.mode.value == "dry_run":
        return "dry_run"
    if result.success:
        return ""
    if result.exit_code in {124, 130, 137, -9, -15}:
        return "infrastructure"
    if re.search(
        r"JAVA_GATEWAY_EXITED|ConnectionRefusedError|ConnectionResetError|"
        r"OutOfMemoryError|ModuleNotFoundError|ImportError|FileNotFoundError|"
        r"ClassNotFoundException|JAVA_HOME|No module named|timed out|TimeoutError",
        result.stderr, re.IGNORECASE,
    ):
        return "infrastructure"
    if re.search(
        r"AssertionError|SyntaxError|IndentationError|NameError|TypeError|ValueError|"
        r"KeyError|IndexError|ZeroDivisionError|AttributeError|AnalysisException|"
        r"ParseException|UNRESOLVED_COLUMN|DATATYPE_MISMATCH",
        result.stderr,
    ):
        return "learner"
    return "unverified"
