"""The baseline the injector restores must match the schema the lab creates."""

import pathlib
import re

from fault_injector.baseline import (
    BASELINE_INDEXES,
    IndexDefinition,
    create_index_sql,
    find_missing_indexes,
    find_unexpected_indexes,
)

SCHEMA_FILE = pathlib.Path(__file__).resolve().parents[2] / "lab/mysql/init/01-schema.sql"


def indexes_declared_in_schema_file():
    declared = set()
    table_name = None
    for line in SCHEMA_FILE.read_text().splitlines():
        table_match = re.match(r"CREATE TABLE (\w+)", line)
        if table_match:
            table_name = table_match.group(1)
        key_match = re.match(r"\s*(UNIQUE )?KEY (\w+) \(([^)]*)\)", line)
        if key_match:
            declared.add(
                IndexDefinition(
                    table=table_name,
                    name=key_match.group(2),
                    columns=tuple(column.strip() for column in key_match.group(3).split(",")),
                    unique=key_match.group(1) is not None,
                )
            )
    return declared


def test_baseline_equals_the_secondary_indexes_in_the_schema_file():
    declared = indexes_declared_in_schema_file()

    assert len(declared) == 4  # the parser really found the indexes
    assert set(BASELINE_INDEXES) == declared


def test_missing_indexes_are_those_in_the_baseline_but_not_live():
    live_indexes = set(BASELINE_INDEXES) - {
        definition for definition in BASELINE_INDEXES
        if definition.name == "idx_orders_customer_created"
    }

    missing = find_missing_indexes(live_indexes)

    assert [definition.name for definition in missing] == ["idx_orders_customer_created"]
    assert find_missing_indexes(set(BASELINE_INDEXES)) == []


def test_an_index_with_the_right_name_but_wrong_columns_counts_as_missing():
    wrong_columns = IndexDefinition(
        table="orders", name="idx_orders_customer_created",
        columns=("customer_id",), unique=False,
    )
    live_indexes = {
        definition for definition in BASELINE_INDEXES
        if definition.name != "idx_orders_customer_created"
    } | {wrong_columns}

    assert [d.name for d in find_missing_indexes(live_indexes)] == ["idx_orders_customer_created"]
    assert find_unexpected_indexes(live_indexes) == [wrong_columns]


def test_create_statement_for_a_composite_index_is_online():
    definition = IndexDefinition(
        table="orders", name="idx_orders_customer_created",
        columns=("customer_id", "created_at"), unique=False,
    )

    assert create_index_sql(definition) == (
        "CREATE INDEX idx_orders_customer_created ON shop.orders "
        "(customer_id, created_at) ALGORITHM = INPLACE LOCK = NONE"
    )


def test_create_statement_for_a_unique_index():
    definition = IndexDefinition(
        table="customers", name="uq_customers_email", columns=("email",), unique=True,
    )

    assert create_index_sql(definition).startswith(
        "CREATE UNIQUE INDEX uq_customers_email ON shop.customers (email)"
    )
