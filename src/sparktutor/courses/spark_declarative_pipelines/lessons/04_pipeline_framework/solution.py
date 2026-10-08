"""
Pipeline 框架 - 参考答案

完整可用的 Pipeline 类：装饰器注册、依赖检测、
拓扑排序和执行。
"""

import inspect
import re
from collections import deque


class Pipeline:
    def __init__(self, spark):
        self.spark = spark
        self._flows = {}

    def materialized_view(self):
        """返回一个装饰器，将被装饰的函数注册到 self._flows 中。"""
        def decorator(func):
            self._flows[func.__name__] = func
            return func
        return decorator

    def _detect_deps(self, func):
        """返回函数中通过 spark.table('...') 引用的表名列表。"""
        source = inspect.getsource(func)
        return re.findall(r'spark\.table\(["\'](\w+)["\']\)', source)

    def _topo_sort(self, graph):
        """
        使用 Kahn 算法进行拓扑排序。
        graph: 字典，映射 节点名 -> 依赖名列表
        返回：按执行顺序排列的节点名列表
        如果检测到循环则抛出 ValueError。
        """
        in_degree = {n: 0 for n in graph}
        for node, deps in graph.items():
            for dep in set(deps):
                if dep in in_degree:
                    in_degree[node] += 1

        queue = deque(n for n, d in in_degree.items() if d == 0)
        order = []

        while queue:
            node = queue.popleft()
            order.append(node)
            for candidate, deps in graph.items():
                if node in deps:
                    in_degree[candidate] -= 1
                    if in_degree[candidate] == 0:
                        queue.append(candidate)

        if len(order) != len(graph):
            raise ValueError("Pipeline 依赖中检测到循环")
        return order

    def run(self):
        """构建依赖图、排序，并按顺序执行每个 flow。"""
        # 构建依赖图
        graph = {}
        for name, func in self._flows.items():
            graph[name] = self._detect_deps(func)

        # 排序
        order = self._topo_sort(graph)

        # 执行
        for name in order:
            df = self._flows[name](self.spark)
            df.createOrReplaceTempView(name)
            print(f"已注册临时视图：{name}（{df.count()} 行）")


# ---- 测试代码 ----
if __name__ == "__main__":
    from pyspark.sql import SparkSession, functions as f

    spark = SparkSession.builder.appName("PipelineTest").master("local[*]").getOrCreate()
    pipe = Pipeline(spark)
    executed = []

    # 故意逆序注册，不能靠字典插入顺序侥幸通过。
    @pipe.materialized_view()
    def gold_orders(spark):
        executed.append("gold")
        return spark.table("silver_orders").agg(f.sum("total").alias("revenue"))

    @pipe.materialized_view()
    def silver_orders(spark):
        executed.append("silver")
        return spark.table("bronze_orders").withColumn("total", f.col("price") * f.col("qty"))

    @pipe.materialized_view()
    def bronze_orders(spark):
        executed.append("bronze")
        return spark.createDataFrame(
            [("1", "widget", 9.99, 2), ("2", "gadget", 24.99, 1)],
            ["order_id", "product", "price", "qty"],
        )

    assert set(pipe._flows) == {"bronze_orders", "silver_orders", "gold_orders"}
    assert pipe._detect_deps(silver_orders) == ["bronze_orders"]
    assert pipe._detect_deps(gold_orders) == ["silver_orders"]
    assert pipe._topo_sort({"gold": ["silver"], "silver": ["bronze", "bronze"], "bronze": []}) == ["bronze", "silver", "gold"]
    assert pipe._topo_sort({"bronze": ["external_source"]}) == ["bronze"]
    try:
        pipe._topo_sort({"a": ["b"], "b": ["a"]})
    except ValueError:
        pass
    else:
        raise AssertionError("循环依赖必须抛出 ValueError")
    pipe.run()
    assert executed == ["bronze", "silver", "gold"], f"执行顺序错误：{executed}"
    assert spark.table("bronze_orders").count() == 2
    assert abs(spark.table("gold_orders").first()["revenue"] - 44.97) < 1e-8
    print("所有测试通过！")
    spark.stop()
