from collections import Counter
import json
from pyspark.sql import SparkSession, functions as f, Window
from pyspark.sql.types import StructType, StructField, LongType, StringType

def compare_join_plans(orders, profiles):
    # TODO: 实现题目约定，不修改下方课程测试。
    raise NotImplementedError("请完成当前函数")

# ---- 课程测试（请勿修改此行以下内容）----
if __name__ == "__main__":
    spark = (SparkSession.builder.appName("JoinPhysicalPlanLab").master("local[2]")
             .config("spark.sql.shuffle.partitions", "2").getOrCreate())
    previous_aqe = spark.conf.get("spark.sql.adaptive.enabled")
    previous_threshold = spark.conf.get("spark.sql.autoBroadcastJoinThreshold")
    try:
        # Fix this teaching observation condition; restore it in finally.
        spark.conf.set("spark.sql.adaptive.enabled", "false")
        orders = spark.createDataFrame([(20,1,10),(20,1,10),(21,2,20),(22,999,30),(23,None,40)],
                                       "order_id LONG, customer_id LONG, amount LONG")
        profiles = spark.createDataFrame([(1,"A"),(2,"B"),(None,"must_not_match")], "customer_id LONG, segment STRING")
        expected = Counter([(20,1,10,"A"),(20,1,10,"A"),(21,2,20,"B"),(22,999,30,None),(23,None,40,None)])
        result = compare_join_plans(orders, profiles)
        assert isinstance(result, dict) and set(result) == {"broadcast","merge"}, "返回 broadcast 和 merge 两个实际 DataFrame"
        plans = {}
        for name in ("broadcast", "merge"):
            frame = result[name]
            assert frame.columns == ["order_id","customer_id","amount","segment"]
            assert Counter(tuple(row) for row in frame.collect()) == expected, "更换策略不能改变左关联内容或删除重复事实"
            plans[name] = frame._jdf.queryExecution().executedPlan().toString()
        assert "BroadcastHashJoin" in plans["broadcast"], "广播结果的真实执行计划必须包含 BroadcastHashJoin"
        assert "SortMergeJoin" in plans["merge"], "合并结果的真实执行计划必须包含 SortMergeJoin，不能用同一份广播结果冒充"
        assert spark.conf.get("spark.sql.autoBroadcastJoinThreshold") == previous_threshold, "策略提示不能改写全局广播阈值"
        print("JOIN_PLAN_EVIDENCE=" + json.dumps({"equalResults":True,"factRows":5,
              "broadcastOperator":"BroadcastHashJoin","mergeOperator":"SortMergeJoin",
              "measurement":"operator observation only, not a performance benchmark"}))
        print("--- broadcast executed plan ---\n" + plans["broadcast"])
        print("--- merge executed plan ---\n" + plans["merge"])
        print("所有测试通过！")
    finally:
        spark.conf.set("spark.sql.adaptive.enabled", previous_aqe)
        spark.conf.set("spark.sql.autoBroadcastJoinThreshold", previous_threshold)
        spark.stop()
