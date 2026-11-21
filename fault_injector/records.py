"""Injection records and the "one fault at a time" lock.

injections.jsonl is the ground truth the evals score against: which fault was
active, with which root-cause label, from when to when. Diagnosis-side code
must never read it.

Whether an injector is running is decided by an operating-system file lock,
not by a process id. The lock disappears with the process that holds it, even
after SIGKILL, and it cannot be mistaken for an unrelated process that later
got the same id.

Nothing in this package ever sends a signal to another process. "clear" stops
a running injector by writing a stop request file, which the injector polls.
A request that reaches no injector does nothing; a signal sent to a process id
read from a file can hit an unrelated process, and three review rounds kept
finding ways for that to happen. The role and process id that a holder writes
into the lock file are for messages only and are never acted on.

Known limits:
- The lock is scoped to this state directory, not to the lab MySQL. Two
  checkouts (for example two git worktrees) pointed at the same lab do not
  exclude each other.
- The lock belongs to the open file, which a fork() child would share. Nothing
  here forks while holding it; code that does must not outlive the injector.
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
STOP_REQUEST_FILE_NAME = "stop_requested"

ROLE_INJECTOR = "injector"
ROLE_CLEAR = "clear"
ROLE_STATUS = "status"


class ActiveFaultError(Exception):
    """Another fault is active, or a crashed injector left one behind."""


def new_injection_id():
    return uuid.uuid4().hex


def _append_event(state_directory, event):
    state_directory.mkdir(parents=True, exist_ok=True)
    with open(state_directory / RECORDS_FILE_NAME, "a", encoding="utf-8") as records_file:
        records_file.write(json.dumps(event, sort_keys=True) + "\n")


def record_injection_started(state_directory, injection_id, fault_id, root_cause,
                             started_at, changes, expected_symptoms=()):
    _append_event(
        state_directory,
        {
            "event": "started",
            "injection_id": injection_id,
            "fault_id": fault_id,
            "root_cause": root_cause,
            "expected_symptoms": list(expected_symptoms),
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


def injection_ended_by(state_directory, injection_id):
    """Returns who closed the injection ("injector" or "clear"), or None if open."""
    for event in read_injection_events(state_directory):
        if event["event"] == "ended" and event["injection_id"] == injection_id:
            return event["ended_by"]
    return None


def injection_has_ended(state_directory, injection_id):
    return injection_ended_by(state_directory, injection_id) is not None


def try_acquire_injector_lock(state_directory, holder_role):
    """Takes the exclusive injector lock without waiting.

    Returns an open file object that holds the lock, or None if another
    process (or another holder in this process) has it. Closing the file, or
    the death of the process, releases the lock. The holder's role and process
    id are written into the lock file for read_lock_holder().
    """
    state_directory.mkdir(parents=True, exist_ok=True)
    lock_file = open(state_directory / LOCK_FILE_NAME, "a+", encoding="utf-8")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        return None
    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(json.dumps({"role": holder_role, "process_id": os.getpid()}))
    lock_file.flush()
    return lock_file


def release_injector_lock(lock_file):
    with contextlib.suppress(OSError, ValueError):
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.flush()
    with contextlib.suppress(OSError):
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    lock_file.close()


def read_lock_holder(state_directory):
    """Returns {"role", "process_id"} as written by the current lock holder.

    Returns None when the file is empty or unreadable. The content can be
    stale: a holder that was killed never wipes it, and a new holder has not
    yet overwritten it in the instant after taking the lock. Use it for
    messages only; never act on it.
    """
    lock_path = state_directory / LOCK_FILE_NAME
    try:
        holder = json.loads(lock_path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(holder, dict) or "role" not in holder or "process_id" not in holder:
        return None
    return holder


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


def request_stop(state_directory):
    """Asks a running injector to stop. It polls for this file."""
    state_directory.mkdir(parents=True, exist_ok=True)
    (state_directory / STOP_REQUEST_FILE_NAME).write_text("stop\n")


def stop_was_requested(state_directory):
    return (state_directory / STOP_REQUEST_FILE_NAME).exists()


def withdraw_stop_request(state_directory):
    (state_directory / STOP_REQUEST_FILE_NAME).unlink(missing_ok=True)

