ARG PYTHON_BASE=python:3.12-slim-bookworm
FROM ${PYTHON_BASE}

ARG PYSPARK_VERSION=4.1.1
RUN apt-get update \
    && apt-get install -y --no-install-recommends openjdk-17-jre-headless \
    && rm -rf /var/lib/apt/lists/*

# Optional public wheel URL (with #sha256=...) for offline/cached builds.
# It is a build input only; no wheel or private environment is copied into source.
ARG PYSPARK_PACKAGE=""
RUN pip install --no-cache-dir "${PYSPARK_PACKAGE:-pyspark==${PYSPARK_VERSION}}" "numpy>=1.22" \
    && python3 -c "import pathlib,pyspark; [(p.chmod(p.stat().st_mode | 0o111)) for p in (pathlib.Path(pyspark.__file__).parent / 'bin').iterdir() if p.is_file()]" \
    && groupadd --gid 10001 learner \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /tmp learner

ENV HOME=/tmp \
    PYSPARK_PYTHON=python3 \
    PYSPARK_DRIVER_PYTHON=python3 \
    SPARK_LOCAL_IP=127.0.0.1 \
    SPARK_LOCAL_DIRS=/tmp/spark \
    PYTHONIOENCODING=utf-8 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1

USER 10001:10001
WORKDIR /tmp
ENTRYPOINT ["python3", "/job/exercise.py"]
