"""Command line: python -m change_gate plan|apply --table T --columns a,b [--query SQL]."""

import argparse
import sys

from change_gate.allowlist import IndexRequest, RejectedChange, parse_index_statement
from change_gate.gate import GateError, apply_plan, format_plan, load_gate_access, make_plan


def main(argument_list=None):
    parser = argparse.ArgumentParser(prog="python -m change_gate",
                                     description="Rehearse, assess and apply one index change.")
    parser.add_argument("command", choices=("plan", "apply"))
    parser.add_argument("--table")
    parser.add_argument("--columns", help="comma-separated, in index order")
    parser.add_argument("--name", default=None)
    parser.add_argument("--statement", default=None,
                        help="instead of --table/--columns: one CREATE INDEX statement")
    parser.add_argument("--query", default=None, help="a query the index is meant to serve")
    arguments = parser.parse_args(argument_list)
    try:
        if arguments.statement:
            request = parse_index_statement(arguments.statement)
        else:
            if not arguments.table or not arguments.columns:
                parser.error("give --table and --columns, or --statement")
            request = IndexRequest(arguments.table, tuple(c.strip() for c in arguments.columns.split(",")),
                                   arguments.name)
        access = load_gate_access()
        plan = make_plan(access, request, arguments.query)
        print(format_plan(plan))
        if arguments.command == "plan":
            return 0
        typed = input("\nConfirmation: ").strip()
        rollback_statement = apply_plan(access, plan, typed)
        print(f"Applied. To undo: {rollback_statement}")
        return 0
    except (RejectedChange, GateError) as refused:
        print(f"refused: {refused}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
