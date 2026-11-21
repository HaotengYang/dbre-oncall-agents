"""How the diagnosis side reaches the lab: a read-only account and Prometheus."""

import os
import pathlib
from dataclasses import dataclass, field
from typing import Optional

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class DiagnosisAccess:
    mysql_host: str
    mysql_port: int
    # None when no administrative channel is available. Diagnosis then has to
    # work from outside signals alone if the normal port refuses connections.
    mysql_admin_port: Optional[int]
    mysql_user: str
    mysql_password: str = field(repr=False)
    prometheus_url: str = ""


def _read_env_file(env_path):
    values = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            stripped_line = line.strip()
            if stripped_line and not stripped_line.startswith("#") and "=" in stripped_line:
                name, _, value = stripped_line.partition("=")
                values[name.strip()] = value.strip()
    return values


ADMIN_CHANNEL_ACCOUNT = "shop_diag"
# Same read-only grants, but no administrative privilege at all: it cannot use
# the administrative port, nor the connection MySQL holds in reserve on the
# normal port.
NO_ADMIN_CHANNEL_ACCOUNT = "shop_diag_noadmin"


def load_diagnosis_access(admin_port_available=True, environment=None):
    """Access for an installation with, or without, an administrative channel."""
    merged = _read_env_file(REPOSITORY_ROOT / ".env")
    merged.update(os.environ if environment is None else environment)
    return DiagnosisAccess(
        mysql_host="127.0.0.1",
        mysql_port=int(merged.get("MYSQL_HOST_PORT", "13306")),
        mysql_admin_port=(
            int(merged.get("MYSQL_ADMIN_HOST_PORT", "13307")) if admin_port_available else None
        ),
        mysql_user=ADMIN_CHANNEL_ACCOUNT if admin_port_available else NO_ADMIN_CHANNEL_ACCOUNT,
        mysql_password=merged["DIAG_DB_PASSWORD"],
        prometheus_url=f"http://127.0.0.1:{merged.get('PROMETHEUS_HOST_PORT', '19090')}",
    )
