"""Offline guard checks for the real CLI/HTTP/Spark acceptance tool."""

import ast
import json

import pytest

from tools import verify_trusted_local as tool


def test_existing_evidence_is_not_overwritten_or_followed_by_network_work(tmp_path, monkeypatch):
    output = tmp_path / "already-verified"
    output.mkdir()
    original = b'{"status":"passed","input":"preserve existing evidence"}'
    (output / "report.json").write_bytes(original)
    def forbidden(*args, **kwargs):
        raise AssertionError("Existing evidence must be rejected before any server launch")
    monkeypatch.setattr(tool.subprocess, "Popen", forbidden)
    monkeypatch.setattr(tool.socket, "socket", forbidden)
    with pytest.raises(ValueError, match="never overwritten"):
        tool.verify(output)
    assert (output / "report.json").read_bytes() == original


def test_error_variants_change_task_aggregation_without_modifying_test_assertions():
    for (reference, wrong), function in zip(tool.reference_variants(),
                                           ("mnm_analysis", "transfer_sensor_aggregation")):
        good_tree, bad_tree = ast.parse(reference), ast.parse(wrong)
        good_function = next(node for node in good_tree.body if isinstance(node, ast.FunctionDef) and node.name == function)
        bad_function = next(node for node in bad_tree.body if isinstance(node, ast.FunctionDef) and node.name == function)
        assert ast.dump(good_function.args) == ast.dump(bad_function.args)
        assert ast.dump(good_function) != ast.dump(bad_function)
        assert any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                   and node.func.attr == "count" and isinstance(node.args[0], ast.Constant)
                   and node.args[0].value == "*" for node in ast.walk(bad_function))
        good_tests = [node for node in good_tree.body if isinstance(node, ast.If)]
        bad_tests = [node for node in bad_tree.body if isinstance(node, ast.If)]
        assert good_tests and [ast.dump(node) for node in good_tests] == [ast.dump(node) for node in bad_tests]


def test_changed_reference_fails_instead_of_testing_an_unchanged_wrong_answer(tmp_path, monkeypatch):
    course = tmp_path / "course"
    (course / "01_getting_started").mkdir(parents=True)
    (course / "01_getting_started/solution.py").write_text("def mnm_analysis(spark, file_path):\n    return None\n", encoding="utf-8")
    monkeypatch.setattr(tool, "COURSE", course)
    with pytest.raises(ValueError, match="must be updated"):
        tool.reference_variants()
