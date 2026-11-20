"""The lab's deliberate MySQL settings and container limits are really applied."""

import json
import subprocess

import pytest

from tests.lab.conftest import REPOSITORY_ROOT, fetch_value

pytestmark = pytest.mark.lab


def test_max_connections_is_lowered(connect_as):
    connection = connect_as("shop_diag")

    assert fetch_value(connection, "SELECT @@global.max_connections") == 60


def test_performance_schema_keeps_long_sample_statements(connect_as):
    connection = connect_as("shop_diag")

    assert fetch_value(connection, "SELECT @@global.performance_schema") == 1
    assert fetch_value(connection, "SELECT @@global.performance_schema_max_sql_text_length") == 4096
    assert fetch_value(connection, "SELECT @@global.max_digest_length") == 4096


def test_mysql_container_has_fixed_cpu_and_memory_limits():
    container_id = subprocess.run(
        ["docker", "compose", "ps", "--quiet", "mysql"],
        cwd=REPOSITORY_ROOT, check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert container_id, "mysql container is not running"

    host_config = json.loads(
        subprocess.run(
            ["docker", "inspect", "--format", "{{json .HostConfig}}", container_id],
            check=True, capture_output=True, text=True,
        ).stdout
    )

    assert host_config["NanoCpus"] == 1_000_000_000  # 1.0 CPU
    assert host_config["Memory"] == 1024 ** 3  # 1 GiB
