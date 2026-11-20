"""Injection records and the "one fault at a time" lock.

injections.jsonl is the ground truth the evals score against: which fault was
active, with which root-cause label, from when to when. Diagnosis-side code
must never read it.

Whether an injector is running is decided by an operating-system file lock,
not by a process id. The lock disappears with the process that holds it, even
after SIGKILL, and it cannot be mistaken for an unrelated process that later
got the same id.
"""

import contextlib
import fcntl
import json
import os
import pathlib
import uuid

DEFAULT_STATE_DIRECTORY = pathlib.Path(__file__).resolve().parents[1] / "var"
RECORDS_FILE_NAME = "injections.jsonl"
ACTIVE_FAULT_FILE_NAME = "active_fault.json"
LOCK_FILE_NAME = "injector.lock"


class ActiveFaultError(Exception):
    """Another fault is active, or a crashed injector left one behind."""


def new_injection_id():
    return uuid.uuid4().hex


def _append_event(state_directory, event):
    state_directory.mkdir(parents=True, exist_ok=True)
    with open(state_directory / RECORDS_FILE_NAME, "a", encoding="utf-8") as records_file:
        records_file.write(json.dumps(event, sort_keys=True) + "\n")


def record_injection_started(state_directory, injection_id, fault_id, root_cause,
                             started_at, changes):
    _append_event(
        state_directory,
        {
            "event": "started",
            "injection_id": injection_id,
            "fault_id": fault_id,
            "root_cause": root_cause,
            "started_at": started_at,
            "changes": list(changes),
        },
    )


def record_injection_ended(state_directory, injection_id, ended_at, ended_by):
    _append_event(
        state_directory,
        {
            "event": "ended",
            "injection_id": injection_id,
            "ended_at": ended_at,
            "ended_by": ended_by,
        },
    )


def read_injection_events(state_directory):
    records_path = state_directory / RECORDS_FILE_NAME
    if not records_path.exists():
        return []
    return [json.loads(line) for line in records_path.read_text().splitlines() if line.strip()]


def injection_has_ended(state_directory, injection_id):
    return any(
        event["event"] == "ended" and event["injection_id"] == injection_id
        for event in read_injection_events(state_directory)
    )


def try_acquire_injector_lock(state_directory):
    """Takes the exclusive injector lock without waiting.

    Returns an open file object that holds the lock, or None if another
    process (or another holder in this process) has it. Closing the file, or
    the death of the process, releases the lock.
    """
    state_directory.mkdir(parents=True, exist_ok=True)
    lock_file = open(state_directory / LOCK_FILE_NAME, "a+", encoding="utf-8")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        return None
    return lock_file


def release_injector_lock(lock_file):
    with contextlib.suppress(OSError):
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    lock_file.close()


def read_claim(state_directory):
    """Returns the claim left by the current or last injector, or None.

    A claim file that cannot be parsed (the writer died mid-write) is reported
    as a claim with unknown contents rather than raising, so "clear" can
    always remove it.
    """
    claim_path = state_directory / ACTIVE_FAULT_FILE_NAME
    if not claim_path.exists():
        return None
    try:
        claim = json.loads(claim_path.read_text())
        if not isinstance(claim, dict):
            raise ValueError("claim is not an object")
    except ValueError:
        return {"injection_id": None, "fault_id": "unknown (unreadable claim)", "process_id": None}
    return claim


def write_claim(state_directory, injection_id, fault_id, process_id):
    """Writes the claim atomically. Call only while holding the injector lock."""
    claim_path = state_directory / ACTIVE_FAULT_FILE_NAME
    temporary_path = claim_path.with_suffix(".json.writing")
    temporary_path.write_text(
        json.dumps({"injection_id": injection_id, "fault_id": fault_id, "process_id": process_id})
    )
    os.replace(temporary_path, claim_path)


def remove_claim(state_directory):
    (state_directory / ACTIVE_FAULT_FILE_NAME).unlink(missing_ok=True)
