"""
Pipeline 框架 - 起始代码

完成下面的 Pipeline 类。你需要实现：
1. materialized_view() -- 注册函数的装饰器
2. _detect_deps()      -- 检查函数源码中的 spark.table() 调用
3. _topo_sort()        -- 使用 Kahn 算法进行拓扑排序
4. run()               -- 构建图、排序、按序执行

运行此文件来测试你的实现。预期输出为：
  Registered temporary view: bronze_orders (... rows)
  Registered temporary view: silver_orders (... rows)
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
        # TODO: 实现
        pass

    def _detect_deps(self, func):
        """返回函数中通过 spark.table('...') 引用的表名列表。"""
        # TODO: 实现
        pass

    def _topo_sort(self, graph):
        """
        使用 Kahn 算法进行拓扑排序。
        graph: 字典，映射 节点名 -> 依赖名列表
        返回：按执行顺序排列的节点名列表
        如果检测到循环则抛出 ValueError。
        """
        # TODO: 实现
        pass

    def run(self):
        """构建依赖图、排序，并按顺序执行每个 flow。"""
        # TODO: 实现
        pass


# ---- 测试代码（请勿修改此行以下内容）----
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
