"""Runs one fault from start to clean stop, and clears the lab."""

import contextlib
import datetime
import os
import signal
import time

from fault_injector import records
from fault_injector.baseline import (
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

STOP_WAIT_SECONDS = 15


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


def open_guarded_control_connection(settings):
    control_connection = open_connection(settings, FAULT_ACCOUNT)
    try:
        assert_lab_environment(control_connection)
    except Exception:
        control_connection.close()
        raise
    return control_connection


@contextlib.contextmanager
def injected_fault(fault_id, settings=None, state_directory=None):
    """Activates a fault for the duration of the with-block.

    Order matters. Check that the target is the lab before anything else.
    Claim "one fault at a time" before changing anything. Write the started
    record before the fault starts, so a crash still leaves ground truth.
    On the way out, stop the fault first (this frees any connections it
    holds), then restore the baseline on a fresh connection.
    """
    settings = settings or load_lab_settings()
    state_directory = state_directory or records.DEFAULT_STATE_DIRECTORY
    fault = find_fault(fault_id)

    control_connection = open_guarded_control_connection(settings)
    lab_access = LabAccess(settings, control_connection)
    injection_id = None
    try:
        # Reserve the claim under a placeholder id, then record the start.
        records.claim_active_fault(
            state_directory, injection_id="pending", fault_id=fault.fault_id,
            process_id=os.getpid(),
        )
        try:
            started_at = current_timestamp()
            changes = fault.start(lab_access)
            injection_id = records.record_injection_started(
                state_directory, fault_id=fault.fault_id, root_cause=fault.root_cause,
                started_at=started_at, changes=changes,
            )
            yield {"injection_id": injection_id, "fault_id": fault.fault_id,
                   "root_cause": fault.root_cause, "started_at": started_at,
                   "changes": changes}
        finally:
            try:
                fault.stop(lab_access)
            finally:
                with contextlib.suppress(Exception):
                    control_connection.close()
                restore_connection = open_guarded_control_connection(settings)
                try:
                    restore_baseline(restore_connection)
                finally:
                    restore_connection.close()
                if injection_id is not None:
                    records.record_injection_ended(
                        state_directory, injection_id=injection_id,
                        ended_at=current_timestamp(), ended_by="injector",
                    )
                records.release_active_fault(state_directory)
    finally:
        with contextlib.suppress(Exception):
            control_connection.close()


def clear_lab(settings=None, state_directory=None):
    """Returns the lab to its baseline, whatever state it was left in.

    Stops a running injector if there is one, restores missing baseline
    indexes, closes the record of an injection whose process died, and removes
    the claim. Safe to run any number of times.
    """
    settings = settings or load_lab_settings()
    state_directory = state_directory or records.DEFAULT_STATE_DIRECTORY
    actions = []

    active_fault = records.read_active_fault(state_directory)
    if active_fault and active_fault["owner_alive"]:
        os.kill(active_fault["process_id"], signal.SIGTERM)
        deadline = time.monotonic() + STOP_WAIT_SECONDS
        while records.is_process_alive(active_fault["process_id"]) and time.monotonic() < deadline:
            time.sleep(0.2)
        actions.append(f"stopped running injector for {active_fault['fault_id']}")
        active_fault = records.read_active_fault(state_directory)

    control_connection = open_guarded_control_connection(settings)
    try:
        for restored_name in restore_baseline(control_connection):
            actions.append(f"recreated index {restored_name}")
    finally:
        control_connection.close()

    if active_fault:
        # The injector did not end this injection itself. Find its started
        # record (the last one without an ended record) and close it.
        events = records.read_injection_events(state_directory)
        ended_ids = {event["injection_id"] for event in events if event["event"] == "ended"}
        open_injections = [
            event for event in events
            if event["event"] == "started" and event["injection_id"] not in ended_ids
        ]
        for open_injection in open_injections:
            records.record_injection_ended(
                state_directory, injection_id=open_injection["injection_id"],
                ended_at=current_timestamp(), ended_by="clear",
            )
        records.release_active_fault(state_directory)
        actions.append(f"removed stale claim for {active_fault['fault_id']}")
    return actions


def describe_lab_state(settings=None, state_directory=None):
    """Returns the active fault, if any, and how the schema differs from baseline."""
    settings = settings or load_lab_settings()
    state_directory = state_directory or records.DEFAULT_STATE_DIRECTORY
    control_connection = open_guarded_control_connection(settings)
    try:
        live_indexes = read_live_indexes(control_connection)
    finally:
        control_connection.close()
    return {
        "active_fault": records.read_active_fault(state_directory),
        "missing_indexes": [d.name for d in find_missing_indexes(live_indexes)],
        "unexpected_indexes": [d.name for d in find_unexpected_indexes(live_indexes)],
    }
