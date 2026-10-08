"""把 M&M 的分组求和结构迁移到传感器上报；保留测试区。"""

from pyspark.sql import SparkSession, functions as f
from pyspark.sql.types import IntegerType, LongType, StringType, StructField, StructType


def transfer_sensor_aggregation(spark, readings, target_site):
    """按站点和类型求上报条数总和，筛选目标站点并按规则排序。"""
    schema = StructType([
        StructField("site", StringType(), False),
        StructField("sensor_type", StringType(), False),
        StructField("reading_count", IntegerType(), False),
    ])
    records = spark.createDataFrame(readings, schema)
    # TODO: 分组求和并命名 total_readings。
    # TODO: 保留 target_site；总数降序，类型名字升序。
    return None


# ---- 课程测试（请勿修改此行以下内容）----
if __name__ == "__main__":
    spark = (SparkSession.builder.appName("DiagnosisSensorsTest").master("local[2]")
             .config("spark.sql.shuffle.partitions", "2").getOrCreate())
    try:
        readings = [
            ("north", "temperature", 3), ("north", "temperature", 7),
            ("north", "temperature", 0), ("north", "humidity", 4),
            ("north", "humidity", 5), ("north", "pressure", 9),
            ("south", "temperature", 1000), ("south", "humidity", 2),
        ]
        result = transfer_sensor_aggregation(spark, readings, "north")
        assert result is not None, "函数必须返回 DataFrame"
        assert result.columns == ["site", "sensor_type", "total_readings"]
        assert [field.dataType for field in result.schema] == [StringType(), StringType(), LongType()]
        assert [tuple(row) for row in result.collect()] == [
            ("north", "temperature", 10), ("north", "humidity", 9), ("north", "pressure", 9)
        ], "检查求和、站点筛选以及并列总数的排序"
        south = transfer_sensor_aggregation(spark, readings, "south")
        assert [tuple(row) for row in south.collect()] == [
            ("south", "temperature", 1000), ("south", "humidity", 2)
        ], "目标站点来自参数，不能写死 north"
        another = transfer_sensor_aggregation(spark, [("west", "light", 8), ("west", "light", 12)], "west")
        assert [tuple(row) for row in another.collect()] == [("west", "light", 20)], "应累计条数而非统计批次数"
        for source, target in [(readings, "missing"), ([], "north")]:
            empty = transfer_sensor_aggregation(spark, source, target)
            assert empty.schema == result.schema and empty.count() == 0, "空结果也必须保留相同 Schema"
        print("所有测试通过！")
    finally:
        spark.stop()
