"""第一课分步练习。只修改目标函数，测试提供 DataFrame 输入。"""
from collections import Counter
from pyspark.sql import SparkSession, functions as f

# 仅供测试准备小表，计算在 Spark 引擎完成；学习目标不要求编写 SQL。
CANDY_QUERY = """
SELECT State, Color, CAST(Count AS BIGINT) AS Count
FROM VALUES ('CA', 'Yellow', 2), ('CA', 'Yellow', 4),
            ('CA', 'Brown', 3), ('TX', 'Yellow', 5)
AS candy(State, Color, Count)
"""
EMPTY_QUERY = """
SELECT CAST(NULL AS STRING) AS State, CAST(NULL AS STRING) AS Color,
       CAST(NULL AS BIGINT) AS Count WHERE FALSE
"""


def summarize_counts(df):
    return df.groupBy("State", "Color").agg(f.sum("Count").alias("Total"))


# ---- 测试代码（请勿修改）----
if __name__ == "__main__":
    spark = (SparkSession.builder.appName("CandyPractice").master("local[2]")
             .config("spark.sql.shuffle.partitions", "2").getOrCreate())
    try:
        cases = [
            (CANDY_QUERY, [("CA", "Yellow", 6), ("CA", "Brown", 3), ("TX", "Yellow", 5)]),
            ("SELECT State, Color, CAST(Count AS BIGINT) AS Count FROM VALUES ('CA', 'Blue', 3), ('CA', 'Blue', 3), ('TX', 'Blue', 7) AS candy(State, Color, Count)", [("CA", "Blue", 6), ("TX", "Blue", 7)]),
            (EMPTY_QUERY, []),
        ]
        for query, expected in cases:
            result = summarize_counts(spark.sql(query))
            assert result is not None, "请返回 DataFrame"
            assert result.columns == ["State", "Color", "Total"], "检查分组键和聚合列名"
            assert Counter(tuple(row) for row in result.collect()) == Counter(expected), "按州和颜色求数量总和，不是记录条数"
        print("所有测试通过！")
    finally:
        spark.stop()
