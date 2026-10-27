"""Static checks for the introductory SparkSession builder exercise.

Only the small, straight-line builder vocabulary taught in this exercise is
interpreted. No learner code is executed and no Spark dependency is imported.
"""

from __future__ import annotations

import ast


def validate_spark_session(
    source: str, *, variable: str, app_name: str, master: str,
) -> list[tuple[int | None, str]]:
    tree = ast.parse(source)
    bindings: dict = {}
    unsupported: list[int] = []

    def value(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return bindings.get(node.id)
        if isinstance(node, ast.Attribute):
            owner = value(node.value)
            if owner == ("class", "SparkSession") and node.attr == "builder":
                return ("builder", {})
            if isinstance(owner, tuple) and owner[0] == "module":
                path = owner[1] + "." + node.attr
                if path == "pyspark.sql.SparkSession":
                    return ("class", "SparkSession")
                if path in {"pyspark.sql", "pyspark"}:
                    return ("module", path)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = value(node.func.value)
            if isinstance(owner, tuple) and owner[0] == "builder":
                method = node.func.attr
                if method in {"appName", "master"} and len(node.args) == 1 and not node.keywords:
                    argument = value(node.args[0])
                    if isinstance(argument, str):
                        owner[1][method] = argument
                        return owner
                if method == "getOrCreate" and not node.args and not node.keywords:
                    return ("session", owner[1].copy())
        unsupported.append(getattr(node, "lineno", 1))
        return None

    for statement in tree.body:
        if isinstance(statement, ast.ImportFrom):
            for alias in statement.names:
                bindings[alias.asname or alias.name] = (
                    ("class", "SparkSession")
                    if statement.level == 0 and statement.module == "pyspark.sql" and alias.name == "SparkSession"
                    else ("module", "pyspark.sql")
                    if statement.level == 0 and statement.module == "pyspark" and alias.name == "sql"
                    else None
                )
        elif isinstance(statement, ast.Import):
            for alias in statement.names:
                path = alias.name if alias.asname else alias.name.split(".")[0]
                bindings[alias.asname or path] = ("module", path) if alias.name in {"pyspark", "pyspark.sql"} else None
        elif isinstance(statement, (ast.Assign, ast.AnnAssign)):
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            if statement.value is None or any(not isinstance(target, ast.Name) for target in targets):
                unsupported.append(statement.lineno)
                continue
            resolved = value(statement.value)
            for target in targets:
                bindings[target.id] = resolved
        elif isinstance(statement, ast.Expr):
            # Permit docstrings and separate builder configuration statements.
            value(statement.value)
        else:
            unsupported.append(statement.lineno)

    result = bindings.get(variable)
    feedback = []
    if not (isinstance(result, tuple) and result[0] == "session"):
        feedback.append((None, f"请导入 pyspark.sql.SparkSession，调用 builder 的 getOrCreate()，并将返回的会话赋给变量 {variable}。"))
    else:
        if result[1].get("appName") != app_name:
            feedback.append((None, f"应用名称应为 {app_name!r}，请检查 appName() 的参数。"))
        if result[1].get("master") != master:
            feedback.append((None, f"master 应为 {master!r}，请检查 master() 的参数。"))
    if unsupported:
        feedback.append((unsupported[0], "本题的本地检查支持顶层导入、变量赋值和 appName()/master()/getOrCreate() 调用；请按题面示例编写这段创建会话的代码。"))
    return feedback
