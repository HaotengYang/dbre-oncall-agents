"""The gate's decisions without a database: risk levels, what must be typed,
that a blocked change can never be applied, and which target queries may be
explained against the sample."""

import pytest

from change_gate.allowlist import AcceptedChange, RejectedChange, render_create, render_rollback
from change_gate.gate import (
    LARGE_TABLE_ROWS,
    GateError,
    Plan,
    Rehearsal,
    apply_plan,
    classify_risk,
    required_confirmation,
    target_query_refusal,
)

GOOD_REHEARSAL = Rehearsal(True, 20_000, True, 0.1, True, "uses the index", None)
ABANDONED = Rehearsal(False, 4_000, None, None, None, "no target query", "the server is busy")
NOT_ONLINE = Rehearsal(False, 20_000, False, None, None, "no target query", "refused online build")
CHANGE = AcceptedChange("orders", ("status",), "idx_orders_status")


def plan_with(rehearsal, rows=1_000, data_bytes=10_000, long_transactions=()):
    risk = classify_risk(rows, data_bytes, list(long_transactions), rehearsal)
    return Plan(CHANGE, render_create(CHANGE), render_rollback(CHANGE), rehearsal, risk, "note")


def test_small_table_good_rehearsal_is_low_risk_and_needs_yes():
    plan = plan_with(GOOD_REHEARSAL)

    assert plan.risk.level == "low"
    assert required_confirmation(plan) == "yes"


@pytest.mark.parametrize("changes, reason_fragment", [
    ({"rows": LARGE_TABLE_ROWS + 1}, "large table"),
    ({"data_bytes": 2 * 1024 ** 3}, "large table"),
    ({"long_transactions": [42]}, "metadata lock"),
    ({"rehearsal": ABANDONED}, "did not complete"),
])
def test_each_risk_factor_raises_the_level_to_high_and_needs_the_typed_name(changes, reason_fragment):
    plan = plan_with(**{"rehearsal": GOOD_REHEARSAL, **changes})

    assert plan.risk.level == "high"
    assert any(reason_fragment in reason for reason in plan.risk.reasons)
    assert required_confirmation(plan) == "idx_orders_status"


def test_an_index_that_cannot_be_built_online_is_blocked_whatever_else_is_true():
    for extra in ({}, {"rows": LARGE_TABLE_ROWS + 1}, {"long_transactions": [99]}):
        plan = plan_with(NOT_ONLINE, **extra)
        assert plan.risk.level == "blocked"
        assert required_confirmation(plan) is None


def test_a_blocked_change_is_refused_before_any_connection_is_opened():
    plan = plan_with(NOT_ONLINE)

    # access=None: any attempt to connect would raise AttributeError instead.
    for typed in ("yes", "idx_orders_status", "", None):
        with pytest.raises(GateError, match="blocked"):
            apply_plan(None, plan, typed)


@pytest.mark.parametrize("plan_kwargs, wrong", [
    ({"rehearsal": GOOD_REHEARSAL}, "y"),
    ({"rehearsal": GOOD_REHEARSAL}, "YES"),
    ({"rehearsal": GOOD_REHEARSAL, "long_transactions": [42]}, "yes"),
])
def test_a_wrong_confirmation_is_refused_before_any_connection_is_opened(plan_kwargs, wrong):
    with pytest.raises(GateError, match="did not match"):
        apply_plan(None, plan_with(**plan_kwargs), wrong)


@pytest.mark.parametrize("query, allowed", [
    ("SELECT id FROM orders WHERE status = 'paid' ORDER BY created_at DESC LIMIT 10", True),
    ("SELECT o.id FROM orders o WHERE o.status = 'paid'", True),
    ("SELECT id FROM shop.orders WHERE status = 'paid'", False),
    ("SELECT id FROM shop_shadow.orders WHERE status = 'paid'", False),
    ("SELECT orders.id FROM orders JOIN customers ON customers.id = orders.customer_id", False),
    ("SELECT id FROM customers WHERE id = 1", False),
    ("SELECT id FROM orders WHERE id = 1 AND SLEEP(3) = 0", False),
    ("SELECT COUNT(*) FROM information_schema.tables", False),
])
def test_only_queries_on_the_copied_table_alone_may_be_explained(query, allowed):
    assert (target_query_refusal(query, "orders") is None) is allowed


def test_a_hand_built_change_with_odd_names_cannot_be_rendered():
    for odd in (AcceptedChange("orders; DROP TABLE x", ("status",), "idx_a"),
                AcceptedChange("orders", ("status) ; --",), "idx_a"),
                AcceptedChange("orders", ("status",), "idx_a ON shop.customers"),
                AcceptedChange("orders", (), "idx_a")):
        with pytest.raises(RejectedChange):
            render_create(odd)
