"""Workload settings, read only from environment variables."""

from dataclasses import dataclass, field


class SettingsError(Exception):
    """A required setting is missing or invalid."""


@dataclass(frozen=True)
class WorkloadSettings:
    mysql_host: str
    mysql_port: int
    mysql_database: str
    mysql_user: str
    mysql_password: str = field(repr=False)
    operations_per_second: float = 0.0
    worker_count: int = 0
    random_seed: int = 0
    metrics_port: int = 0


def _required_text(environment, variable_name):
    value = environment.get(variable_name, "")
    if value == "":
        raise SettingsError(f"{variable_name} is not set or is empty")
    return value


def _positive_integer(environment, variable_name):
    raw_value = _required_text(environment, variable_name)
    try:
        parsed_value = int(raw_value)
    except ValueError:
        raise SettingsError(f"{variable_name} must be a whole number, got {raw_value!r}")
    if parsed_value <= 0:
        raise SettingsError(f"{variable_name} must be positive, got {raw_value!r}")
    return parsed_value


def _integer(environment, variable_name):
    raw_value = _required_text(environment, variable_name)
    try:
        return int(raw_value)
    except ValueError:
        raise SettingsError(f"{variable_name} must be a whole number, got {raw_value!r}")


def _positive_number(environment, variable_name):
    raw_value = _required_text(environment, variable_name)
    try:
        parsed_value = float(raw_value)
    except ValueError:
        raise SettingsError(f"{variable_name} must be a number, got {raw_value!r}")
    if parsed_value <= 0:
        raise SettingsError(f"{variable_name} must be positive, got {raw_value!r}")
    return parsed_value


def load_settings(environment):
    return WorkloadSettings(
        mysql_host=_required_text(environment, "MYSQL_HOST"),
        mysql_port=_positive_integer(environment, "MYSQL_PORT"),
        mysql_database=_required_text(environment, "MYSQL_DATABASE"),
        mysql_user=_required_text(environment, "APP_DB_USER"),
        mysql_password=_required_text(environment, "APP_DB_PASSWORD"),
        operations_per_second=_positive_number(environment, "WORKLOAD_OPERATIONS_PER_SECOND"),
        worker_count=_positive_integer(environment, "WORKLOAD_WORKER_COUNT"),
        random_seed=_integer(environment, "WORKLOAD_RANDOM_SEED"),
        metrics_port=_positive_integer(environment, "WORKLOAD_METRICS_PORT"),
    )
