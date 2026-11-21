"""Command line: python -m diagnosis "alert text" [--json] [--no-admin-port]."""

import argparse
import json
import sys

from diagnosis.access import load_diagnosis_access
from diagnosis.orchestrator import orchestrate, render_report_text, report_as_dict


def main(argument_list=None):
    parser = argparse.ArgumentParser(
        prog="python -m diagnosis", description="Diagnose the lab MySQL from an alert."
    )
    parser.add_argument("alert", help="the alert text, as an application engineer wrote it")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--no-admin-port", action="store_true",
                        help="diagnose as an installation without an administrative channel")
    arguments = parser.parse_args(argument_list)
    try:
        access = load_diagnosis_access(admin_port_available=not arguments.no_admin_port)
    except KeyError as missing_setting:
        print(f"error: {missing_setting.args[0]} is not set. Copy .env.example to .env.",
              file=sys.stderr)
        return 2
    report = orchestrate(arguments.alert, access)
    print(json.dumps(report_as_dict(report), indent=2) if arguments.json
          else render_report_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
