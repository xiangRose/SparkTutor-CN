"""修复订单清洗和金额计算；只修改 debug_orders，保留测试区。"""

from pyspark.sql import SparkSession, functions as f
from pyspark.sql.types import DoubleType, IntegerType, StringType, StructField, StructType


def debug_orders(spark, orders):
    """把四元组原始订单清洗为类型化明细；当前实现有两处逻辑错误。"""
    schema = StructType([
        StructField("order_id", IntegerType(), False),
        StructField("product", StringType(), False),
        StructField("unit_price", StringType(), True),
        StructField("quantity", StringType(), True),
    ])
    raw = spark.createDataFrame(orders, schema)
    typed = (raw.withColumn("unit_price", f.expr("try_cast(unit_price AS DOUBLE)"))
             .withColumn("quantity", f.expr("try_cast(quantity AS INT)")))
    # TODO: 检查哪些订单应该保留。负单价或零数量能否单独构成无效记录？
    valid = typed.filter((f.col("unit_price") >= 0) | (f.col("quantity") > 0))
    # TODO: 核对单价 7.5、数量 3 应有的金额。
    return valid.withColumn("total", f.col("unit_price") + f.col("quantity"))


# ---- 课程测试（请勿修改此行以下内容）----
if __name__ == "__main__":
    spark = (SparkSession.builder.appName("DiagnosisOrdersTest").master("local[2]")
             .config("spark.sql.shuffle.partitions", "2").getOrCreate())
    try:
        orders = [
            (1, "book", "10.0", "2"), (2, "pen", "7.5", "3"),
            (3, "invalid_price", "N/A", "1"), (4, "zero_quantity", "4.0", "0"),
            (5, "negative_price", "-2.0", "2"), (6, "sample", "0.0", "1"),
            (7, "invalid_quantity", "2.0", "bad"), (8, "missing_price", None, "2"),
            (9, "negative_quantity", "2.0", "-1"), (10, "missing_quantity", "2.0", None),
        ]
        result = debug_orders(spark, orders)
        assert result is not None, "函数必须返回 DataFrame"
        assert result.columns == ["order_id", "product", "unit_price", "quantity", "total"]
        assert [field.dataType for field in result.schema] == [
            IntegerType(), StringType(), DoubleType(), IntegerType(), DoubleType()
        ], "检查解析后的单价、数量和金额类型"
        actual = [tuple(row) for row in result.orderBy("order_id").collect()]
        assert [row[0] for row in actual] == [1, 2, 6], f"有效订单应为 1、2、6，实际为 {actual}"
        assert actual == [(1, "book", 10.0, 2, 20.0), (2, "pen", 7.5, 3, 22.5),
                          (6, "sample", 0.0, 1, 0.0)], "金额应为单价乘数量"
        another = debug_orders(spark, [(20, "cable", "2.5", "4"), (21, "gift", "0", "2")])
        assert [tuple(row) for row in another.orderBy("order_id").collect()] == [
            (20, "cable", 2.5, 4, 10.0), (21, "gift", 0.0, 2, 0.0)
        ], "实现必须适用于不同订单输入"
        empty = debug_orders(spark, [])
        assert empty.schema == result.schema and empty.count() == 0, "空输入应保留相同 Schema"
        print("所有测试通过！")
    finally:
        spark.stop()
