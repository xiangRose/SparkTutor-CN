from collections import Counter
import json
from pyspark.sql import SparkSession, functions as f, Window
from pyspark.sql.types import StructType, StructField, LongType, StringType

def enrich_orders(orders, customer_history):
    # TODO: 实现题目约定，不修改下方课程测试。
    raise NotImplementedError("请完成当前函数")

# ---- 课程测试（请勿修改此行以下内容）----
if __name__ == "__main__":
    spark = (SparkSession.builder.appName("JoinCardinalityLab").master("local[2]")
             .config("spark.sql.shuffle.partitions", "2").getOrCreate())
    try:
        fact_schema = "order_id LONG, customer_id LONG, amount LONG"
        dim_schema = "customer_id LONG, segment STRING, updated_at LONG, version LONG"
        cases = [
            ([(10,1,100),(10,1,100),(11,2,40),(12,404,30),(13,None,20)],
             [(1,"old",1,1),(1,"current",2,1),(2,"lower_revision",3,1),(2,"premium",3,2),(None,"never_match",99,99)],
             [(10,1,100,"current"),(10,1,100,"current"),(11,2,40,"premium"),(12,404,30,None),(13,None,20,None)]),
            ([(31,7,5),(32,7,6),(33,8,7)],
             [(8,"new",14,2),(7,"newest_revision",10,9),(8,"old",14,1),(7,"older_revision",10,2)],
             [(31,7,5,"newest_revision"),(32,7,6,"newest_revision"),(33,8,7,"new")]),
            ([(41,9,8)], [], [(41,9,8,None)]),
            ([], [(2,"unused",1,1)], []),
        ]
        for orders_data, history_data, expected in cases:
            orders = spark.createDataFrame(orders_data, fact_schema)
            history = spark.createDataFrame(history_data, dim_schema)
            result = enrich_orders(orders, history)
            assert result is not None, "函数必须返回 DataFrame"
            assert result.columns == ["order_id","customer_id","amount","segment"], "保留题目约定的四列及顺序"
            actual = [tuple(row) for row in result.collect()]
            assert len(actual) == len(orders_data), "关联结果必须保留每条左表事实行，不能膨胀或丢行"
            assert Counter(actual) == Counter(expected), "先按更新时间和版本选最新维表，再左关联；保留重复事实、未匹配键和空键"
            assert result.schema["segment"].dataType == StringType(), "空结果仍须保留字符串分类列"
        sample_orders = spark.createDataFrame(cases[0][0], fact_schema)
        sample_history = spark.createDataFrame(cases[0][1], dim_schema)
        naive = sample_orders.join(sample_history, "customer_id", "left")
        print("JOIN_LAB_EVIDENCE=" + json.dumps({"factRows":sample_orders.count(),
              "rawLeftJoinRows":naive.count(),"correctRows":len(cases[0][2]),
              "checks":["latest-dimension","duplicate-facts","unmatched-keys","null-keys","empty-inputs"]}))
        print("所有测试通过！")
    finally:
        spark.stop()
