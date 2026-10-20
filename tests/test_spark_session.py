import pytest


@pytest.mark.spark
def test_local_spark_runs_python_workers(spark):
    df = spark.createDataFrame([(1,), (2,)], ["n"])
    assert df.rdd.map(lambda r: r.n * 10).collect() == [10, 20]
    assert spark.version.startswith("4.1")
