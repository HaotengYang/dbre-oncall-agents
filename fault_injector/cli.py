"""Command line: python -m fault_injector <command>."""

import argparse
import signal
import sys
import threading

from fault_injector.engine import clear_lab, describe_lab_state, injected_fault
from fault_injector.lab import LabSettingsError, NotLabEnvironmentError
from fault_injector.records import ActiveFaultError
from fault_injector.registry import find_fault, list_faults

EXIT_REFUSED = 2


def build_parser():
    parser = argparse.ArgumentParser(
        prog="python -m fault_injector",
        description="Break the lab MySQL on purpose, and put it back.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    inject = commands.add_parser(
        "inject", help="activate a fault until Ctrl-C or until --duration has passed"
    )
    inject.add_argument("fault_id")
    inject.add_argument("--duration", type=float, default=None,
                        help="seconds to keep the fault active (default: until Ctrl-C)")

    commands.add_parser("clear", help="return the lab to its baseline")
    commands.add_parser("status", help="show the active fault and schema differences")
    commands.add_parser("list", help="list available faults")
    return parser


def run_inject(arguments):
    find_fault(arguments.fault_id)  # fail on an unknown id before touching the lab
    stop_requested = threading.Event()
    for stop_signal in (signal.SIGINT, signal.SIGTERM):
        signal.signal(stop_signal, lambda signal_number, frame: stop_requested.set())

    with injected_fault(arguments.fault_id) as injection:
        print(f"injected {injection['fault_id']} at {injection['started_at']}", flush=True)
        for change in injection["changes"]:
            print(f"  {change}", flush=True)
        print("fault is active; press Ctrl-C to stop and restore", flush=True)
        stop_requested.wait(arguments.duration)
    print("fault stopped, baseline restored", flush=True)
    return 0


def run_clear(arguments):
    actions = clear_lab()
    for action in actions:
        print(action)
    print("lab is at baseline" if actions else "lab was already at baseline")
    return 0


def run_status(arguments):
    state = describe_lab_state()
    active_fault = state["active_fault"]
    if active_fault is None:
        print("active fault: none")
    else:
        owner = "running" if active_fault["owner_alive"] else "process gone, run clear"
        print(f"active fault: {active_fault['fault_id']} ({owner})")
    print(f"missing baseline indexes: {', '.join(state['missing_indexes']) or 'none'}")
    print(f"unexpected indexes: {', '.join(state['unexpected_indexes']) or 'none'}")
    return 0


def run_list(arguments):
    for fault in list_faults():
        print(f"{fault.fault_id}\n    label: {fault.root_cause}\n    {fault.summary}")
    return 0


def main(argument_list=None):
    arguments = build_parser().parse_args(argument_list)
    handlers = {"inject": run_inject, "clear": run_clear, "status": run_status, "list": run_list}
    try:
        return handlers[arguments.command](arguments)
    except (ActiveFaultError, NotLabEnvironmentError, LabSettingsError) as refusal:
        print(f"refused: {refusal}", file=sys.stderr)
        return EXIT_REFUSED
    except KeyError as unknown_fault:
        print(f"refused: {unknown_fault.args[0]}", file=sys.stderr)
        return EXIT_REFUSED
