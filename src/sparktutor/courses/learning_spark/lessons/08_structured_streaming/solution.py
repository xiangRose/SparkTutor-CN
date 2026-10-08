"""
Structured Streaming - 参考答案

以批处理模式模拟流式单词计数。
"""

from pyspark.sql import SparkSession, functions as f
from pyspark.sql.types import StructType, StructField, StringType


TEXT_DATA = [
    ("hello world hello spark",),
    ("spark streaming is great",),
    ("hello streaming world",),
    ("spark spark spark",),
    ("world of streaming data",),
    ("hello data world",),
]

SCHEMA = StructType([
    StructField("line", StringType(), False),
])


def streaming_word_count(spark):
    """以批处理模式模拟流式单词计数。"""

    lines_df = spark.createDataFrame(TEXT_DATA, SCHEMA)

    words_df = lines_df.select(
        f.explode(f.split("line", " ")).alias("word")
    )

    counts_df = words_df.groupBy("word").count()

    result = counts_df.orderBy(f.col("count").desc())

    return result


def streaming_windowed_count(spark):
    """以批处理模式演示窗口聚合概念。"""
    from pyspark.sql.types import TimestampType
    from datetime import datetime, timedelta

    base_time = datetime(2024, 1, 1, 12, 0, 0)
    timestamped_data = [
        ("hello spark", base_time),
        ("hello world", base_time + timedelta(minutes=2)),
        ("spark streaming", base_time + timedelta(minutes=5)),
        ("hello spark", base_time + timedelta(minutes=8)),
        ("streaming world", base_time + timedelta(minutes=12)),
        ("spark data", base_time + timedelta(minutes=15)),
    ]

    ts_schema = StructType([
        StructField("line", StringType(), False),
        StructField("event_time", TimestampType(), False),
    ])

    ts_df = spark.createDataFrame(timestamped_data, ts_schema)

    words_df = ts_df.select(
        f.explode(f.split("line", " ")).alias("word"),
        "event_time"
    )

    windowed_counts = (words_df
        .groupBy(f.window("event_time", "10 minutes"), "word")
        .count()
        .orderBy("window", f.col("count").desc()))

    return windowed_counts


# ---- 测试代码 ----
if __name__ == "__main__":
    spark = (SparkSession.builder
        .appName("StreamingTest")
        .master("local[*]")
        .getOrCreate())

    wc = streaming_word_count(spark)
    assert wc is not None, "streaming_word_count 返回了 None"
    top_word = wc.collect()[0]
    assert top_word["word"] in ("spark", "hello", "world"), f"意外的排名第一的单词：{top_word}"
    print("单词计数：")
    wc.show(truncate=False)

    ww = streaming_windowed_count(spark)
    assert ww is not None, "streaming_windowed_count 返回了 None"
    assert "window" in ww.columns, "缺少 window 列"
    print("\n窗口单词计数：")
    ww.show(truncate=False)

    counts = {row.word: row["count"] for row in wc.collect()}
    assert counts == {"hello": 4, "world": 4, "spark": 5, "streaming": 3, "is": 1, "great": 1, "of": 1, "data": 2}
    assert [row["count"] for row in wc.collect()] == sorted(counts.values(), reverse=True)
    from datetime import timedelta
    windows = {(row.window.start.minute, row.word): row["count"] for row in ww.collect()}
    assert windows == {(0, "hello"): 3, (0, "spark"): 3, (0, "world"): 1, (0, "streaming"): 1,
                       (10, "streaming"): 1, (10, "world"): 1, (10, "spark"): 1, (10, "data"): 1}
    assert all(row.window.end - row.window.start == timedelta(minutes=10) for row in ww.collect())

    print("所有测试通过！")
    spark.stop()
