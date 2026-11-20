"""Settings come only from the environment and fail loudly when incomplete."""

import pytest

from shop_workload.settings import SettingsError, load_settings

COMPLETE_ENVIRONMENT = {
    "MYSQL_HOST": "mysql",
    "MYSQL_PORT": "3306",
    "MYSQL_DATABASE": "shop",
    "APP_DB_USER": "shop_app",
    "APP_DB_PASSWORD": "example-password",
    "WORKLOAD_OPERATIONS_PER_SECOND": "20",
    "WORKLOAD_WORKER_COUNT": "4",
    "WORKLOAD_RANDOM_SEED": "42",
    "WORKLOAD_METRICS_PORT": "8000",
}


def test_complete_environment_is_parsed_into_typed_values():
    settings = load_settings(COMPLETE_ENVIRONMENT)

    assert settings.mysql_host == "mysql"
    assert settings.mysql_port == 3306
    assert settings.operations_per_second == 20.0
    assert settings.worker_count == 4
    assert settings.random_seed == 42
    assert settings.metrics_port == 8000


@pytest.mark.parametrize("missing_name", sorted(COMPLETE_ENVIRONMENT))
def test_each_missing_variable_is_named_in_the_error(missing_name):
    environment = dict(COMPLETE_ENVIRONMENT)
    del environment[missing_name]

    with pytest.raises(SettingsError, match=missing_name):
        load_settings(environment)


def test_empty_password_is_rejected():
    environment = dict(COMPLETE_ENVIRONMENT, APP_DB_PASSWORD="")

    with pytest.raises(SettingsError, match="APP_DB_PASSWORD"):
        load_settings(environment)


@pytest.mark.parametrize(
    "variable_name, invalid_value",
    [
        ("WORKLOAD_OPERATIONS_PER_SECOND", "0"),
        ("WORKLOAD_OPERATIONS_PER_SECOND", "fast"),
        ("WORKLOAD_WORKER_COUNT", "0"),
        ("WORKLOAD_WORKER_COUNT", "2.5"),
        ("MYSQL_PORT", "not-a-port"),
    ],
)
def test_invalid_numbers_are_rejected_with_the_variable_name(variable_name, invalid_value):
    environment = dict(COMPLETE_ENVIRONMENT, **{variable_name: invalid_value})

    with pytest.raises(SettingsError, match=variable_name):
        load_settings(environment)


def test_password_never_appears_in_the_printable_form():
    settings = load_settings(COMPLETE_ENVIRONMENT)

    assert "example-password" not in repr(settings)
    assert "example-password" not in str(settings)
