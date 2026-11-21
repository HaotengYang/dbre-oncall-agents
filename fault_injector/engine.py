"""Runs one fault from start to clean stop, and clears the lab."""

import contextlib
import datetime
import os
import time

import pymysql

from fault_injector import records
from fault_injector.baseline import (
    DDL_LOCK_WAIT_TIMEOUT_SECONDS,
    end_orphaned_schema_sessions,
    find_missing_indexes,
    find_unexpected_indexes,
    read_live_indexes,
    restore_baseline,
)
from fault_injector.lab import (
    APP_ACCOUNT,
    FAULT_ACCOUNT,
    assert_lab_environment,
    load_lab_settings,
    open_connection,
)
from fault_injector.registry import find_fault

# Longer than the DDL lock wait, so a stopping injector that is waiting for a
# metadata lock gets to finish its own restore before "clear" gives up on it.
STOP_WAIT_SECONDS = DDL_LOCK_WAIT_TIMEOUT_SECONDS + 30
TOO_MANY_CONNECTIONS_ERROR = 1040
CONNECT_RETRY_SECONDS = 15


class ClearError(Exception):
    """The lab could not be cleared; nothing was released."""


def current_timestamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")


class LabAccess:
    """What a fault may use: the schema account's connection, and a way to
    open connections as the application."""

    def __init__(self, settings, control_connection):
        self._settings = settings
        self.control_connection = control_connection

    def open_app_connection(self, autocommit=True):
        return open_connection(self._settings, APP_ACCOUNT, autocommit=autocommit)

    def open_schema_connection(self):
        """A second connection as the schema account, for a statement that
        is meant to block (the control connection must stay usable)."""
        return open_connection(self._settings, FAULT_ACCOUNT)


def open_guarded_control_connection(settings):
    """Opens the schema account's connection and checks the lab marker.

    Right after a connection flood ends the server may still be releasing
    sessions, so "too many connections" is retried for a short while. DDL on
    this connection gives up after a bounded lock wait instead of the server
    default of a year.
    """
    deadline = time.monotonic() + CONNECT_RETRY_SECONDS
    while True:
        try:
            control_connection = open_connection(settings, FAULT_ACCOUNT)
            break
        except pymysql.err.OperationalError as connect_error:
            refused_for_capacity = connect_error.args[0] == TOO_MANY_CONNECTIONS_ERROR
            if not refused_for_capacity or time.monotonic() >= deadline:
                raise
            time.sleep(0.5)
    try:
        assert_lab_environment(control_connection)
        with control_connection.cursor() as cursor:
            cursor.execute(f"SET SESSION lock_wait_timeout = {DDL_LOCK_WAIT_TIMEOUT_SECONDS}")
    except Exception:
        control_connection.close()
        raise
    return control_connection


def restore_baseline_on_fresh_connection(settings):
    """Restores the baseline. Callers hold the injector lock and have already
    stopped their own fault, so any other session of the schema account is an
    orphan from a dead injector and is ended first."""
    restore_connection = open_guarded_control_connection(settings)
    try:
        end_orphaned_schema_sessions(restore_connection)
        return restore_baseline(restore_connection)
    finally:
        restore_connection.close()


@contextlib.contextmanager
def injected_fault(fault_id, settings=None, state_directory=None):
    """Activates a fault for the duration of the with-block.

    Order matters:
    1. Take the injector lock. Needs no database, so a second injector is
       refused cleanly even when the database accepts no connections.
    2. Refuse if a claim is left over from an injector that died.
    3. Check the target is the lab.
    4. Write the claim and the started record, then start the fault. A crash
       at any later point leaves both behind for "clear".
    5. On the way out: stop the fault (this frees what it holds), restore the
       baseline on a fresh connection, write the ended record, remove the
       claim, release the lock. The claim is removed only if the restore
       succeeded; otherwise it stays so the next "inject" is refused until
       "clear" has run.
    """
    settings = settings or load_lab_settings()
    state_directory = state_directory or records.DEFAULT_STATE_DIRECTORY
    fault = find_fault(fault_id)

    injector_lock = records.try_acquire_injector_lock(state_directory, records.ROLE_INJECTOR)
    if injector_lock is None:
        running = records.read_claim(state_directory) or {}
        raise records.ActiveFaultError(
            f"a fault is already active ({running.get('fault_id', 'starting')}). "
            "Stop it or run: python -m fault_injector clear"
        )
    try:
        leftover_claim = records.read_claim(state_directory)
        if leftover_claim is not None:
            raise records.ActiveFaultError(
                f"fault {leftover_claim['fault_id']} was left behind by an injector that "
                "is gone. Run: python -m fault_injector clear"
            )

        # A request addressed to an earlier injector must not stop this one.
        records.withdraw_stop_request(state_directory)
        control_connection = open_guarded_control_connection(settings)
        lab_access = LabAccess(settings, control_connection)
        injection_id = records.new_injection_id()
        started_at = current_timestamp()
        records.write_claim(state_directory, injection_id, fault.fault_id, os.getpid())
        records.record_injection_started(
            state_directory, injection_id=injection_id, fault_id=fault.fault_id,
            root_cause=fault.root_cause, started_at=started_at,
            changes=fault.planned_changes, expected_symptoms=fault.expected_symptoms,
        )
        try:
            fault.start(lab_access)
            yield {"injection_id": injection_id, "fault_id": fault.fault_id,
                   "root_cause": fault.root_cause,
                   "expected_symptoms": list(fault.expected_symptoms),
                   "started_at": started_at,
                   "changes": list(fault.planned_changes)}
        finally:
            try:
                fault.stop(lab_access)
            finally:
                with contextlib.suppress(Exception):
                    control_connection.close()
                restore_baseline_on_fresh_connection(settings)
                records.record_injection_ended(
                    state_directory, injection_id=injection_id,
                    ended_at=current_timestamp(), ended_by="injector",
                )
                records.remove_claim(state_directory)
    finally:
        records.release_injector_lock(injector_lock)


def _wait_for_lock_holder(state_directory):
    """Asks a running injector to stop and waits for the injector lock.

    Writes a stop request, which a command-line injector polls for, and waits
    until this process can take the lock. No signal is sent to anyone. If the
    holder is another "clear" or a "status" probe, the request is ignored by
    them and this simply waits its turn.

    An injection running through the Python API (the eval runner) does not
    poll; it ends when its with-block ends. If that takes longer than the
    wait, this raises and nothing is cleared.
    """
    holder = records.read_lock_holder(state_directory)
    if holder is not None and holder["process_id"] == os.getpid():
        raise ClearError("clear was called from inside the running injection")

    try:
        deadline = time.monotonic() + STOP_WAIT_SECONDS
        while time.monotonic() < deadline:
            # Rewritten on every pass: an injector that starts while clear is
            # waiting withdraws old requests, and must still be told to stop.
            records.request_stop(state_directory)
            injector_lock = records.try_acquire_injector_lock(state_directory, records.ROLE_CLEAR)
            if injector_lock is not None:
                return injector_lock
            time.sleep(0.2)
    finally:
        # Never leave a request behind: it would stop the next injection.
        records.withdraw_stop_request(state_directory)
    raise ClearError(
        f"the injector lock was still held after {STOP_WAIT_SECONDS} s. Nothing was cleared. "
        "If a fault is running in another terminal, stop it there with Ctrl-C."
    )


def clear_lab(settings=None, state_directory=None):
    """Returns the lab to its baseline, whatever state it was left in.

    Stops a running injector if there is one, restores missing baseline
    indexes, closes the record of an injection whose injector died, and
    removes its claim. Holds the injector lock throughout, so no injector can
    start while the lab is being cleared. Safe to run any number of times.
    """
    settings = settings or load_lab_settings()
    state_directory = state_directory or records.DEFAULT_STATE_DIRECTORY
    actions = []

    injector_lock = records.try_acquire_injector_lock(state_directory, records.ROLE_CLEAR)
    if injector_lock is None:
        claim_while_held = records.read_claim(state_directory)
        injector_lock = _wait_for_lock_holder(state_directory)
        # Say an injector stopped only on evidence: there was a claim while
        # the lock was held, and that injection was then closed by its own
        # injector (not by another clear). Whether it stopped because of this
        # request or by itself in the same moment cannot be told apart.
        if (
            claim_while_held is not None
            and claim_while_held.get("injection_id")
            and records.injection_ended_by(state_directory, claim_while_held["injection_id"])
            == "injector"
        ):
            actions.append(f"stopped running injector for {claim_while_held['fault_id']}")
    try:
        for restored_name in restore_baseline_on_fresh_connection(settings):
            actions.append(f"recreated index {restored_name}")

        leftover_claim = records.read_claim(state_directory)
        if leftover_claim is not None:
            # Close exactly the injection named in the claim, and only if its
            # own injector did not already close it.
            injection_id = leftover_claim.get("injection_id")
            if injection_id and not records.injection_has_ended(state_directory, injection_id):
                records.record_injection_ended(
                    state_directory, injection_id=injection_id,
                    ended_at=current_timestamp(), ended_by="clear",
                )
            records.remove_claim(state_directory)
            actions.append(f"removed leftover claim for {leftover_claim['fault_id']}")
    finally:
        records.release_injector_lock(injector_lock)
    return actions


def describe_lab_state(settings=None, state_directory=None):
    """Returns the active fault, if any, and how the schema differs from baseline."""
    settings = settings or load_lab_settings()
    state_directory = state_directory or records.DEFAULT_STATE_DIRECTORY

    claim = records.read_claim(state_directory)
    probe_lock = records.try_acquire_injector_lock(state_directory, records.ROLE_STATUS)
    injector_running = probe_lock is None
    if probe_lock is not None:
        records.release_injector_lock(probe_lock)

    control_connection = open_guarded_control_connection(settings)
    try:
        live_indexes = read_live_indexes(control_connection)
    finally:
        control_connection.close()
    return {
        "claim": claim,
        "injector_running": injector_running,
        "missing_indexes": [d.name for d in find_missing_indexes(live_indexes)],
        "unexpected_indexes": [d.name for d in find_unexpected_indexes(live_indexes)],
    }
