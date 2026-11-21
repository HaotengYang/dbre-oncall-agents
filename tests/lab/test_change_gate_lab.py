"""The change safety gate against the real lab (proposal D, criteria 3 to 6)."""

import re
import threading

import pymysql
import pytest

from change_gate import gate as gate_module
from change_gate.allowlist import IndexRequest, RejectedChange
from change_gate.gate import (
    GateError,
    StatementLog,
    apply_plan,
    load_gate_access,
    make_plan,
    required_confirmation,
    rollback,
)
from tests.lab.conftest import fetch_all, fetch_value
from tests.lab.fault_helpers import clear_and_wait_for_baseline, schema_snapshot

pytestmark = pytest.mark.lab

STATUS_QUERY = "SELECT id FROM orders WHERE status = 'paid' ORDER BY created_at DESC LIMIT 10"


def shadow_tables(connection):
    return fetch_all(connection, "SELECT TABLE_NAME FROM information_schema.TABLES "
                                 "WHERE TABLE_SCHEMA = 'shop_shadow'")


@pytest.fixture(autouse=True)
def lab_at_baseline_before_and_after(connect_as):
    clear_and_wait_for_baseline()
    schema_before = schema_snapshot(connect_as("shop_diag"))
    yield
    clear_and_wait_for_baseline()  # also removes any idx_ index a failed test left behind
    observer = connect_as("shop_diag")
    for table, create_statement in schema_snapshot(observer).items():
        leftover = re.findall(r"KEY `(idx_orders_status_created_at|idx_customers_full_name)`",
                              create_statement)
        if leftover:
            rollback(load_gate_access(),
                     make_plan_stub(table, leftover[0]))
    assert schema_snapshot(connect_as("shop_diag")) == schema_before
    assert shadow_tables(observer) == ()


def make_plan_stub(table, index_name):
    from types import SimpleNamespace
    from change_gate.allowlist import AcceptedChange
    return SimpleNamespace(change=AcceptedChange(table, (), index_name))


def test_rehearsal_uses_a_capped_sample_and_cleans_up(connect_as):
    log = StatementLog()

    plan = make_plan(load_gate_access(), IndexRequest("orders", ("status", "created_at")),
                     STATUS_QUERY, log=log)

    assert plan.rehearsal.completed and plan.rehearsal.online_build_possible
    assert plan.rehearsal.sample_rows == gate_module.SAMPLE_ROW_LIMIT  # 20,000 of 200,000
    assert plan.rehearsal.query_uses_index is True
    assert plan.risk.level == "low"
    assert "does not predict build time" in plan.limits_note
    # Planning changes nothing in shop and leaves no shadow table.
    observer = connect_as("shop_diag")
    assert "idx_orders_status_created_at" not in str(schema_snapshot(observer))
    assert shadow_tables(observer) == ()


def test_a_failure_half_way_still_drops_the_shadow_table(connect_as, monkeypatch):
    def failing_build(change, shadow_schema):
        return "CREATE INDEX this is not valid sql"

    monkeypatch.setattr(gate_module, "render_shadow_create", failing_build)

    plan = make_plan(load_gate_access(), IndexRequest("orders", ("status", "created_at")))

    assert not plan.rehearsal.completed and plan.risk.level == "high"
    assert shadow_tables(connect_as("shop_diag")) == ()


def test_only_the_rendered_statements_ever_reach_shop(connect_as):
    log = StatementLog()
    access = load_gate_access()

    plan = make_plan(access, IndexRequest("customers", ("full_name",)), log=log)
    apply_plan(access, plan, required_confirmation(plan), log=log)
    rollback(access, plan, log=log)

    # Every statement the gate account sent has one of a fixed set of shapes.
    # On shop itself only the rendered CREATE and DROP ran.
    allowed_shapes = [
        r"SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED",
        r"SET SESSION (innodb_)?lock_wait_timeout = \d+",
        r"DROP TABLE IF EXISTS shop_shadow\.customers",
        r"CREATE TABLE shop_shadow\.customers LIKE shop\.customers",
        r"INSERT INTO shop_shadow\.customers SELECT \* FROM shop\.customers "
        r"(WHERE id > %s )?ORDER BY id LIMIT \d+",
        r"SELECT (COUNT\(\*\)|MAX\(id\)) FROM shop_shadow\.customers",
        r"CREATE INDEX idx_customers_full_name ON shop_shadow\.customers \(full_name\) "
        r"ALGORITHM = INPLACE LOCK = NONE",
        re.escape(plan.statement),
        re.escape(plan.rollback),
    ]
    for account, sql in log.entries:
        if account == "shop_gate":
            assert any(re.fullmatch(shape, sql) for shape in allowed_shapes), sql
    on_shop = [sql for account, sql in log.entries if re.search(r"\bON shop\.", sql)]
    assert on_shop == [plan.statement, plan.rollback]


def test_apply_then_rollback_returns_the_schema_to_baseline(connect_as):
    access = load_gate_access()
    observer = connect_as("shop_diag")
    before = schema_snapshot(observer)

    plan = make_plan(access, IndexRequest("customers", ("full_name",)))
    with pytest.raises(GateError, match="did not match"):
        apply_plan(access, plan, "no")
    assert schema_snapshot(observer) == before  # a wrong confirmation changed nothing

    undo = apply_plan(access, plan, "yes")
    assert "idx_customers_full_name" in schema_snapshot(observer)["customers"]
    assert undo == "DROP INDEX idx_customers_full_name ON shop.customers"

    rollback(access, plan)
    assert schema_snapshot(observer) == before


def test_a_long_transaction_holding_a_metadata_lock_is_high_risk(connect_as):
    access = load_gate_access()
    holder = connect_as("shop_app")
    with holder.cursor() as cursor:
        cursor.execute("BEGIN")
        cursor.execute("SELECT id FROM shop.customers WHERE id = 1")
        cursor.fetchall()
    try:
        import time
        time.sleep(gate_module.LONG_TRANSACTION_SECONDS + 1)

        plan = make_plan(access, IndexRequest("customers", ("full_name",)))

        assert plan.risk.level == "high"
        assert any("metadata lock" in reason for reason in plan.risk.reasons)
        assert required_confirmation(plan) == "idx_customers_full_name"
        with pytest.raises(GateError, match="did not match"):
            apply_plan(access, plan, "yes")  # plain yes is not enough at high risk
        # With the typed name, the apply gives up after its short lock wait
        # instead of queueing traffic behind it.
        with pytest.raises(GateError, match="gave up after"):
            apply_plan(access, plan, "idx_customers_full_name")
        assert "idx_customers_full_name" not in schema_snapshot(connect_as("shop_diag"))["customers"]
    finally:
        holder.rollback()


def test_requests_outside_the_allowlist_never_touch_the_server():
    log = StatementLog()

    with pytest.raises(RejectedChange):
        make_plan(load_gate_access(), IndexRequest("orders", ("customer_id",)), log=log)

    assert all(sql.startswith("SELECT") for _, sql in log.entries)  # only the schema read
