"""The injector's failure paths: whatever goes wrong, the lab ends up at
baseline, the ground truth stays right, and nothing is left locked."""

import pytest

from fault_injector import engine, records
from fault_injector.baseline import BASELINE_INDEXES, drop_index_sql
from fault_injector.faults.base import Fault
from tests.lab.conftest import fetch_all
from tests.lab.fault_helpers import run_injector

pytestmark = pytest.mark.lab

DROPPED_INDEX_NAME = "idx_orders_customer_created"


def orders_index_names(connection):
    return {row[2] for row in fetch_all(connection, "SHOW INDEX FROM shop.orders")}


def lock_is_free(state_directory):
    probe_lock = records.try_acquire_injector_lock(state_directory)
    if probe_lock is None:
        return False
    records.release_injector_lock(probe_lock)
    return True


@pytest.fixture(autouse=True)
def lab_at_baseline_before_and_after():
    assert run_injector("clear").returncode == 0
    yield
    assert run_injector("clear").returncode == 0


def test_exception_inside_the_block_still_restores_and_records(connect_as, tmp_path):
    diag_connection = connect_as("shop_diag")

    with pytest.raises(RuntimeError, match="eval runner failed"):
        with engine.injected_fault("missing_index.dropped_index", state_directory=tmp_path):
            assert DROPPED_INDEX_NAME not in orders_index_names(diag_connection)
            raise RuntimeError("eval runner failed")

    assert DROPPED_INDEX_NAME in orders_index_names(diag_connection)
    started, ended = records.read_injection_events(tmp_path)
    assert started["injection_id"] == ended["injection_id"]
    assert ended["ended_by"] == "injector"
    assert records.read_claim(tmp_path) is None
    assert lock_is_free(tmp_path)


class FaultThatBreaksWhileStarting(Fault):
    fault_id = "missing_index.dropped_index"
    root_cause = "missing_index_slow_query"
    planned_changes = ("drop an index, then fail",)

    def start(self, lab_access):
        definition = next(d for d in BASELINE_INDEXES if d.name == DROPPED_INDEX_NAME)
        with lab_access.control_connection.cursor() as cursor:
            cursor.execute(drop_index_sql(definition))
        raise RuntimeError("start failed after changing the lab")

    def stop(self, lab_access):
        pass


def test_start_that_fails_after_changing_the_lab_is_undone_and_recorded(
    connect_as, tmp_path, monkeypatch
):
    diag_connection = connect_as("shop_diag")
    monkeypatch.setattr(engine, "find_fault", lambda fault_id: FaultThatBreaksWhileStarting())
    block_was_entered = False

    with pytest.raises(RuntimeError, match="start failed"):
        with engine.injected_fault("missing_index.dropped_index", state_directory=tmp_path):
            block_was_entered = True

    assert block_was_entered is False
    assert DROPPED_INDEX_NAME in orders_index_names(diag_connection)
    # The lab really was changed for a moment, so ground truth must say so.
    assert [event["event"] for event in records.read_injection_events(tmp_path)] == [
        "started", "ended",
    ]
    assert records.read_claim(tmp_path) is None
    assert lock_is_free(tmp_path)


def test_clear_from_inside_a_running_injection_is_refused_and_harmless(connect_as, tmp_path):
    diag_connection = connect_as("shop_diag")

    with engine.injected_fault("missing_index.dropped_index", state_directory=tmp_path):
        with pytest.raises(engine.ClearError, match="inside the running injection"):
            engine.clear_lab(state_directory=tmp_path)
        # Still running, fault still in place, process not signalled.
        assert DROPPED_INDEX_NAME not in orders_index_names(diag_connection)
        assert records.read_claim(tmp_path) is not None

    assert DROPPED_INDEX_NAME in orders_index_names(diag_connection)


def test_clear_closes_only_the_injection_named_in_the_leftover_claim(tmp_path):
    # An older injection whose ended record was never written (for example the
    # file was edited by hand). It must not be given today's end time.
    older_injection_id = records.new_injection_id()
    records.record_injection_started(
        tmp_path, injection_id=older_injection_id, fault_id="lock_wait.long_transaction",
        root_cause="lock_wait", started_at="2026-11-19T09:00:00.000+00:00", changes=[],
    )
    # A crashed injector: started record and claim exist, nobody holds the lock.
    crashed_injection_id = records.new_injection_id()
    records.record_injection_started(
        tmp_path, injection_id=crashed_injection_id, fault_id="missing_index.dropped_index",
        root_cause="missing_index_slow_query", started_at="2026-11-20T09:00:00.000+00:00",
        changes=[],
    )
    records.write_claim(tmp_path, crashed_injection_id, "missing_index.dropped_index", 4242)

    with pytest.raises(records.ActiveFaultError, match="clear"):
        with engine.injected_fault("lock_wait.long_transaction", state_directory=tmp_path):
            pass

    engine.clear_lab(state_directory=tmp_path)

    ended_events = [e for e in records.read_injection_events(tmp_path) if e["event"] == "ended"]
    assert [event["injection_id"] for event in ended_events] == [crashed_injection_id]
    assert ended_events[0]["ended_by"] == "clear"
    assert records.injection_has_ended(tmp_path, older_injection_id) is False
    assert records.read_claim(tmp_path) is None

    engine.clear_lab(state_directory=tmp_path)  # a second clear adds nothing
    assert len(records.read_injection_events(tmp_path)) == 3


def test_unreadable_claim_does_not_stop_clear(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "active_fault.json").write_text("")

    actions = engine.clear_lab(state_directory=tmp_path)

    assert any("leftover claim" in action for action in actions)
    assert records.read_claim(tmp_path) is None
