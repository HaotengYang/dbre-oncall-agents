"""Injection records are the evals' ground truth, so their shape is pinned."""

import json
import os

import pytest

from fault_injector.records import (
    ActiveFaultError,
    claim_active_fault,
    read_active_fault,
    read_injection_events,
    record_injection_ended,
    record_injection_started,
    release_active_fault,
)


def test_started_and_ended_events_share_an_injection_id(tmp_path):
    injection_id = record_injection_started(
        tmp_path, fault_id="missing_index.dropped_index",
        root_cause="missing_index_slow_query", started_at="2026-11-20T10:00:00+00:00",
        changes=["dropped index idx_orders_customer_created"],
    )
    record_injection_ended(
        tmp_path, injection_id=injection_id, ended_at="2026-11-20T10:05:00+00:00",
        ended_by="injector",
    )

    events = read_injection_events(tmp_path)

    assert [event["event"] for event in events] == ["started", "ended"]
    assert events[0]["injection_id"] == events[1]["injection_id"] == injection_id
    assert events[0]["fault_id"] == "missing_index.dropped_index"
    assert events[0]["root_cause"] == "missing_index_slow_query"
    assert events[0]["started_at"] == "2026-11-20T10:00:00+00:00"
    assert events[0]["changes"] == ["dropped index idx_orders_customer_created"]
    assert events[1]["ended_at"] == "2026-11-20T10:05:00+00:00"
    assert events[1]["ended_by"] == "injector"


def test_each_event_is_one_json_line_and_earlier_lines_are_kept(tmp_path):
    for hour in (10, 11):
        injection_id = record_injection_started(
            tmp_path, fault_id="lock_wait.long_transaction", root_cause="lock_wait",
            started_at=f"2026-11-20T{hour}:00:00+00:00", changes=[],
        )
        record_injection_ended(
            tmp_path, injection_id=injection_id,
            ended_at=f"2026-11-20T{hour}:01:00+00:00", ended_by="injector",
        )

    lines = (tmp_path / "injections.jsonl").read_text().splitlines()

    assert len(lines) == 4
    assert all(isinstance(json.loads(line), dict) for line in lines)
    assert len({json.loads(line)["injection_id"] for line in lines}) == 2


def test_no_records_file_reads_as_no_events(tmp_path):
    assert read_injection_events(tmp_path) == []


def test_claiming_twice_is_refused_while_the_first_owner_is_alive(tmp_path):
    claim_active_fault(tmp_path, injection_id="first", fault_id="lock_wait.long_transaction",
                       process_id=os.getpid())

    with pytest.raises(ActiveFaultError, match="lock_wait.long_transaction"):
        claim_active_fault(tmp_path, injection_id="second",
                           fault_id="missing_index.dropped_index", process_id=os.getpid())

    assert read_active_fault(tmp_path)["injection_id"] == "first"


def test_a_claim_left_by_a_dead_process_is_still_refused_and_reported_stale(tmp_path):
    dead_process_id = 2 ** 22 + 12345  # above the default macOS and Linux pid range
    claim_active_fault(tmp_path, injection_id="crashed", fault_id="lock_wait.long_transaction",
                       process_id=dead_process_id)

    # A crashed injector may have left the lab changed. Starting another fault
    # on top would make the ground truth wrong, so "clear" must run first.
    with pytest.raises(ActiveFaultError, match="clear"):
        claim_active_fault(tmp_path, injection_id="next",
                           fault_id="missing_index.dropped_index", process_id=os.getpid())

    active_fault = read_active_fault(tmp_path)
    assert active_fault["injection_id"] == "crashed"
    assert active_fault["owner_alive"] is False


def test_release_allows_the_next_claim(tmp_path):
    claim_active_fault(tmp_path, injection_id="first", fault_id="lock_wait.long_transaction",
                       process_id=os.getpid())
    release_active_fault(tmp_path)

    assert read_active_fault(tmp_path) is None
    claim_active_fault(tmp_path, injection_id="second", fault_id="lock_wait.long_transaction",
                       process_id=os.getpid())
    assert read_active_fault(tmp_path)["injection_id"] == "second"


def test_release_without_a_claim_is_harmless(tmp_path):
    release_active_fault(tmp_path)

    assert read_active_fault(tmp_path) is None
