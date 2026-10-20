import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


def _find_java_home() -> str | None:
    if os.environ.get("JAVA_HOME"):
        return os.environ["JAVA_HOME"]
    for base in (Path("C:/Program Files/Microsoft"), Path("C:/Program Files/Eclipse Adoptium")):
        candidates = sorted(base.glob("jdk-21*")) if base.exists() else []
        if candidates:
            return str(candidates[-1])
    return None


@pytest.fixture(scope="session")
def spark():
    """A small local Spark session for unit tests of the platform transforms.

    Windows needs three workarounds: an explicit JAVA_HOME, a short directory for the JVM's
    Unix-domain sockets (long temp paths exceed the socket path limit), and Python workers
    that use the test interpreter over TCP. On Linux CI these settings are harmless.
    """
    java_home = _find_java_home()
    if java_home is None:
        pytest.skip("no Java runtime found; install JDK 21 to run Spark tests")
    os.environ["JAVA_HOME"] = java_home
    os.environ["PATH"] = str(Path(java_home) / "bin") + os.pathsep + os.environ["PATH"]
    socket_dir = Path.home() / ".sparktmp"
    socket_dir.mkdir(exist_ok=True)
    os.environ.setdefault(
        "JAVA_TOOL_OPTIONS", f"-Djdk.net.unixdomain.tmpdir={socket_dir.as_posix()}"
    )
    os.environ["PYSPARK_PYTHON"] = sys.executable
    os.environ["PYSPARK_DRIVER_PYTHON"] = sys.executable

    from pyspark.sql import SparkSession

    session = (
        SparkSession.builder.master("local[2]")
        .appName("isbank-tests")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "2")
        .config("spark.python.unix.domain.socket.enabled", "false")
        .config("spark.sql.session.timeZone", "UTC")
        # Arrow avoids per-row Python datetime conversion, which fails on Windows before 1970.
        .config("spark.sql.execution.arrow.pyspark.enabled", "true")
        .getOrCreate()
    )
    session.sparkContext.setLogLevel("ERROR")
    yield session
    session.stop()
