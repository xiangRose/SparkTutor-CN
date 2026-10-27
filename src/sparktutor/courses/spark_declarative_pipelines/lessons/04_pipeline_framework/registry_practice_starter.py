"""函数注册分步练习；注册函数对象，稍后才执行。"""


def register_flow(flows, func):
    # TODO: 按函数名更新传入的 flows 字典，返回原函数，不在注册时调用它。
    return None


# ---- 测试代码（请勿修改）----
if __name__ == "__main__":
    calls = []
    flows = {}

    def bronze(session):
        calls.append(("bronze", session))
        return "raw rows"

    returned = register_flow(flows, bronze)
    assert returned is bronze, "返回原函数对象，而不是执行结果"
    assert flows == {"bronze": bronze}, "以函数名为键，保存原函数对象"
    assert calls == [], "注册阶段不能执行数据处理函数"

    def silver(session):
        calls.append(("silver", session))
        return "clean rows"

    assert register_flow(flows, silver) is silver
    assert flows == {"bronze": bronze, "silver": silver}, "注册新步骤不能丢失已有步骤"
    assert calls == [], "多个步骤注册完成后仍不应有执行记录"
    assert flows["bronze"]("session") == "raw rows"
    assert calls == [("bronze", "session")], "只有显式调用时才执行"

    def bronze(session):
        return "replacement rows"

    assert register_flow(flows, bronze) is bronze
    assert flows == {"bronze": bronze, "silver": silver}, "同名步骤替换旧函数，其他步骤保留"
    print("所有测试通过！")
