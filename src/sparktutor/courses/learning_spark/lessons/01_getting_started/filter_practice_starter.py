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


def filter_ca(df):
    # TODO: 筛选 State 为 CA 的行，保留列和重复行。
    return None


# ---- 测试代码（请勿修改）----
if __name__ == "__main__":
    spark = (SparkSession.builder.appName("CandyPractice").master("local[2]")
             .config("spark.sql.shuffle.partitions", "2").getOrCreate())
    try:
        cases = [
            (CANDY_QUERY, [("CA", "Yellow", 2), ("CA", "Yellow", 4), ("CA", "Brown", 3)]),
            ("SELECT State, Color, CAST(Count AS BIGINT) AS Count FROM VALUES ('CA', 'Yellow', 2), ('CA', 'Yellow', 2), ('TX', 'Yellow', 5) AS candy(State, Color, Count)", [("CA", "Yellow", 2), ("CA", "Yellow", 2)]),
            ("SELECT 'TX' AS State, 'Yellow' AS Color, CAST(9 AS BIGINT) AS Count", []),
            (EMPTY_QUERY, []),
        ]
        for query, expected in cases:
            result = filter_ca(spark.sql(query))
            assert result is not None, "请返回 DataFrame"
            assert result.columns == ["State", "Color", "Count"], "保留原来的三列"
            assert Counter(tuple(row) for row in result.collect()) == Counter(expected), "只保留 CA 行，不聚合或丢失重复行"
        print("所有测试通过！")
    finally:
        spark.stop()
