"""Shared fixtures for tests that talk to the running lab."""

import pathlib

import pymysql
import pytest
import requests

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[2]


def read_env_file(env_path):
    values = {}
    for line in env_path.read_text().splitlines():
        stripped_line = line.strip()
        if not stripped_line or stripped_line.startswith("#"):
            continue
        name, _, value = stripped_line.partition("=")
        values[name.strip()] = value.strip()
    return values


@pytest.fixture(scope="session")
def lab_env():
    env_path = REPOSITORY_ROOT / ".env"
    if not env_path.exists():
        pytest.fail("No .env file. Copy .env.example to .env and run make up.")
    return read_env_file(env_path)


@pytest.fixture
def connect_as(lab_env):
    """Returns a function that opens a connection as one of the lab accounts.

    Function-scoped on purpose: every connection is closed when its test ends.
    Holding them for the whole session would pile up dozens of idle
    connections against the lab's low max_connections and make the lab look
    unhealthy to the very tests that measure its baseline.
    """
    password_variable_by_user = {
        "shop_app": "APP_DB_PASSWORD",
        "shop_diag": "DIAG_DB_PASSWORD",
        "shop_gate": "GATE_DB_PASSWORD",
        "shop_fault": "FAULT_DB_PASSWORD",
        "root": "MYSQL_ROOT_PASSWORD",
    }
    open_connections = []

    def open_connection(user_name, database=None):
        connection = pymysql.connect(
            host="127.0.0.1",
            port=int(lab_env.get("MYSQL_HOST_PORT", "13306")),
            user=user_name,
            password=lab_env[password_variable_by_user[user_name]],
            database=database,
            autocommit=True,
            connect_timeout=5,
            read_timeout=30,
        )
        open_connections.append(connection)
        return connection

    yield open_connection
    for connection in open_connections:
        try:
            connection.close()
        except pymysql.MySQLError:
            pass


def fetch_all(connection, sql, parameters=None):
    with connection.cursor() as cursor:
        cursor.execute(sql, parameters)
        return cursor.fetchall()


def fetch_value(connection, sql, parameters=None):
    return fetch_all(connection, sql, parameters)[0][0]


@pytest.fixture(scope="session")
def prometheus_query(lab_env):
    """Returns a function that runs an instant PromQL query and returns samples."""
    base_url = f"http://127.0.0.1:{lab_env.get('PROMETHEUS_HOST_PORT', '19090')}"

    def run_query(promql):
        response = requests.get(
            f"{base_url}/api/v1/query", params={"query": promql}, timeout=10
        )
        response.raise_for_status()
        payload = response.json()
        assert payload["status"] == "success", payload
        return [
            (sample["metric"], float(sample["value"][1]))
            for sample in payload["data"]["result"]
        ]

    return run_query
