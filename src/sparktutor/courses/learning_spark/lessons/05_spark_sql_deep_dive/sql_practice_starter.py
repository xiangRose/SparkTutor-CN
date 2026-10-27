"""SQL 分步练习：先筛选员工，再按部门计算平均工资。"""
from pyspark.sql import SparkSession


def summarize_payroll(spark, employees):
    employees.createOrReplaceTempView("payroll")
    # TODO: 用 spark.sql 查询 payroll，先筛选 salary > 50000，再分部门求平均并排序。
    return None


# ---- 测试代码（请勿修改）----
if __name__ == "__main__":
    spark = (SparkSession.builder.appName("SQLPractice").master("local[2]")
             .config("spark.sql.shuffle.partitions", "2").getOrCreate())
    try:
        cases = [
            (
                "SELECT department, CAST(salary AS DOUBLE) AS salary FROM VALUES "
                "('Engineering', 40000), ('Engineering', 60000), ('Engineering', 90000), "
                "('Sales', 50000), ('Sales', 55000), ('HR', 48000) AS employees(department, salary)",
                [("Engineering", 75000.0), ("Sales", 55000.0)],
            ),
            (
                "SELECT department, CAST(salary AS DOUBLE) AS salary FROM VALUES "
                "('Sales', 60000), ('Admin', 60000), ('Engineering', 60000) AS employees(department, salary)",
                [("Admin", 60000.0), ("Engineering", 60000.0), ("Sales", 60000.0)],
            ),
            ("SELECT 'HR' AS department, CAST(50000 AS DOUBLE) AS salary", []),
            (
                "SELECT CAST(NULL AS STRING) AS department, CAST(NULL AS DOUBLE) AS salary WHERE FALSE",
                [],
            ),
        ]
        for query, expected in cases:
            result = summarize_payroll(spark, spark.sql(query))
            assert result is not None, "请返回查询结果 DataFrame"
            assert result.columns == ["department", "avg_salary"], "只返回 department、avg_salary 两列"
            assert [tuple(row) for row in result.collect()] == expected, (
                "先保留工资严格大于 50000 的员工再求均值；按均值降序，同均值按部门升序"
            )
        print("所有测试通过！")
    finally:
        spark.stop()
