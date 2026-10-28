"""Load bounded, declarative coaching nodes beside real registered lessons.

Configuration supplies plain author text and constrained choices, never Python,
commands, scoring rules, or navigation. An invalid file/task is unavailable to
the coach without disabling other lessons or changing course assessment.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from types import MappingProxyType
from typing import Mapping

import yaml

from sparktutor.engine.exercise_catalog import build_exercise_catalog


SCHEMA_VERSION = "coach-catalog-v1"
MAX_FILE_BYTES = 65_536
PROBE_KINDS = frozenset({"row_count_vs_quantity"})
EVIDENCE_KINDS = frozenset({"task", "event"})


@dataclass(frozen=True)
class CoachHint:
    question: str
    guidance: str
    reflection: str


@dataclass(frozen=True)
class CoachHypothesis:
    summary: str
    evidence_kinds: tuple[str, ...]
    levels: tuple[CoachHint, ...]


@dataclass(frozen=True)
class CoachTask:
    course_id: str
    lesson_id: str
    step_id: str
    topic: str
    contract: str
    knowledge_components: tuple[str, ...]
    validation_kind: str
    validation_guidance: str
    hypotheses: Mapping[str, CoachHypothesis]
    probe_kind: str | None
    probe_hypotheses: tuple[str, ...]
    fingerprint: str


@dataclass(frozen=True)
class CoachConfigurationIssue:
    course_id: str
    lesson_id: str
    step_id: str | None
    code: str


@dataclass(frozen=True)
class CoachCatalog:
    tasks: Mapping[tuple[str, str, str], CoachTask]
    issues: tuple[CoachConfigurationIssue, ...]


class CoachConfigurationError(ValueError):
    """A fixed reason code; untrusted YAML text is not echoed to learners."""


class _StrictSafeLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise CoachConfigurationError("aliases_not_allowed")
        depth = getattr(self, "_coach_depth", 0)
        if depth >= 20:
            raise CoachConfigurationError("configuration_too_deep")
        self._coach_depth = depth + 1
        try:
            return super().compose_node(parent, index)
        finally:
            self._coach_depth = depth

    def construct_mapping(self, node, deep=False):
        if not isinstance(node, yaml.MappingNode):
            raise CoachConfigurationError("mapping_required")
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise CoachConfigurationError("duplicate_or_nonstring_key")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def _fields(value, required, optional=()):
    if (not isinstance(value, dict) or set(value) - set(required) - set(optional)
            or not set(required).issubset(value)):
        raise CoachConfigurationError("invalid_fields")


def _text(value, limit=1200):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise CoachConfigurationError("invalid_text")
    return value.strip()


def _strings(value, limit=32):
    if (not isinstance(value, list) or not 1 <= len(value) <= limit
            or any(not isinstance(item, str) or not item.strip() or len(item) > 100 for item in value)
            or len(set(value)) != len(value)):
        raise CoachConfigurationError("invalid_string_list")
    return tuple(value)


def _task(raw, entry, fingerprint):
    _fields(raw, {"stepId", "topic", "contract", "knowledgeComponents", "validation", "hypotheses"}, {"probe"})
    step_id = _text(raw["stepId"], 100)
    if step_id != entry["stepId"] or entry["taskType"] != "script":
        raise CoachConfigurationError("real_script_task_required")
    topic = _text(raw["topic"], 64)
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", topic):
        raise CoachConfigurationError("invalid_topic")
    components = _strings(raw["knowledgeComponents"])
    if not set(components).issubset(entry.get("knowledgeComponents", [])):
        raise CoachConfigurationError("unknown_knowledge_component")
    validation = raw["validation"]
    _fields(validation, {"kind", "guidance"})
    if validation["kind"] != "course_tests":
        raise CoachConfigurationError("unsupported_validation_kind")
    validation_guidance = _text(validation["guidance"], 1600)
    hypotheses = raw["hypotheses"]
    if not isinstance(hypotheses, dict) or not 1 <= len(hypotheses) <= 12 or "uncertain" not in hypotheses:
        raise CoachConfigurationError("uncertain_hypothesis_required")
    parsed = {}
    for identifier, hypothesis in hypotheses.items():
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", identifier):
            raise CoachConfigurationError("invalid_hypothesis_id")
        _fields(hypothesis, {"summary", "evidenceRequirements", "levels"})
        requirements = hypothesis["evidenceRequirements"]
        _fields(requirements, {"anyOf"})
        kinds = _strings(requirements["anyOf"], limit=2)
        if not set(kinds).issubset(EVIDENCE_KINDS):
            raise CoachConfigurationError("unsupported_evidence_kind")
        levels = hypothesis["levels"]
        if not isinstance(levels, list) or len(levels) != 3:
            raise CoachConfigurationError("three_levels_required")
        hints = []
        for level in levels:
            _fields(level, {"question", "guidance", "reflection"})
            hints.append(CoachHint(*(_text(level[key]) for key in ("question", "guidance", "reflection"))))
        parsed[identifier] = CoachHypothesis(_text(hypothesis["summary"], 300), kinds, tuple(hints))
    # There must always be a task-grounded fallback when no run has occurred.
    if "task" not in parsed["uncertain"].evidence_kinds:
        raise CoachConfigurationError("uncertain_requires_task_evidence")
    probe = raw.get("probe")
    probe_kind, probe_hypotheses = None, ()
    if probe is not None:
        _fields(probe, {"kind", "allowedHypotheses"})
        if probe["kind"] not in PROBE_KINDS:
            raise CoachConfigurationError("unsupported_probe_kind")
        probe_hypotheses = _strings(probe["allowedHypotheses"], limit=12)
        if not set(probe_hypotheses).issubset(parsed):
            raise CoachConfigurationError("unknown_probe_hypothesis")
        probe_kind = probe["kind"]
    return CoachTask(entry["courseId"], entry["lessonId"], step_id, topic,
                     _text(raw["contract"], 5000), components, validation["kind"], validation_guidance,
                     MappingProxyType(parsed), probe_kind, probe_hypotheses, fingerprint)


def load_coach_catalog(registry, exercise_catalog=None) -> CoachCatalog:
    entries = build_exercise_catalog(registry) if exercise_catalog is None else exercise_catalog
    lessons = {}
    for entry in entries:
        lessons.setdefault((entry["courseId"], entry["lessonId"]), {})[entry["stepId"]] = entry
    tasks, issues = {}, []
    courses_dir = Path(registry.courses_dir).resolve()
    for (course_id, lesson_id), lesson_entries in lessons.items():
        path = courses_dir / course_id / "lessons" / lesson_id / "coach.yaml"
        try:
            if not path.exists():
                continue
            if not path.resolve().is_relative_to(courses_dir) or not path.is_file():
                raise CoachConfigurationError("configuration_path_outside_courses")
            if path.stat().st_size > MAX_FILE_BYTES:
                raise CoachConfigurationError("configuration_too_large")
            content = path.read_bytes()
            if len(content) > MAX_FILE_BYTES:
                raise CoachConfigurationError("configuration_too_large")
            document = yaml.load(content.decode("utf-8"), Loader=_StrictSafeLoader)
            _fields(document, {"schemaVersion", "tasks"})
            if document["schemaVersion"] != SCHEMA_VERSION:
                raise CoachConfigurationError("unsupported_schema_version")
            configured = document["tasks"]
            if not isinstance(configured, list) or not 1 <= len(configured) <= 64:
                raise CoachConfigurationError("invalid_task_list")
        except (OSError, UnicodeError, yaml.YAMLError, CoachConfigurationError, RecursionError, RuntimeError) as error:
            code = str(error) if isinstance(error, CoachConfigurationError) else "configuration_unreadable"
            issues.append(CoachConfigurationIssue(course_id, lesson_id, None, code))
            continue
        fingerprint = hashlib.sha256(content).hexdigest()
        seen = set()
        for raw in configured:
            step_id = raw.get("stepId") if isinstance(raw, dict) else None
            key = (course_id, lesson_id, step_id) if isinstance(step_id, str) else None
            try:
                if key is None or step_id not in lesson_entries:
                    raise CoachConfigurationError("unknown_step_id")
                if step_id in seen:
                    tasks.pop(key, None)
                    raise CoachConfigurationError("duplicate_step_id")
                seen.add(step_id)
                tasks[key] = _task(raw, lesson_entries[step_id], fingerprint)
            except (CoachConfigurationError, TypeError, KeyError) as error:
                code = str(error) if isinstance(error, CoachConfigurationError) else "invalid_task_configuration"
                issues.append(CoachConfigurationIssue(course_id, lesson_id,
                              step_id if isinstance(step_id, str) else None, code))
    return CoachCatalog(MappingProxyType(tasks), tuple(issues))
