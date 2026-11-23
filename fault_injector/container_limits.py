"""The MySQL container's CPU limit, for the one scenario that makes the whole
database slow (the Honeycomb 2018 incident replay).

The lab gives MySQL exactly one CPU (docker-compose.yml). The scenario lowers
that limit while it runs; restore_cpu_limit puts it back and is called by
every stop and by "clear", so an interrupted run cannot leave the lab slow.
"""

import pathlib
import re
import subprocess

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
BASELINE_CPUS = "1.0"
BASELINE_NANO_CPUS = 1_000_000_000
COMMAND_TIMEOUT_SECONDS = 30


def _mysql_container_id():
    completed = subprocess.run(
        ["docker", "compose", "ps", "--quiet", "mysql"], cwd=REPOSITORY_ROOT,
        capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS, check=True,
    )
    container_id = completed.stdout.strip()
    if not container_id:
        raise RuntimeError("the lab's mysql container is not running")
    return container_id


def current_nano_cpus():
    completed = subprocess.run(
        ["docker", "inspect", "--format", "{{.HostConfig.NanoCpus}}", _mysql_container_id()],
        capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS, check=True,
    )
    return int(completed.stdout.strip())


def set_cpu_limit(cpus):
    """Sets the MySQL container's CPU limit, for example "0.1"."""
    # Above 0 and at most 1 (the baseline), ASCII digits only.
    if not isinstance(cpus, str) or not re.fullmatch(r"0\.[0-9]*[1-9][0-9]*|1(\.0+)?", cpus):
        raise ValueError("cpus must be a decimal string above 0 and at most 1")
    subprocess.run(["docker", "update", "--cpus", cpus, _mysql_container_id()],
                   capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS, check=True)


def restore_cpu_limit():
    """Puts the baseline limit back if it differs. Returns True if it changed it."""
    if current_nano_cpus() == BASELINE_NANO_CPUS:
        return False
    set_cpu_limit(BASELINE_CPUS)
    return True
