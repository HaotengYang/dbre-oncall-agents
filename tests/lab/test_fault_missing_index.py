"""Completion criterion 8: the dropped-index fault really makes the workload's
customer query scan the whole table, and stopping it really undoes that."""

import pytest

from tests.lab.conftest import fetch_all
from tests.lab.fault_helpers import (
    clear_and_wait_for_baseline,
    rows_examined_per_execution,
    run_injector,
    start_fault,
    stop_fault,
)

pytestmark = pytest.mark.lab

CUSTOMER_ORDERS_DIGEST = (
    "SELECT `id` , STATUS , `total_cents` , `created_at` FROM `orders` "
    "WHERE `customer_id` = ? ORDER BY `created_at` DESC LIMIT ?"
)
CUSTOMER_ORDERS_QUERY = (
    "SELECT id, status, total_cents, created_at FROM shop.orders "
    "WHERE customer_id = 77 ORDER BY created_at DESC LIMIT 10"
)
OBSERVATION_SECONDS = 15


def explain_access(connection):
    """Returns (access type, chosen key) from EXPLAIN of the customer query."""
    with connection.cursor() as cursor:
        cursor.execute("EXPLAIN " + CUSTOMER_ORDERS_QUERY)
        column_names = [column[0] for column in cursor.description]
        plan_row = dict(zip(column_names, cursor.fetchone()))
    return plan_row["type"], plan_row["key"]


@pytest.fixture(autouse=True)
def lab_at_baseline_before_and_after():
    clear_and_wait_for_baseline()
    yield
    clear_and_wait_for_baseline()


def test_dropped_index_turns_the_customer_query_into_a_full_scan(connect_as):
    diag_connection = connect_as("shop_diag")
    assert explain_access(diag_connection) == ("ref", "idx_orders_customer_created")
    examined_before = rows_examined_per_execution(
        diag_connection, CUSTOMER_ORDERS_DIGEST, OBSERVATION_SECONDS
    )

    injector = start_fault("missing_index.dropped_index")
    try:
        examined_during = rows_examined_per_execution(
            diag_connection, CUSTOMER_ORDERS_DIGEST, OBSERVATION_SECONDS
        )
        access_during = explain_access(diag_connection)
        index_names_during = {
            row[2] for row in fetch_all(diag_connection, "SHOW INDEX FROM shop.orders")
        }
    finally:
        stop_fault(injector)

    assert "idx_orders_customer_created" not in index_names_during
    assert access_during == ("ALL", None)
    # The whole table. Measured values sit a little above 200,000 (202,995 on
    # the first run): statements in flight at the edges of the window are
    # counted in one snapshot's rows but the other's executions.
    assert examined_during == pytest.approx(200_000, rel=0.05)
    assert examined_during >= 1_000 * examined_before

    # Stopping the fault brings the index and the cheap plan back.
    assert explain_access(diag_connection) == ("ref", "idx_orders_customer_created")
    examined_after = rows_examined_per_execution(
        diag_connection, CUSTOMER_ORDERS_DIGEST, OBSERVATION_SECONDS
    )
    assert examined_after == pytest.approx(examined_before, rel=0.5)
