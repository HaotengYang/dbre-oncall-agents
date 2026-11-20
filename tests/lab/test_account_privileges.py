"""Completion criteria 7 and 10: least privilege is enforced by the database.

Each denial test really attempts the forbidden statement, checks that MySQL
refuses it with a privilege error, and checks that nothing changed.
"""

import pymysql
import pytest

from tests.lab.conftest import fetch_all, fetch_value

pytestmark = pytest.mark.lab

# 1142: command denied on a table. 1044: access denied to a database.
# 1227: a global privilege is required.
PRIVILEGE_ERROR_CODES = {1142, 1044, 1227}

# How MySQL normalizes the workload's order_by_id statement.
WORKLOAD_ORDER_BY_ID_DIGEST = (
    "SELECT `id` , `customer_id` , STATUS , `total_cents` , `created_at` "
    "FROM `orders` WHERE `id` = ?"
)


def assert_denied(connection, sql):
    with pytest.raises(pymysql.MySQLError) as raised:
        with connection.cursor() as cursor:
            cursor.execute(sql)
    error_code = raised.value.args[0]
    assert error_code in PRIVILEGE_ERROR_CODES, raised.value


def shop_table_names(connection):
    rows = fetch_all(
        connection,
        "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = 'shop'",
    )
    return sorted(row[0] for row in rows)


def index_names(connection, schema_name, table_name):
    rows = fetch_all(
        connection,
        "SELECT DISTINCT INDEX_NAME FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s",
        (schema_name, table_name),
    )
    return {row[0] for row in rows}


# ---- diagnosis account -------------------------------------------------


def test_diag_can_read_statement_digests_with_sample_text(connect_as):
    connection = connect_as("shop_diag")

    rows = fetch_all(
        connection,
        "SELECT DIGEST_TEXT, QUERY_SAMPLE_TEXT, COUNT_STAR "
        "FROM performance_schema.events_statements_summary_by_digest "
        "WHERE SCHEMA_NAME = 'shop' AND DIGEST_TEXT = %s",
        (WORKLOAD_ORDER_BY_ID_DIGEST,),
    )

    # Exactly the workload's order_by_id statement. Other digests on orders
    # exist too (these tests run their own queries), so the filter is specific.
    assert len(rows) == 1, rows
    digest_text, sample_text, execution_count = rows[0]
    assert digest_text.endswith("WHERE `id` = ?")  # normalized: literal replaced
    assert "?" not in sample_text  # the stored sample is a real statement
    assert sample_text.split("WHERE id = ")[1].isdigit()  # with a real value
    assert execution_count >= 10


def test_diag_can_see_other_sessions(connect_as):
    connection = connect_as("shop_diag")

    other_users = fetch_all(
        connection,
        "SELECT DISTINCT USER FROM performance_schema.processlist WHERE USER = 'shop_app'",
    )

    assert other_users == (("shop_app",),)


def test_diag_can_read_lock_and_transaction_tables(connect_as):
    connection = connect_as("shop_diag")

    assert fetch_value(connection, "SELECT COUNT(*) FROM information_schema.INNODB_TRX") >= 0
    assert fetch_value(connection, "SELECT COUNT(*) FROM performance_schema.data_lock_waits") >= 0
    assert fetch_value(connection, "SELECT COUNT(*) FROM performance_schema.metadata_locks") >= 0


def test_diag_can_explain_a_select(connect_as):
    connection = connect_as("shop_diag", database="shop")

    plan_rows = fetch_all(connection, "EXPLAIN SELECT id FROM orders WHERE customer_id = 1")

    assert plan_rows
    assert "idx_orders_customer_created" in str(plan_rows)


@pytest.mark.parametrize(
    "forbidden_sql",
    [
        "INSERT INTO shop.orders (id, customer_id, status, total_cents, created_at) "
        "VALUES (999999999, 1, 'pending', 1, '2026-01-01')",
        "UPDATE shop.orders SET status = 'cancelled' WHERE id = 1",
        "DELETE FROM shop.orders WHERE id = 1",
        "DROP TABLE shop.orders",
        "CREATE INDEX idx_diag_should_fail ON shop.orders (status)",
        "CREATE TABLE shop.diag_should_fail (id INT)",
        "SET GLOBAL max_connections = 1000",
    ],
)
def test_diag_cannot_change_anything(connect_as, forbidden_sql):
    diag_connection = connect_as("shop_diag")
    status_before = fetch_value(diag_connection, "SELECT status FROM shop.orders WHERE id = 1")
    tables_before = shop_table_names(diag_connection)

    assert_denied(diag_connection, forbidden_sql)

    assert shop_table_names(diag_connection) == tables_before
    assert fetch_value(diag_connection, "SELECT COUNT(*) FROM shop.orders") == 200_000
    assert fetch_value(diag_connection, "SELECT @@global.max_connections") == 60
    assert "idx_diag_should_fail" not in index_names(diag_connection, "shop", "orders")
    # The workload may legitimately change order 1's status between the two
    # reads, so only the row's existence is checked, not its value.
    assert status_before is not None


def test_diag_cannot_kill_another_session(connect_as):
    diag_connection = connect_as("shop_diag")
    victim_connection = connect_as("shop_app")
    victim_session_id = fetch_value(victim_connection, "SELECT CONNECTION_ID()")

    with pytest.raises(pymysql.MySQLError) as raised:
        with diag_connection.cursor() as cursor:
            cursor.execute(f"KILL {int(victim_session_id)}")

    # 1095: "You are not owner of thread".
    assert raised.value.args[0] == 1095, raised.value
    assert fetch_value(victim_connection, "SELECT 1") == 1  # still alive


# ---- workload account --------------------------------------------------


def test_app_cannot_change_schema(connect_as):
    app_connection = connect_as("shop_app")
    diag_connection = connect_as("shop_diag")
    tables_before = shop_table_names(diag_connection)

    assert_denied(app_connection, "DROP TABLE shop.orders")
    assert_denied(app_connection, "CREATE INDEX idx_app_should_fail ON shop.orders (status)")

    assert shop_table_names(diag_connection) == tables_before
    assert "idx_app_should_fail" not in index_names(diag_connection, "shop", "orders")


# ---- change safety gate account ----------------------------------------


@pytest.mark.parametrize(
    "forbidden_sql",
    [
        "DROP TABLE shop.orders",
        "DROP TABLE shop.products",
        "TRUNCATE TABLE shop.orders",
        "DROP DATABASE shop",
        "CREATE TABLE shop.gate_should_fail (id INT)",
        "ALTER TABLE shop.products ADD COLUMN gate_should_fail INT",
        "ALTER TABLE shop.products DROP COLUMN category",
        "RENAME TABLE shop.products TO shop.products_renamed",
        "INSERT INTO shop.products (id, sku, name, category, price_cents) "
        "VALUES (999999, 'x', 'x', 'x', 1)",
        "UPDATE shop.products SET price_cents = 0 WHERE id = 1",
        "DELETE FROM shop.products WHERE id = 1",
    ],
)
def test_gate_cannot_do_anything_to_shop_except_indexes(connect_as, forbidden_sql):
    gate_connection = connect_as("shop_gate")
    diag_connection = connect_as("shop_diag")
    tables_before = shop_table_names(diag_connection)
    product_columns_before = fetch_all(
        diag_connection,
        "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = 'shop' AND TABLE_NAME = 'products' ORDER BY ORDINAL_POSITION",
    )
    first_product_before = fetch_all(diag_connection, "SELECT * FROM shop.products WHERE id = 1")

    assert_denied(gate_connection, forbidden_sql)

    assert shop_table_names(diag_connection) == tables_before
    assert fetch_all(
        diag_connection,
        "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = 'shop' AND TABLE_NAME = 'products' ORDER BY ORDINAL_POSITION",
    ) == product_columns_before
    assert fetch_all(diag_connection, "SELECT * FROM shop.products WHERE id = 1") == first_product_before
    assert fetch_value(diag_connection, "SELECT COUNT(*) FROM shop.products") == 1_000
    assert fetch_value(diag_connection, "SELECT COUNT(*) FROM shop.orders") == 200_000


def test_gate_can_add_and_drop_an_index_online_in_shop(connect_as):
    gate_connection = connect_as("shop_gate")
    diag_connection = connect_as("shop_diag")
    index_name = "idx_gate_privilege_probe"
    assert index_name not in index_names(diag_connection, "shop", "products")

    try:
        with gate_connection.cursor() as cursor:
            cursor.execute(
                f"CREATE INDEX {index_name} ON shop.products (category) "
                "ALGORITHM = INPLACE LOCK = NONE"
            )
        assert index_name in index_names(diag_connection, "shop", "products")
    finally:
        with gate_connection.cursor() as cursor:
            cursor.execute(f"DROP INDEX {index_name} ON shop.products")

    assert index_name not in index_names(diag_connection, "shop", "products")


def test_gate_can_build_and_remove_a_shadow_table(connect_as):
    gate_connection = connect_as("shop_gate")
    shadow_table = "shop_shadow.products_privilege_probe"

    try:
        with gate_connection.cursor() as cursor:
            cursor.execute(f"CREATE TABLE {shadow_table} LIKE shop.products")
            cursor.execute(
                f"INSERT INTO {shadow_table} SELECT * FROM shop.products ORDER BY id LIMIT 50"
            )
            cursor.execute(f"CREATE INDEX idx_probe_category ON {shadow_table} (category)")
        assert fetch_value(gate_connection, f"SELECT COUNT(*) FROM {shadow_table}") == 50
    finally:
        with gate_connection.cursor() as cursor:
            cursor.execute(f"DROP TABLE IF EXISTS {shadow_table}")

    remaining_shadow_tables = fetch_value(
        gate_connection,
        "SELECT COUNT(*) FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = 'shop_shadow' AND TABLE_NAME = 'products_privilege_probe'",
    )
    assert remaining_shadow_tables == 0
