"""Connections to the lab MySQL, and the check that it really is the lab."""

import os
import pathlib
from dataclasses import dataclass, field

import pymysql

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
LAB_MARKER_NAME = "dbre-lab"
CONNECT_TIMEOUT_SECONDS = 5

APP_ACCOUNT = "shop_app"
FAULT_ACCOUNT = "shop_fault"


class NotLabEnvironmentError(Exception):
    """The server does not carry the lab marker. Nothing may be injected."""


class LabSettingsError(Exception):
    """A setting needed to reach the lab is missing."""


@dataclass(frozen=True)
class LabSettings:
    host: str
    port: int
    app_password: str = field(repr=False)
    fault_password: str = field(repr=False)


def read_env_file(env_path):
    values = {}
    if not env_path.exists():
        return values
    for line in env_path.read_text().splitlines():
        stripped_line = line.strip()
        if stripped_line and not stripped_line.startswith("#") and "=" in stripped_line:
            name, _, value = stripped_line.partition("=")
            values[name.strip()] = value.strip()
    return values


def load_lab_settings(environment=None, env_path=None):
    """Reads settings from .env, with process environment variables on top."""
    merged = read_env_file(env_path or REPOSITORY_ROOT / ".env")
    merged.update(os.environ if environment is None else environment)

    def required(variable_name):
        value = merged.get(variable_name, "")
        if value == "":
            raise LabSettingsError(
                f"{variable_name} is not set. Copy .env.example to .env and run make up."
            )
        return value

    return LabSettings(
        host=merged.get("LAB_MYSQL_HOST", "127.0.0.1"),
        port=int(merged.get("MYSQL_HOST_PORT", "13306")),
        app_password=required("APP_DB_PASSWORD"),
        fault_password=required("FAULT_DB_PASSWORD"),
    )


def open_connection(settings, account, autocommit=True):
    passwords = {APP_ACCOUNT: settings.app_password, FAULT_ACCOUNT: settings.fault_password}
    return pymysql.connect(
        host=settings.host,
        port=settings.port,
        user=account,
        password=passwords[account],
        autocommit=autocommit,
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
    )


def assert_lab_environment(connection):
    """Raises unless the server carries exactly the lab marker."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT name FROM lab_meta.environment")
            marker_rows = cursor.fetchall()
    except pymysql.MySQLError as marker_error:
        raise NotLabEnvironmentError(
            f"cannot read the lab marker (lab_meta.environment): {marker_error}"
        ) from marker_error
    if tuple(marker_rows) != ((LAB_MARKER_NAME,),):
        raise NotLabEnvironmentError(
            f"lab marker is {marker_rows!r}, expected a single row {LAB_MARKER_NAME!r}"
        )
