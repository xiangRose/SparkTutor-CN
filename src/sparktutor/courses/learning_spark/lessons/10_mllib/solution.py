"""
使用 MLlib 进行机器学习 - 参考答案

完整的房价预测 ML Pipeline。
"""

from pyspark.sql import SparkSession
from pyspark.ml import Pipeline
from pyspark.ml.feature import StringIndexer, VectorAssembler
from pyspark.ml.regression import RandomForestRegressor
from pyspark.ml.evaluation import RegressionEvaluator
from pyspark.sql.types import (
    StructType, StructField, StringType, IntegerType, DoubleType
)
import random


def generate_housing_data(n=500):
    """生成用于 ML 训练的合成房屋数据。"""
    neighborhoods = ["Downtown", "Suburbs", "Rural", "Midtown", "Waterfront"]
    conditions = ["Excellent", "Good", "Fair", "Poor"]
    random.seed(42)
    data = []
    for i in range(n):
        neighborhood = random.choice(neighborhoods)
        condition = random.choice(conditions)
        bedrooms = random.randint(1, 5)
        sqft = random.randint(500, 4000)
        age = random.randint(0, 80)

        base = {"Downtown": 300000, "Suburbs": 200000, "Rural": 100000,
                "Midtown": 250000, "Waterfront": 400000}
        cond = {"Excellent": 1.3, "Good": 1.1, "Fair": 0.9, "Poor": 0.7}
        price = (base[neighborhood] * cond[condition]
                 + bedrooms * 25000 + sqft * 100 - age * 1000
                 + random.gauss(0, 20000))
        data.append((neighborhood, condition, bedrooms, sqft, age, round(price, 2)))
    return data


SCHEMA = StructType([
    StructField("neighborhood", StringType(), False),
    StructField("condition", StringType(), False),
    StructField("bedrooms", IntegerType(), False),
    StructField("sqft", IntegerType(), False),
    StructField("age", IntegerType(), False),
    StructField("price", DoubleType(), False),
])


def build_pipeline(spark):
    """构建并评估用于房价预测的 ML Pipeline。"""

    housing_df = spark.createDataFrame(generate_housing_data(), SCHEMA)

    (trainDF, testDF) = housing_df.randomSplit([0.8, 0.2], seed=42)

    neighborhood_indexer = StringIndexer(
        inputCol="neighborhood", outputCol="neighborhoodIndex", handleInvalid="skip"
    )
    condition_indexer = StringIndexer(
        inputCol="condition", outputCol="conditionIndex", handleInvalid="skip"
    )

    assembler = VectorAssembler(
        inputCols=["neighborhoodIndex", "conditionIndex", "bedrooms", "sqft", "age"],
        outputCol="features"
    )

    rf = RandomForestRegressor(
        labelCol="price", featuresCol="features",
        numTrees=20, maxDepth=5, seed=42
    )

    pipeline = Pipeline(stages=[neighborhood_indexer, condition_indexer, assembler, rf])

    model = pipeline.fit(trainDF)

    predictions = model.transform(testDF)

    evaluator = RegressionEvaluator(labelCol="price", predictionCol="prediction")
    rmse = evaluator.setMetricName("rmse").evaluate(predictions)
    r2 = evaluator.setMetricName("r2").evaluate(predictions)

    return {
        "model": model,
        "predictions": predictions,
        "rmse": rmse,
        "r2": r2,
        "train_count": trainDF.count(),
        "test_count": testDF.count(),
    }


# ---- 测试代码 ----
if __name__ == "__main__":
    spark = (SparkSession.builder
        .appName("MLlibTest")
        .master("local[*]")
        .getOrCreate())

    # 观察真实拟合输入，不依赖学生使用的变量名或代码写法。
    # 只在目标函数执行期间包装 fit，任何异常都会恢复原方法。
    from collections import Counter
    _course_expected_rows = Counter(generate_housing_data())
    _course_fit_records = []
    _course_original_fit = Pipeline.fit

    def _course_checked_fit(self, dataset, *args, **kwargs):
        rows = Counter(tuple(row) for row in dataset.select(*SCHEMA.fieldNames()).collect())
        assert 0 < sum(rows.values()) < sum(_course_expected_rows.values()), "Pipeline 只能在训练子集上拟合，不能使用全部数据或空数据"
        assert not rows - _course_expected_rows, "拟合输入必须来自题目提供的房屋数据"
        model = _course_original_fit(self, dataset, *args, **kwargs)
        _course_fit_records.append((model.uid, rows))
        return model

    Pipeline.fit = _course_checked_fit
    try:
        result = build_pipeline(spark)
    finally:
        Pipeline.fit = _course_original_fit
    assert result is not None, "函数返回了 None"
    assert result["model"] is not None, "模型为 None"
    assert result["predictions"] is not None, "预测为 None"
    assert result["rmse"] is not None, "RMSE 为 None"
    assert result["r2"] is not None, "R2 为 None"
    assert result["rmse"] >= 0, f"RMSE 应为非负数，实际得到 {result['rmse']}"
    assert result["r2"] <= 1, f"R2 不应大于 1，允许负数，实际得到 {result['r2']}"
    print(f"训练样本数：{result['train_count']}")
    print(f"测试样本数：{result['test_count']}")
    print(f"RMSE：{result['rmse']:.2f}")
    print(f"R2：{result['r2']:.4f}")
    print("\n预测示例：")
    result["predictions"].select("neighborhood", "condition", "bedrooms",
                                  "sqft", "age", "price", "prediction").show(10)
    import math
    from pyspark.ml import PipelineModel
    assert isinstance(result["model"], PipelineModel) and len(result["model"].stages) == 4
    assert result["train_count"] > 0 and result["test_count"] > 0
    assert result["train_count"] + result["test_count"] == 500
    assert result["predictions"].count() == result["test_count"], "预测必须来自测试集，行数应与 test_count 一致"
    assert {"features", "price", "prediction"} <= set(result["predictions"].columns)
    fitted = [rows for uid, rows in _course_fit_records if uid == result["model"].uid]
    assert len(fitted) == 1, "返回的模型必须来自本次 Pipeline 拟合"
    assert sum(fitted[0].values()) == result["train_count"], "train_count 必须等于模型实际使用的训练行数"
    predicted = Counter(tuple(row) for row in result["predictions"].select(*SCHEMA.fieldNames()).collect())
    assert fitted[0] + predicted == _course_expected_rows, "训练数据与测试预测必须恰好覆盖原数据，不能重复使用或遗漏样本"
    check_evaluator = RegressionEvaluator(labelCol="price", predictionCol="prediction")
    for metric in ["rmse", "r2"]:
        measured = check_evaluator.setMetricName(metric).evaluate(result["predictions"])
        assert math.isfinite(result[metric]) and math.isclose(result[metric], measured, rel_tol=1e-9, abs_tol=1e-9)

    print("所有测试通过！")
    spark.stop()
