"""依赖排序分步练习参考答案。"""
from collections import deque
from copy import deepcopy


def dependency_order(graph):
    remaining = {name: len({dependency for dependency in dependencies if dependency in graph})
                 for name, dependencies in graph.items()}
    ready = deque(name for name, count in remaining.items() if count == 0)
    order = []
    while ready:
        name = ready.popleft()
        order.append(name)
        for candidate, dependencies in graph.items():
            if name in dependencies:
                remaining[candidate] -= 1
                if remaining[candidate] == 0:
                    ready.append(candidate)
    if len(order) != len(graph):
        raise ValueError("管道依赖中检测到循环")
    return order


# ---- 测试代码（请勿修改）----
if __name__ == "__main__":
    cases = [
        {"gold": ["silver"], "silver": ["bronze", "bronze"], "bronze": []},
        {"report": ["orders", "users"], "users": ["external_users"], "orders": [], "audit": []},
        {"bronze": ["external_source"]},
        {},
    ]
    for graph in cases:
        before = deepcopy(graph)
        order = dependency_order(graph)
        assert isinstance(order, list), "请返回节点名列表"
        assert graph == before, "排序不能修改输入依赖图"
        assert len(order) == len(graph) and set(order) == set(graph), "每个图内节点恰好出现一次，不加入图外节点"
        position = {name: index for index, name in enumerate(order)}
        for name, dependencies in graph.items():
            assert all(position[dependency] < position[name] for dependency in set(dependencies) if dependency in graph), (
                "步骤必须在它依赖的图内节点之后执行"
            )
    for graph in [{"a": ["b"], "b": ["a"]}, {"self": ["self"]}]:
        before = deepcopy(graph)
        try:
            dependency_order(graph)
        except ValueError:
            pass
        else:
            raise AssertionError("循环依赖必须抛出 ValueError，不能返回部分顺序")
        assert graph == before, "检测环时也不能修改输入依赖图"
    print("所有测试通过！")
