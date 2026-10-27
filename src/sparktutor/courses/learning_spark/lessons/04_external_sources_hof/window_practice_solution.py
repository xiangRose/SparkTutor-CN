"""窗口排名分步练习参考答案。"""
from collections import Counter
from pyspark.sql import SparkSession, Window, functions as f


def rank_orders(df):
    window = Window.partitionBy("region").orderBy(f.col("total").desc())
    return df.withColumn("region_rank", f.rank().over(window))


# ---- 测试代码（请勿修改）----
if __name__ == "__main__":
    spark = (SparkSession.builder.appName("WindowPractice").master("local[2]")
             .config("spark.sql.shuffle.partitions", "2").getOrCreate())
    try:
        cases = [
            (
                "SELECT CAST(order_id AS INT) AS order_id, region, CAST(total AS DOUBLE) AS total "
                "FROM VALUES (1, 'West', 100), (4, 'East', 40), (2, 'West', 300), "
                "(5, 'East', 50), (3, 'West', 300) AS orders(order_id, region, total)",
                [(1, "West", 100.0, 3), (4, "East", 40.0, 2),
                 (2, "West", 300.0, 1), (5, "East", 50.0, 1), (3, "West", 300.0, 1)],
            ),
            (
                "SELECT 7 AS order_id, 'North' AS region, CAST(10 AS DOUBLE) AS total",
                [(7, "North", 10.0, 1)],
            ),
            (
                "SELECT order_id, region, CAST(total AS DOUBLE) AS total FROM VALUES "
                "(8, 'North', 20), (8, 'North', 20), (9, 'North', 10) AS orders(order_id, region, total)",
                [(8, "North", 20.0, 1), (8, "North", 20.0, 1), (9, "North", 10.0, 3)],
            ),
            (
                "SELECT CAST(NULL AS INT) AS order_id, CAST(NULL AS STRING) AS region, "
                "CAST(NULL AS DOUBLE) AS total WHERE FALSE",
                [],
            ),
        ]
        for query, expected in cases:
            result = rank_orders(spark.sql(query))
            assert result is not None, "请返回 DataFrame"
            assert result.columns == ["order_id", "region", "total", "region_rank"], "保留原列并添加 region_rank"
            assert Counter(tuple(row) for row in result.collect()) == Counter(expected), (
                "每区独立降序排名；并列名次为 1、1、3，不是 1、2、3 或 1、1、2；保留全部订单"
            )
        print("所有测试通过！")
    finally:
        spark.stop()
