"""Run the course-owned test harness against a student's implementation.

The learner's editable __main__ block is never used as proof of correctness.
This is a local teaching runner, not a sandbox for hostile Python code.
"""

from __future__ import annotations

import ast


VALIDATION_MARKER = "[sparktutor] COURSE_TESTS_PASSED"


def _is_main_guard(node: ast.stmt) -> bool:
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return False
    test = node.test
    if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return False
    values = (test.left, test.comparators[0])
    return any(isinstance(v, ast.Name) and v.id == "__name__" for v in values) and any(
        isinstance(v, ast.Constant) and v.value == "__main__" for v in values
    )


def build_validation_script(student_code: str, starter_code: str) -> str:
    """Embed the unedited course tests and keep source available to inspect."""
    ast.parse(student_code)
    template = ast.parse(starter_code)
    main_blocks = [node for node in template.body if _is_main_guard(node)]
    if not main_blocks:
        raise ValueError("本练习缺少课程测试入口，请联系课程维护者。")
    setup = ast.unparse(ast.Module(
        body=[node for node in template.body if not _is_main_guard(node)], type_ignores=[]
    ))
    tests = ast.unparse(ast.Module(
        body=[stmt for block in main_blocks for stmt in block.body], type_ignores=[]
    ))
    # Separate scopes prevent ordinary student variable names from replacing the
    # runner's source strings. linecache allows inspect.getsource in Pipeline.
    return f'''import linecache as _linecache

def _run_course_tests():
    student_source = {student_code!r}
    filename = "<sparktutor-student>"
    setup_source = {setup!r}
    test_source = {tests!r}
    for source_name, source in (
        (filename, student_source),
        ("<sparktutor-course-setup>", setup_source),
        ("<sparktutor-course-tests>", test_source),
    ):
        _linecache.cache[source_name] = (
            len(source), None, source.splitlines(keepends=True), source_name
        )
    namespace = {{"__name__": "sparktutor_student", "__file__": filename}}
    exec(compile(setup_source, "<sparktutor-course-setup>", "exec", optimize=0), namespace)
    exec(compile(student_source, filename, "exec"), namespace)
    namespace["__name__"] = "__main__"
    exec(compile(test_source, "<sparktutor-course-tests>", "exec", optimize=0), namespace)
    print({VALIDATION_MARKER!r})

_run_course_tests()
'''
