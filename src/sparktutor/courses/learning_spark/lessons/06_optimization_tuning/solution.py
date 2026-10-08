"""
优化与调优 - 参考答案

演示缓存和分区优化。
"""

from pyspark.sql import SparkSession, functions as f
from pyspark.sql.types import (
    StructType, StructField, StringType, IntegerType, DoubleType
)
import random


def generate_data(n=10000):
    """生成示例销售数据。"""
    departments = ["Engineering", "Marketing", "Sales", "Support", "HR"]
    regions = ["North", "South", "East", "West"]
    random.seed(42)
    return [
        (i, random.choice(departments), random.choice(regions),
         round(random.uniform(1000, 50000), 2))
        for i in range(n)
    ]


SCHEMA = StructType([
    StructField("id", IntegerType(), False),
    StructField("department", StringType(), False),
    StructField("region", StringType(), False),
    StructField("revenue", DoubleType(), False),
])


def optimize_pipeline(spark):
    """演示缓存和分区优化。"""

    spark.conf.set("spark.sql.shuffle.partitions", "8")

    sales_df = spark.createDataFrame(generate_data(), SCHEMA)

    agg_df = (sales_df
        .groupBy("department", "region")
        .agg(
            f.sum("revenue").alias("total_revenue"),
            f.avg("revenue").alias("avg_revenue"),
            f.count("*").alias("sale_count"),
        ))

    agg_df.cache()
    agg_df.count()  # 触发物化

    top_dept = (agg_df
        .groupBy("department")
        .agg(f.sum("total_revenue").alias("dept_revenue"))
        .orderBy(f.col("dept_revenue").desc()))

    top_region = (agg_df
        .groupBy("region")
        .agg((f.sum("total_revenue") / f.sum("sale_count")).alias("region_avg_revenue"))
        .orderBy(f.col("region_avg_revenue").desc()))

    partitions_before = agg_df.rdd.getNumPartitions()

    # 两项 action 在缓存仍然有效时执行，避免只定义惰性计划就释放缓存。
    top_dept.collect()
    top_region.collect()
    agg_df.unpersist()

    top_dept_single = top_dept.coalesce(1)

    return {
        "top_dept": top_dept_single,
        "top_region": top_region,
        "agg_partitions": partitions_before,
    }


# ---- 测试代码 ----
if __name__ == "__main__":
    spark = (SparkSession.builder
        .appName("OptimizationTest")
        .master("local[*]")
        .getOrCreate())

    result = optimize_pipeline(spark)
    assert result is not None, "函数返回了 None"
    assert result["top_dept"] is not None, "top_dept 为 None"
    assert result["top_region"] is not None, "top_region 为 None"
    assert result["top_dept"].count() > 0, "top_dept 为空"
    assert result["top_region"].count() > 0, "top_region 为空"
    assert result["top_dept"].rdd.getNumPartitions() == 1, "top_dept 应为 1 个分区"
    print(f"聚合分区数：{result['agg_partitions']}")
    print("按总营收排名的部门：")
    result["top_dept"].show()
    print("按平均营收排名的区域：")
    result["top_region"].show()
    from collections import defaultdict
    import math
    dept_sums, region_sums, region_counts = defaultdict(float), defaultdict(float), defaultdict(int)
    for _, dept, region, revenue in generate_data():
        dept_sums[dept] += revenue
        region_sums[region] += revenue
        region_counts[region] += 1
    dept_rows = result["top_dept"].collect()
    region_rows = result["top_region"].collect()
    assert len(dept_rows) == 5 and len(region_rows) == 4
    assert [r.department for r in dept_rows] == sorted(dept_sums, key=dept_sums.get, reverse=True)
    assert all(math.isclose(r.dept_revenue, dept_sums[r.department], rel_tol=1e-10) for r in dept_rows)
    averages = {region: value / region_counts[region] for region, value in region_sums.items()}
    assert [r.region for r in region_rows] == sorted(averages, key=averages.get, reverse=True)
    assert all(math.isclose(r.region_avg_revenue, averages[r.region], rel_tol=1e-10) for r in region_rows)
    assert isinstance(result["agg_partitions"], int) and result["agg_partitions"] > 0

    print("所有测试通过！")
    spark.stop()
