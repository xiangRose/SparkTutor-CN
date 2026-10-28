from collections import Counter
import json
from pyspark.sql import SparkSession, functions as f, Window
from pyspark.sql.types import StructType, StructField, LongType, StringType

def enrich_visits(visits, page_versions):
    """Apply the same invariant to a previously unseen page dimension."""
    window = Window.partitionBy("page_id").orderBy(f.col("updated_at").desc(), f.col("version").desc())
    latest = (page_versions.withColumn("_latest_rank", f.row_number().over(window))
              .filter(f.col("_latest_rank") == 1).select("page_id", "category"))
    return visits.join(latest, "page_id", "left").select("visit_id", "page_id", "dwell_seconds", "category")

# ---- 课程测试（请勿修改此行以下内容）----
if __name__ == "__main__":
    spark = (SparkSession.builder.appName("JoinTransferLab").master("local[2]")
             .config("spark.sql.shuffle.partitions", "2").getOrCreate())
    try:
        cases = [
            ([(80,12,3),(81,12,8),(81,12,8),(82,77,4),(83,None,2)],
             [(12,"archive",6,1),(12,"news",9,1),(12,"science",9,3),(99,"other",20,1),(None,"null_page",30,1)],
             [(80,12,3,"science"),(81,12,8,"science"),(81,12,8,"science"),(82,77,4,None),(83,None,2,None)]),
            ([(91,4,20),(92,5,10)], [(5,"new_b",4,2),(4,"new_a",8,2),(4,"old_a",8,1),(5,"old_b",2,9)],
             [(91,4,20,"new_a"),(92,5,10,"new_b")]),
            ([(93,9,1)], [], [(93,9,1,None)]),
            ([], [(3,"unused",2,1)], []),
        ]
        for visits_data, page_data, expected in cases:
            visits = spark.createDataFrame(visits_data, "visit_id LONG, page_id LONG, dwell_seconds LONG")
            pages = spark.createDataFrame(page_data, "page_id LONG, category STRING, updated_at LONG, version LONG")
            result = enrich_visits(visits, pages)
            assert result is not None and result.columns == ["visit_id","page_id","dwell_seconds","category"]
            actual = [tuple(row) for row in result.collect()]
            assert len(actual) == len(visits_data), "迁移任务仍须保留每条访问事实，不膨胀、不去重、不丢未匹配行"
            assert Counter(actual) == Counter(expected), "迁移到访问页面时也须按更新时间和版本选最新分类"
            assert result.schema["category"].dataType == StringType()
        print("JOIN_TRANSFER_EVIDENCE=" + json.dumps({"context":"visits_latest_page","cases":len(cases),
              "checks":["latest-version","duplicate-facts","unmatched","null","empty"]}))
        print("所有测试通过！")
    finally:
        spark.stop()
