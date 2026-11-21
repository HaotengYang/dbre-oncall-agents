"""The preflight refuses to run cases against a lab whose accounts are not
what the cases assume."""

import pymysql
import pytest

from eval_runner.preflight import PreflightError, check_lab_accounts

ADMIN_PORT = 13307


class Closable:
    def close(self):
        pass


def fake_connect(refusals):
    """refusals maps (user, is_admin_port) to an error code."""
    def connect(host, port, user, password, connect_timeout):
        error_code = refusals.get((user, port == ADMIN_PORT))
        if error_code is not None:
            raise pymysql.err.OperationalError(error_code, "refused")
        return Closable()
    return connect


@pytest.fixture(autouse=True)
def fixed_ports(monkeypatch):
    monkeypatch.setenv("MYSQL_HOST_PORT", "13306")
    monkeypatch.setenv("MYSQL_ADMIN_HOST_PORT", str(ADMIN_PORT))
    monkeypatch.setenv("DIAG_DB_PASSWORD", "unused-in-this-test")


def test_passes_when_accounts_behave_as_the_cases_assume():
    # The no-admin account is refused on the administrative port, as it should be.
    check_lab_accounts(fake_connect({("shop_diag_noadmin", True): 1227}))


def test_missing_no_admin_account_is_reported_with_the_way_to_fix_it():
    refusals = {("shop_diag_noadmin", False): 1045, ("shop_diag_noadmin", True): 1045}

    with pytest.raises(PreflightError) as problem:
        check_lab_accounts(fake_connect(refusals))

    assert "shop_diag_noadmin" in str(problem.value) and "1045" in str(problem.value)
    assert "make reset" in str(problem.value)


def test_no_admin_account_that_can_use_the_admin_port_is_rejected():
    with pytest.raises(PreflightError, match="must not be able to"):
        check_lab_accounts(fake_connect({}))


def test_diagnosis_account_without_the_admin_port_is_reported():
    refusals = {("shop_diag", True): 1227, ("shop_diag_noadmin", True): 1227}

    with pytest.raises(PreflightError, match="shop_diag cannot connect to the administrative"):
        check_lab_accounts(fake_connect(refusals))
