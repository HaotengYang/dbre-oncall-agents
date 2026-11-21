"""Checks, before any case runs, that the lab has what the cases rely on.

The lab's accounts are created only when its data volume is first created. A
volume older than an account change silently lacks the account, and a case can
then score for the wrong reason: the no-admin-channel case would be "locked
out" by a login failure instead of by exhausted connections.
"""

import pymysql

from diagnosis.access import load_diagnosis_access

ACCESS_DENIED_ERROR = 1045
PRIVILEGE_REQUIRED_ERROR = 1227


class PreflightError(Exception):
    """The lab is not in a state the eval cases can be run against."""


def _try_connect(connect, **connection_settings):
    try:
        connect(connect_timeout=5, **connection_settings).close()
    except pymysql.MySQLError as connect_error:
        return connect_error.args[0] if connect_error.args else -1
    return None


def check_lab_accounts(connect=pymysql.connect):
    """Raises PreflightError unless both diagnosis accounts work as the cases assume."""
    with_admin = load_diagnosis_access(admin_port_available=True)
    without_admin = load_diagnosis_access(admin_port_available=False)
    reset_hint = (
        " The lab's data volume probably predates this account. Run: make reset"
    )

    def login(access, port):
        return _try_connect(connect, host=access.mysql_host, port=port,
                            user=access.mysql_user, password=access.mysql_password)

    problems = []
    if login(with_admin, with_admin.mysql_admin_port) is not None:
        problems.append(
            f"{with_admin.mysql_user} cannot connect to the administrative port."
        )
    no_admin_error = login(without_admin, without_admin.mysql_port)
    if no_admin_error is not None:
        problems.append(
            f"{without_admin.mysql_user} cannot connect on the normal port "
            f"(error {no_admin_error})."
        )
    # The account without the administrative channel must really be without it.
    if login(without_admin, with_admin.mysql_admin_port) is None:
        problems.append(
            f"{without_admin.mysql_user} can connect to the administrative port; "
            "it must not be able to."
        )
    if problems:
        raise PreflightError(" ".join(problems) + reset_hint)
