"""Injection records and the "one fault at a time" claim.

injections.jsonl is the ground truth the evals score against: which fault was
active, with which root-cause label, from when to when. Diagnosis-side code
must never read it.
"""

import json
import os
import pathlib
import uuid

DEFAULT_STATE_DIRECTORY = pathlib.Path(__file__).resolve().parents[1] / "var"
RECORDS_FILE_NAME = "injections.jsonl"
ACTIVE_FAULT_FILE_NAME = "active_fault.json"


class ActiveFaultError(Exception):
    """Another fault is active, or a crashed injector left one behind."""


def _append_event(state_directory, event):
    state_directory.mkdir(parents=True, exist_ok=True)
    with open(state_directory / RECORDS_FILE_NAME, "a", encoding="utf-8") as records_file:
        records_file.write(json.dumps(event, sort_keys=True) + "\n")


def record_injection_started(state_directory, fault_id, root_cause, started_at, changes):
    injection_id = uuid.uuid4().hex
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
    return injection_id


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


def is_process_alive(process_id):
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OverflowError:
        return False
    return True


def read_active_fault(state_directory):
    active_path = state_directory / ACTIVE_FAULT_FILE_NAME
    if not active_path.exists():
        return None
    active_fault = json.loads(active_path.read_text())
    active_fault["owner_alive"] = is_process_alive(active_fault["process_id"])
    return active_fault


def claim_active_fault(state_directory, injection_id, fault_id, process_id):
    """Marks a fault as active. Refuses if one already is.

    A claim left by a dead process is refused as well: the crashed injector
    may have left the lab changed, and stacking another fault on top would
    make the recorded ground truth wrong. "clear" removes the stale claim.
    """
    state_directory.mkdir(parents=True, exist_ok=True)
    active_path = state_directory / ACTIVE_FAULT_FILE_NAME
    claim = {"injection_id": injection_id, "fault_id": fault_id, "process_id": process_id}
    try:
        # Exclusive create: two injectors started together cannot both win.
        with open(active_path, "x", encoding="utf-8") as active_file:
            json.dump(claim, active_file)
    except FileExistsError:
        existing = read_active_fault(state_directory)
        if existing["owner_alive"]:
            raise ActiveFaultError(
                f"fault {existing['fault_id']} is already active "
                f"(process {existing['process_id']}). Stop it or run: "
                "python -m fault_injector clear"
            ) from None
        raise ActiveFaultError(
            f"fault {existing['fault_id']} was left behind by a process that is gone. "
            "Run: python -m fault_injector clear"
        ) from None


def release_active_fault(state_directory):
    (state_directory / ACTIVE_FAULT_FILE_NAME).unlink(missing_ok=True)
