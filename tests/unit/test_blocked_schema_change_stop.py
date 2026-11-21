"""Stopping the blocked-schema-change fault must cancel the waiting schema
change before it releases the transaction that blocks it. In the other order
the index would really be built. The baseline restore would then remove it,
so only a test of the order itself can catch a regression."""

from fault_injector.faults.lock_wait import BlockedSchemaChange


class RecordingCursor:
    def __init__(self, connection_name, event_log, fail_on=None):
        self._connection_name, self._event_log, self._fail_on = connection_name, event_log, fail_on

    def __enter__(self):
        return self

    def __exit__(self, *exception_details):
        return False

    def execute(self, sql, parameters=None):
        self._event_log.append(f"{self._connection_name}: {sql}")
        if self._fail_on and self._fail_on in sql:
            raise RuntimeError("statement failed")


class RecordingConnection:
    def __init__(self, connection_name, event_log, fail_on=None):
        self._connection_name, self._event_log, self._fail_on = connection_name, event_log, fail_on

    def cursor(self):
        return RecordingCursor(self._connection_name, self._event_log, self._fail_on)

    def rollback(self):
        self._event_log.append(f"{self._connection_name}: rollback")

    def close(self):
        self._event_log.append(f"{self._connection_name}: close")


class FakeLabAccess:
    def __init__(self, event_log, control_fails_on=None):
        self.control_connection = RecordingConnection("control", event_log, control_fails_on)


def started_fault(event_log, ddl_session_id=4711):
    fault = BlockedSchemaChange()
    fault._holder_connection = RecordingConnection("holder", event_log)
    fault._ddl_connection = RecordingConnection("ddl", event_log)
    fault._ddl_session_id = ddl_session_id
    return fault


def test_stop_kills_the_schema_change_before_releasing_the_holder():
    event_log = []
    fault = started_fault(event_log)

    fault.stop(FakeLabAccess(event_log))

    kill_position = event_log.index("control: KILL 4711")
    holder_release_position = event_log.index("holder: rollback")
    assert kill_position < holder_release_position
    assert event_log.index("ddl: close") < holder_release_position
    assert "holder: close" in event_log


def test_stop_still_releases_everything_when_the_kill_fails():
    event_log = []
    fault = started_fault(event_log)

    fault.stop(FakeLabAccess(event_log, control_fails_on="KILL"))

    assert "holder: rollback" in event_log and "holder: close" in event_log
    assert "ddl: close" in event_log


def test_stop_after_a_start_that_never_got_going_does_nothing():
    event_log = []

    BlockedSchemaChange().stop(FakeLabAccess(event_log))

    assert event_log == []


def test_stop_twice_is_harmless():
    event_log = []
    fault = started_fault(event_log)
    fault.stop(FakeLabAccess(event_log))
    events_after_first_stop = list(event_log)

    fault.stop(FakeLabAccess(event_log))

    assert event_log == events_after_first_stop
