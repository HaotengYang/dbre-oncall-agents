"""The query mix must be reproducible, stay inside seeded data, and never
change row counts (the lab's seed-count check depends on that)."""

import random
import re
from collections import Counter

import pytest

from shop_workload.query_mix import SeedSizes, build_query_mix, choose_operation

SEED_SIZES = SeedSizes(customers=10_000, products=1_000, orders=200_000)


def draw_operations(seed, count):
    random_source = random.Random(seed)
    query_mix = build_query_mix(SEED_SIZES)
    return [choose_operation(query_mix, random_source) for _ in range(count)]


def test_same_seed_gives_the_same_sequence():
    first_run = [(op.name, op.parameters) for op in draw_operations(seed=7, count=200)]
    second_run = [(op.name, op.parameters) for op in draw_operations(seed=7, count=200)]

    assert first_run == second_run


def test_different_seeds_give_different_sequences():
    first_run = [(op.name, op.parameters) for op in draw_operations(seed=7, count=200)]
    other_run = [(op.name, op.parameters) for op in draw_operations(seed=8, count=200)]

    assert first_run != other_run


def test_observed_frequencies_match_the_declared_weights():
    query_mix = build_query_mix(SEED_SIZES)
    total_weight = sum(template.weight for template in query_mix)
    sample_size = 20_000

    observed = Counter(op.name for op in draw_operations(seed=11, count=sample_size))

    for template in query_mix:
        expected_share = template.weight / total_weight
        observed_share = observed[template.name] / sample_size
        assert observed_share == pytest.approx(expected_share, abs=0.02), template.name


def test_mix_contains_both_reads_and_writes():
    query_mix = build_query_mix(SEED_SIZES)
    total_weight = sum(template.weight for template in query_mix)
    write_weight = sum(template.weight for template in query_mix if template.is_write)

    # Lock-wait faults need real writers; a read-only workload would hide them.
    assert 0.05 <= write_weight / total_weight <= 0.5


def test_no_statement_changes_row_counts():
    for template in build_query_mix(SEED_SIZES):
        first_keyword = template.sql.strip().split()[0].upper()
        assert first_keyword in {"SELECT", "UPDATE"}, template.name


def test_every_template_is_a_single_statement():
    for template in build_query_mix(SEED_SIZES):
        assert ";" not in template.sql, template.name


def test_parameters_are_passed_separately_not_formatted_into_sql():
    for operation in draw_operations(seed=3, count=500):
        placeholder_count = operation.sql.count("%s")
        assert placeholder_count == len(operation.parameters), operation.name
        assert not re.search(r"=\s*\d", operation.sql), operation.name


def test_ids_stay_inside_the_seeded_ranges():
    upper_bound_by_column = {
        "customer_id": SEED_SIZES.customers,
        "order_id": SEED_SIZES.orders,
        "product_id": SEED_SIZES.products,
    }
    seen_columns = set()
    for operation in draw_operations(seed=5, count=5_000):
        for column_name, value in zip(operation.parameter_names, operation.parameters):
            if column_name in upper_bound_by_column:
                seen_columns.add(column_name)
                assert 1 <= value <= upper_bound_by_column[column_name], operation.name

    assert seen_columns == set(upper_bound_by_column)


def test_writes_only_set_allowed_status_values():
    allowed_statuses = {"pending", "paid", "shipped", "delivered", "cancelled"}
    write_operations = [op for op in draw_operations(seed=9, count=5_000) if op.is_write]

    assert write_operations
    for operation in write_operations:
        status = dict(zip(operation.parameter_names, operation.parameters))["status"]
        assert status in allowed_statuses
