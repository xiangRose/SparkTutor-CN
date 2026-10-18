"""Tests for the evaluation engine."""

import pytest
from pathlib import Path

from sparktutor.engine.evaluator import Evaluator, EvalResult, FeedbackItem
from sparktutor.config.settings import Settings
from sparktutor.engine.executor import ExecMode, ExecResult
from sparktutor.engine.lesson_loader import Step, load_lesson
from sparktutor.engine.normalizer import choices_match, code_match, normalize_code


class TestNormalizer:
    def test_choices_match_exact(self):
        assert choices_match("getOrCreate()", "getOrCreate()")

    def test_choices_match_case_insensitive(self):
        assert choices_match("getorcreate()", "getOrCreate()")

    def test_choices_no_match(self):
        assert not choices_match("create()", "getOrCreate()")

    def test_chinese_choices_keep_their_meaning(self):
        assert choices_match("协调执行并规划查询", "协调执行并规划查询")
        assert not choices_match("将数据存储在磁盘上", "协调执行并规划查询")
        assert not choices_match("", "协调执行并规划查询")
        assert not choices_match(" \n ", "")

    def test_choice_full_width_ascii_and_whitespace(self):
        assert choices_match("　调用：getOrCreate（）　", "调用:getOrCreate()")
        assert choices_match("Spark  SQL\n查询", "Spark SQL 查询")

    @pytest.mark.parametrize("guess,correct", [
        ("a + b", "a - b"),
        ("count()", "count"),
        ("is_null", "isnull"),
        ("x²", "x2"),
        ("x < 2", "x > 2"),
    ])
    def test_choices_keep_code_and_math_distinctions(self, guess, correct):
        assert not choices_match(guess, correct)

    def test_code_match_quotes(self):
        assert code_match('x = "hello"', "x = 'hello'")

    def test_code_match_whitespace(self):
        assert code_match("x  =  42", "x = 42")

    def test_code_no_match(self):
        assert not code_match("x = 42", "x = 43")

    def test_code_match_preserves_literal_whitespace(self):
        assert not code_match("x = 'a  b'", "x = 'a b'")
        assert not code_match('x = "He said \\\"yes\\\""', 'x = "He said \'yes\'"')

    def test_exact_answer_keeps_indentation_semantics(self):
        lesson = load_lesson(Path(__file__).resolve().parents[1] / "src/sparktutor/courses/"
                             "spark_declarative_pipelines/lessons/04_pipeline_framework")
        correct = next(step.correct_answer for step in lesson.steps if step.id == "5")
        wrong = correct.replace("        return decorator", "            return decorator")
        assert wrong != correct
        result = Evaluator().check_code_exact(wrong, correct)
        assert not result.passed
        assert not result.assessment_eligible

    def test_normalize_code(self):
        assert normalize_code("  x  =  42  ") == "x = 42"


class TestEvaluator:
    @pytest.fixture
    def evaluator(self):
        return Evaluator()

    def test_syntax_check_valid(self, evaluator):
        result = evaluator.check_syntax("x = 42")
        assert result.passed

    def test_syntax_check_invalid(self, evaluator):
        result = evaluator.check_syntax("x = ")
        assert not result.passed
        assert result.feedback[0].severity == "error"
        assert result.feedback[0].line is not None

    def test_mult_choice_correct(self, evaluator):
        result = evaluator.check_mult_choice("getOrCreate()", "getOrCreate()")
        assert result.passed

    def test_mult_choice_wrong(self, evaluator):
        result = evaluator.check_mult_choice("create()", "getOrCreate()")
        assert not result.passed

    def test_code_exact_match(self, evaluator):
        result = evaluator.check_code_exact("x = 42", "x = 42")
        assert result.passed

    def test_ast_contains_class(self, evaluator):
        code = "class Pipeline:\n    pass"
        result = evaluator.check_ast_contains(code, [{"expr": "ast_contains(class_def='Pipeline')"}])
        assert result.passed

    def test_ast_contains_missing_class(self, evaluator):
        code = "x = 42"
        result = evaluator.check_ast_contains(code, [{"expr": "ast_contains(class_def='Pipeline')"}])
        assert not result.passed
        assert any("Pipeline" in f.message for f in result.feedback)

    def test_ast_contains_method(self, evaluator):
        code = "class Foo:\n    def __init__(self):\n        pass"
        result = evaluator.check_ast_contains(code, [{"expr": "ast_contains(method='__init__')"}])
        assert result.passed

    def test_ast_contains_syntax_error(self, evaluator):
        result = evaluator.check_ast_contains("def (:", [{"expr": "ast_contains(function='foo')"}])
        assert not result.passed
        assert result.feedback[0].severity == "error"


def test_all_course_choices_reject_every_wrong_option():
    courses_dir = Path(__file__).resolve().parents[1] / "src" / "sparktutor" / "courses"
    evaluator = Evaluator()
    questions_checked = 0
    for lesson_path in courses_dir.rglob("lesson.yaml"):
        lesson = load_lesson(lesson_path.parent)
        for step in lesson.steps:
            if step.cls != "mult_question":
                continue
            questions_checked += 1
            assert step.correct_answer
            options = [option.strip() for option in step.answer_choices.split(";")]
            assert step.correct_answer in options
            for option in options:
                expected = option == step.correct_answer
                result = evaluator.check_mult_choice(option, step.correct_answer)
                assert result.passed is expected, (
                    f"{lesson_path}: {option!r} compared with {step.correct_answer!r}"
                )
    assert questions_checked > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("execution,kind,eligible,message", [
    (ExecResult(ExecMode.LOCAL, 124, "", "Execution timed out after 120s"),
     "infrastructure", False, "执行超时，课程测试尚未完成"),
    (ExecResult(ExecMode.LOCAL, 124, "AssertionError", "Execution timed out after 120s"),
     "infrastructure", False, "无法判断作答是否正确"),
    (ExecResult(ExecMode.LOCAL, 1, "ModuleNotFoundError: No module named pyspark", ""),
     "infrastructure", False, "请先检查 Python、Java、PySpark 与运行环境"),
    (ExecResult(ExecMode.LOCAL, 137, "", "process terminated"),
     "infrastructure", False, "执行环境发生故障"),
    (ExecResult(ExecMode.LOCAL, 1, "", "AssertionError: Expected quantity sum"),
     "learner", True, "请根据反馈修正后重新提交"),
    (ExecResult(ExecMode.LOCAL, 1, "", "Unknown launcher failure"),
     "unverified", False, "现有信息不足以判断作答是否正确"),
    (ExecResult(ExecMode.DRY_RUN, 0, "Syntax OK", ""),
     "dry_run", False, "尚未验证结果"),
    (ExecResult(ExecMode.LOCAL, 0, "partial output", ""),
     "unverified", False, "课程测试未完整执行"),
    (None, "unverified", False, "尚未运行课程测试"),
])
async def test_required_execution_feedback_preserves_assessment_attribution(
    execution, kind, eligible, message,
):
    evaluator = Evaluator(settings=Settings())
    step = Step(cls="script", requires_execution=True, correct_answer="x = 42")
    result, needs_ai, review = await evaluator.evaluate_local("x = 42", step, exec_result=execution)

    assert result is not None and not result.passed
    assert not needs_ai and review == {}
    assert result.assessment_source == "course_tests"
    assert result.assessment_eligible is eligible
    assert result.failure_kind == kind
    assert message in result.feedback[0].message
    assert result.feedback[0].suggestion == (execution.stderr if execution and execution.stderr else None)
    if kind != "learner":
        assert "修正" not in result.feedback[0].message
