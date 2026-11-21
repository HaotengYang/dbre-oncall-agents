"""Injection records are the evals' ground truth, and the injector lock is
what keeps two faults from overlapping; both are pinned here."""

import json
import multiprocessing
import os
import time

from fault_injector.records import (
    ROLE_INJECTOR,
    read_lock_holder,
    injection_has_ended,
    new_injection_id,
    read_claim,
    read_injection_events,
    record_injection_ended,
    record_injection_started,
    release_injector_lock,
    remove_claim,
    try_acquire_injector_lock,
    write_claim,
)


def record_one_injection(state_directory, fault_id="lock_wait.long_transaction", hour=10):
    injection_id = new_injection_id()
    record_injection_started(
        state_directory, injection_id=injection_id, fault_id=fault_id,
        root_cause="lock_wait", started_at=f"2026-11-20T{hour}:00:00+00:00",
        changes=["hold row locks"],
    )
    return injection_id


def test_started_and_ended_events_share_an_injection_id(tmp_path):
    injection_id = record_one_injection(tmp_path)
    record_injection_ended(
        tmp_path, injection_id=injection_id, ended_at="2026-11-20T10:05:00+00:00",
        ended_by="injector",
    )

    started, ended = read_injection_events(tmp_path)

    assert started == {
        "event": "started", "injection_id": injection_id,
        "fault_id": "lock_wait.long_transaction", "root_cause": "lock_wait",
        "started_at": "2026-11-20T10:00:00+00:00", "changes": ["hold row locks"],
    }
    assert ended == {
        "event": "ended", "injection_id": injection_id,
        "ended_at": "2026-11-20T10:05:00+00:00", "ended_by": "injector",
    }


def test_each_event_is_one_json_line_and_earlier_lines_are_kept(tmp_path):
    for hour in (10, 11):
        injection_id = record_one_injection(tmp_path, hour=hour)
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


def test_has_ended_is_per_injection(tmp_path):
    finished_id = record_one_injection(tmp_path, hour=10)
    record_injection_ended(tmp_path, injection_id=finished_id,
                           ended_at="2026-11-20T10:01:00+00:00", ended_by="injector")
    open_id = record_one_injection(tmp_path, hour=11)

    assert injection_has_ended(tmp_path, finished_id) is True
    assert injection_has_ended(tmp_path, open_id) is False


def test_lock_is_exclusive_until_released(tmp_path):
    assert read_lock_holder(tmp_path) is None
    first_lock = try_acquire_injector_lock(tmp_path, ROLE_INJECTOR)

    assert first_lock is not None
    assert try_acquire_injector_lock(tmp_path, ROLE_INJECTOR) is None
    assert read_lock_holder(tmp_path) == {"role": "injector", "process_id": os.getpid()}

    release_injector_lock(first_lock)
    assert read_lock_holder(tmp_path) is None  # identity is wiped on release
    second_lock = try_acquire_injector_lock(tmp_path, ROLE_INJECTOR)
    assert second_lock is not None
    release_injector_lock(second_lock)


def _hold_lock_until_killed(state_directory, lock_is_held):
    held_lock = try_acquire_injector_lock(state_directory, ROLE_INJECTOR)
    assert held_lock is not None
    lock_is_held.set()
    while True:
        time.sleep(1)


def test_lock_held_by_another_process_is_released_when_that_process_is_killed(tmp_path):
    context = multiprocessing.get_context("spawn")
    lock_is_held = context.Event()
    holder = context.Process(target=_hold_lock_until_killed, args=(tmp_path, lock_is_held))
    holder.start()
    try:
        assert lock_is_held.wait(timeout=20)
        assert try_acquire_injector_lock(tmp_path, ROLE_INJECTOR) is None  # really held elsewhere
    finally:
        holder.kill()  # SIGKILL: the holder gets no chance to clean up
        holder.join(timeout=10)

    lock_after_kill = try_acquire_injector_lock(tmp_path, ROLE_INJECTOR)
    assert lock_after_kill is not None
    release_injector_lock(lock_after_kill)


def test_claim_round_trip_and_removal(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    assert read_claim(tmp_path) is None

    write_claim(tmp_path, injection_id="abc", fault_id="lock_wait.long_transaction",
                process_id=4242)

    assert read_claim(tmp_path) == {
        "injection_id": "abc", "fault_id": "lock_wait.long_transaction", "process_id": 4242,
    }
    assert not list(tmp_path.glob("*.writing"))  # the temporary file was renamed away
    remove_claim(tmp_path)
    assert read_claim(tmp_path) is None
    remove_claim(tmp_path)  # removing twice is harmless


def test_unreadable_claim_is_reported_not_raised(tmp_path):
    # A writer that died mid-write must not make "clear" and "status" crash.
    for broken_content in ("", "{not json", "[1, 2]"):
        (tmp_path / "active_fault.json").write_text(broken_content)

        claim = read_claim(tmp_path)

        assert claim["injection_id"] is None
        assert "unreadable" in claim["fault_id"]
