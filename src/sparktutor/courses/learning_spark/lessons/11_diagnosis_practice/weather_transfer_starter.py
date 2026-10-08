"""把显式 Schema 与布尔派生列迁移到天气观测；保留测试区。"""

from pyspark.sql import SparkSession, functions as f
from pyspark.sql.types import (
    ArrayType, BooleanType, DoubleType, IntegerType, StringType, StructField, StructType,
)


def transfer_weather_schema(spark, observations):
    """显式建模五个原始字段，追加符合题目阈值的 needs_review 布尔列。"""
    # TODO: 定义 observation_id、station、temperature_c、rainfall_mm、tags 的 Schema。
    # TODO: 从 observations 创建 DataFrame，并追加 needs_review。
    return None


# ---- 课程测试（请勿修改此行以下内容）----
if __name__ == "__main__":
    spark = (SparkSession.builder.appName("DiagnosisWeatherTest").master("local[2]")
             .config("spark.sql.shuffle.partitions", "2").getOrCreate())
    try:
        observations = [
            (1, "A", 22.0, 0.0, ["morning"]),
            (2, "B", 36.0, 0.0, ["afternoon", "manual"]),
            (3, "C", 20.0, 60.0, []),
            (4, "D", 35.0, 2.0, ["temperature_boundary"]),
            (5, "E", 18.0, 50.0, ["rainfall_boundary"]),
            (6, "F", 37.0, 70.0, ["both"]),
        ]
        result = transfer_weather_schema(spark, observations)
        assert result is not None, "函数必须返回 DataFrame"
        original_columns = ["observation_id", "station", "temperature_c", "rainfall_mm", "tags"]
        assert result.columns == original_columns + ["needs_review"]
        expected_types = [IntegerType(), StringType(), DoubleType(), DoubleType(),
                          ArrayType(StringType()), BooleanType()]
        assert [field.dataType for field in result.schema] == expected_types, "检查显式 Schema 和布尔列类型"
        rows = result.orderBy("observation_id").collect()
        assert [tuple(row[name] for name in original_columns) for row in rows] == observations, "保留全部原始数据及数组内容"
        assert [row.needs_review for row in rows] == [False, True, True, True, True, True], "检查或条件及包含边界的比较"
        another = transfer_weather_schema(spark, [(20, "G", 34.99, 49.99, []), (21, "H", -5.0, 50.0, ["cold"])])
        assert [(row.observation_id, row.needs_review) for row in another.orderBy("observation_id").collect()] == [
            (20, False), (21, True)
        ], "标志必须由当前输入计算"
        empty = transfer_weather_schema(spark, [])
        assert empty.schema == result.schema and empty.count() == 0, "空输入应返回具有相同 Schema 的空 DataFrame"
        print("所有测试通过！")
    finally:
        spark.stop()
