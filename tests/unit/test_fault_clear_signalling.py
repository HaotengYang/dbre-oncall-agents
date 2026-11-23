"""clear never signals any process. It leaves a stop request that a running
injector acts on by itself, and it releases nothing when it cannot get the
injector lock."""

import multiprocessing
import time

import pytest

from fault_injector import engine, records

CHILD_LIFETIME_LIMIT_SECONDS = 60  # children end by themselves if the test run is killed


def _hold_lock_ignoring_stop_requests(state_directory, lock_is_held):
    held_lock = records.try_acquire_injector_lock(state_directory, records.ROLE_INJECTOR)
    assert held_lock is not None
    lock_is_held.set()
    time.sleep(CHILD_LIFETIME_LIMIT_SECONDS)


def _hold_lock_until_stop_is_requested(state_directory, lock_is_held):
    held_lock = records.try_acquire_injector_lock(state_directory, records.ROLE_INJECTOR)
    assert held_lock is not None
    lock_is_held.set()
    give_up_at = time.monotonic() + CHILD_LIFETIME_LIMIT_SECONDS
    while time.monotonic() < give_up_at and not records.stop_was_requested(state_directory):
        time.sleep(0.05)
    records.release_injector_lock(held_lock)


def _hold_lock_briefly(state_directory, lock_is_held, hold_seconds):
    held_lock = records.try_acquire_injector_lock(state_directory, records.ROLE_CLEAR)
    assert held_lock is not None
    lock_is_held.set()
    time.sleep(hold_seconds)
    records.release_injector_lock(held_lock)


@pytest.fixture
def spawn():
    context = multiprocessing.get_context("spawn")
    started_processes = []

    def start(target, *arguments):
        process = context.Process(target=target, args=arguments, daemon=True)
        process.start()
        started_processes.append(process)
        return process

    start.event = context.Event
    yield start
    for process in started_processes:
        process.kill()
        process.join(timeout=10)


@pytest.fixture
def restore_calls(monkeypatch):
    """Replaces the database restore and counts how often it is reached."""
    calls = []
    monkeypatch.setattr(engine, "load_lab_settings", lambda: None)
    monkeypatch.setattr(
        engine, "restore_baseline_on_fresh_connection",
        lambda settings: calls.append("restore") or [],
    )
    return calls


def test_a_holder_that_never_stops_is_left_alone_and_nothing_is_released(
    tmp_path, spawn, restore_calls, monkeypatch
):
    monkeypatch.setattr(engine, "STOP_WAIT_SECONDS", 1.5)
    lock_is_held = spawn.event()
    holder = spawn(_hold_lock_ignoring_stop_requests, tmp_path, lock_is_held)
    assert lock_is_held.wait(timeout=20)
    # Lock file and claim both name the holder. Under the old design this is
    # exactly the case in which it would have been sent SIGTERM.
    records.write_claim(tmp_path, "running-injection", "lock_wait.long_transaction", holder.pid)
    assert records.read_lock_holder(tmp_path) == {"role": "injector", "process_id": holder.pid}

    with pytest.raises(engine.ClearError, match="still held"):
        engine.clear_lab(state_directory=tmp_path)

    time.sleep(0.5)
    assert holder.is_alive()  # a signalled process would be dead
    assert restore_calls == []  # nothing was restored without the lock
    assert records.read_claim(tmp_path)["injection_id"] == "running-injection"
    assert records.read_injection_events(tmp_path) == []  # no "ended" was invented
    assert not records.stop_was_requested(tmp_path)  # no request left for the next injector


def test_a_polling_injector_stops_on_request_and_clear_proceeds(tmp_path, spawn, restore_calls):
    lock_is_held = spawn.event()
    injector = spawn(_hold_lock_until_stop_is_requested, tmp_path, lock_is_held)
    assert lock_is_held.wait(timeout=20)

    engine.clear_lab(state_directory=tmp_path)

    injector.join(timeout=10)
    assert injector.exitcode == 0  # it left by itself, not by a signal
    assert restore_calls == ["restore"]
    assert not records.stop_was_requested(tmp_path)


def test_clear_waits_its_turn_behind_another_clear(tmp_path, spawn, restore_calls):
    lock_is_held = spawn.event()
    other_clear = spawn(_hold_lock_briefly, tmp_path, lock_is_held, 1.0)
    assert lock_is_held.wait(timeout=20)
    waiting_started_at = time.monotonic()

    engine.clear_lab(state_directory=tmp_path)

    assert time.monotonic() - waiting_started_at >= 0.5  # it really waited
    other_clear.join(timeout=10)
    assert other_clear.exitcode == 0
    assert restore_calls == ["restore"]


def test_stop_request_can_be_written_and_withdrawn_repeatedly(tmp_path):
    records.request_stop(tmp_path)
    records.request_stop(tmp_path)
    assert records.stop_was_requested(tmp_path)

    records.withdraw_stop_request(tmp_path)

    assert not records.stop_was_requested(tmp_path)
    records.withdraw_stop_request(tmp_path)  # withdrawing twice is harmless


def _hold_lock_and_withdraw_the_first_request_like_a_starting_injector(
    state_directory, lock_is_held
):
    held_lock = records.try_acquire_injector_lock(state_directory, records.ROLE_INJECTOR)
    assert held_lock is not None
    lock_is_held.set()
    give_up_at = time.monotonic() + CHILD_LIFETIME_LIMIT_SECONDS
    # A starting injector deletes whatever request it finds, once.
    while time.monotonic() < give_up_at and not records.stop_was_requested(state_directory):
        time.sleep(0.01)
    records.withdraw_stop_request(state_directory)
    # After that it polls like a running injector.
    while time.monotonic() < give_up_at and not records.stop_was_requested(state_directory):
        time.sleep(0.05)
    records.release_injector_lock(held_lock)


def test_request_withdrawn_by_a_starting_injector_is_sent_again(tmp_path, spawn, restore_calls):
    lock_is_held = spawn.event()
    injector = spawn(
        _hold_lock_and_withdraw_the_first_request_like_a_starting_injector,
        tmp_path, lock_is_held,
    )
    assert lock_is_held.wait(timeout=20)
    waiting_started_at = time.monotonic()

    engine.clear_lab(state_directory=tmp_path)

    # With a request written only once, clear would wait the full
    # STOP_WAIT_SECONDS (60) and then fail.
    assert time.monotonic() - waiting_started_at < 10
    injector.join(timeout=10)
    assert injector.exitcode == 0
    assert restore_calls == ["restore"]


def test_clear_does_not_claim_to_have_stopped_an_injector_closed_by_another_clear(
    tmp_path, spawn, restore_calls
):
    # A crashed injector's claim and open record; another clear holds the lock
    # and closes that record, as it would in real use.
    injection_id = records.new_injection_id()
    records.record_injection_started(
        tmp_path, injection_id=injection_id, fault_id="lock_wait.long_transaction",
        root_cause="lock_wait", started_at="2026-11-20T09:00:00.000+00:00", changes=[],
    )
    records.write_claim(tmp_path, injection_id, "lock_wait.long_transaction", 4242)
    lock_is_held = spawn.event()
    spawn(_hold_lock_briefly, tmp_path, lock_is_held, 1.0)
    assert lock_is_held.wait(timeout=20)
    records.record_injection_ended(
        tmp_path, injection_id=injection_id,
        ended_at="2026-11-20T09:05:00.000+00:00", ended_by="clear",
    )

    actions = engine.clear_lab(state_directory=tmp_path)

    assert not any("stopped running injector" in action for action in actions)


def test_the_package_never_sends_a_signal():
    """The whole point of the stop-request design, pinned against regressions."""
    import pathlib

    package_directory = pathlib.Path(records.__file__).parent
    offending_lines = [
        f"{source_path.name}:{line_number}"
        for source_path in package_directory.rglob("*.py")
        for line_number, line in enumerate(source_path.read_text().splitlines(), start=1)
        if any(
            forbidden in line
            for forbidden in (
                "os.kill", "import kill", "killpg", "send_signal", ".terminate(", ".kill(",
                "pthread_kill", "raise_signal", "os.fork",
            )
        )
        # Starting processes is allowed in one place only: the module that
        # changes the MySQL container's CPU limit through the docker CLI.
        or ("subprocess" in line and source_path.name != "container_limits.py")
    ]

    assert offending_lines == []
