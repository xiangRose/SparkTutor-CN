"""The first code exercise must give useful feedback without an AI service."""

from pathlib import Path

import pytest

from sparktutor.engine.evaluator import Evaluator
from sparktutor.engine.lesson_loader import load_lesson


DIRECTORY = Path(__file__).resolve().parents[1] / "src/sparktutor/courses/learning_spark/lessons/01_getting_started"


def session_step():
    return next(step for step in load_lesson(DIRECTORY).steps if step.id == "8")


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [
    session_step().correct_answer,
    "from pyspark.sql import SparkSession\nspark = (SparkSession.builder\n.master('local[*]')\n.appName('MnMCount').getOrCreate())",
    "from pyspark.sql import SparkSession as S\nspark = S.builder.appName('MnMCount').master('local[*]').getOrCreate()",
    "import pyspark.sql as sql\nspark = sql.SparkSession.builder.master('local[*]').appName('MnMCount').getOrCreate()",
    "import pyspark.sql\nspark = pyspark.sql.SparkSession.builder.appName('MnMCount').master('local[*]').getOrCreate()",
    "from pyspark import sql\nspark = sql.SparkSession.builder.appName('MnMCount').master('local[*]').getOrCreate()",
    "from pyspark.sql import SparkSession\nname = 'MnMCount'\nb = SparkSession.builder\nb.appName(name)\nb.master('local[*]')\nspark = b.getOrCreate()",
    "from pyspark.sql import SparkSession\nspark: SparkSession = SparkSession.builder.appName('MnMCount').master('local[*]').getOrCreate()",
])
async def test_equivalent_introductory_answers_pass_without_ai_or_spark(code):
    result, needs_ai, prompt = await Evaluator().evaluate_local(code, session_step())
    assert not needs_ai and prompt == {}
    assert result.passed and result.assessment_source == "structural"
    assert "不代表 Spark 已实际运行" in result.encouragement


REFERENCE = session_step().correct_answer


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [
    # The learner's reported code: wrong name, no import, no spark assignment.
    'SparkSession.builder.appName("MnCount").master("local[*]").getOrCreate()',
    REFERENCE.replace('"MnMCount"', '"MnCount"'),
    REFERENCE.replace('"local[*]"', '"local[2]"'),
    REFERENCE.replace('.appName("MnMCount")', ''),
    REFERENCE.replace('.master("local[*]")', ''),
    REFERENCE.replace('.getOrCreate()', '.getOrCreate'),
    REFERENCE.replace('spark = ', ''),
    REFERENCE.replace('from pyspark.sql import SparkSession', ''),
    REFERENCE + '\nspark = "not a session"',
    'def create_session():\n' + '\n'.join('    ' + line for line in REFERENCE.splitlines()),
    'if False:\n' + '\n'.join('    ' + line for line in REFERENCE.splitlines()),
    REFERENCE.replace('from pyspark.sql import SparkSession', 'SparkSession = "fake"'),
    REFERENCE.replace('.getOrCreate()', '.appName("WrongLastName").getOrCreate()'),
    REFERENCE.replace('spark = ', 'another_session = '),
    REFERENCE.replace('.getOrCreate()', '.getOrCreate("unexpected")'),
    REFERENCE.replace('from pyspark.sql import SparkSession', 'import pyspark.sql.SparkSession').replace('SparkSession.builder', 'pyspark.sql.SparkSession.builder'),
    REFERENCE + '\nspark.stop()',
])
async def test_incomplete_or_wrong_introductory_answers_get_local_feedback(code):
    result, needs_ai, prompt = await Evaluator().evaluate_local(code, session_step())
    assert not needs_ai and prompt == {}
    assert not result.passed and result.feedback
    assert result.assessment_source == "structural"
    assert result.assessment_eligible and result.failure_kind == "learner"


@pytest.mark.asyncio
async def test_wrong_application_name_reports_the_required_name():
    result, _, _ = await Evaluator().evaluate_local(REFERENCE.replace('"MnMCount"', '"MnCount"'), session_step())
    assert any("MnMCount" in item.message and "appName" in item.message for item in result.feedback)


@pytest.mark.asyncio
async def test_broken_course_contract_is_not_a_learner_error():
    step = session_step()
    step.validation[0].params.pop("master")
    result, needs_ai, _ = await Evaluator().evaluate_local(REFERENCE, step)
    assert not needs_ai and not result.passed
    assert result.failure_kind == "infrastructure" and not result.assessment_eligible
