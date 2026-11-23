"""CPU-limit input checks, clear without docker, and a failing fault stop."""

import pytest

from fault_injector import container_limits, engine, records


@pytest.mark.parametrize("cpus", ["0", "0.0", "1.5", "2", "-1", "0.05 ", "٠.٥", "1e-1", "", ".5"])
def test_set_cpu_limit_rejects_anything_but_a_value_above_zero_and_at_most_one(cpus, monkeypatch):
    def fail_if_called(*arguments, **keyword_arguments):
        raise AssertionError("docker must not be called for a rejected value")
    monkeypatch.setattr(container_limits.subprocess, "run", fail_if_called)
    with pytest.raises(ValueError):
        container_limits.set_cpu_limit(cpus)


@pytest.mark.parametrize("cpus", ["0.05", "0.5", "1", "1.0"])
def test_set_cpu_limit_accepts_plain_values_and_runs_docker_update(cpus, monkeypatch):
    commands = []
    monkeypatch.setattr(container_limits, "_mysql_container_id", lambda: "container")
    monkeypatch.setattr(container_limits.subprocess, "run",
                        lambda command, **keyword_arguments: commands.append(command))
    container_limits.set_cpu_limit(cpus)
    assert commands == [["docker", "update", "--cpus", cpus, "container"]]


def test_clear_still_restores_indexes_and_removes_the_claim_when_docker_is_unreachable(
    tmp_path, monkeypatch
):
    def docker_missing():
        raise FileNotFoundError("docker")
    restored = []
    monkeypatch.setattr(engine.container_limits, "restore_cpu_limit", docker_missing)
    monkeypatch.setattr(engine, "load_lab_settings", lambda: None)
    monkeypatch.setattr(engine, "restore_baseline_on_fresh_connection",
                        lambda settings: restored.append("restore") or [])
    records.write_claim(tmp_path, "injection-1", "incident.example", 999999)

    actions = engine.clear_lab(state_directory=tmp_path)

    assert restored == ["restore"]
    assert records.read_claim(tmp_path) is None
    assert any("could not check the MySQL container's CPU limit" in action for action in actions)


class _FaultWhoseStopFails:
    fault_id = "incident.example"
    root_cause = "connection_exhaustion"
    expected_symptoms = ()
    planned_changes = ()

    def start(self, lab_access):
        pass

    def stop(self, lab_access):
        raise RuntimeError("could not restore the CPU limit")


def test_a_failing_fault_stop_keeps_the_claim_so_the_next_inject_is_refused(tmp_path, monkeypatch):
    class _Connection:
        def close(self):
            pass
    monkeypatch.setattr(engine, "find_fault", lambda fault_id: _FaultWhoseStopFails())
    monkeypatch.setattr(engine, "open_guarded_control_connection", lambda settings: _Connection())
    monkeypatch.setattr(engine, "LabAccess", lambda settings, connection: None)
    monkeypatch.setattr(engine, "restore_baseline_on_fresh_connection", lambda settings: [])

    with pytest.raises(RuntimeError):
        with engine.injected_fault("incident.example", settings=object(), state_directory=tmp_path):
            pass

    assert records.read_claim(tmp_path)["fault_id"] == "incident.example"
    with pytest.raises(records.ActiveFaultError):
        with engine.injected_fault("incident.example", settings=object(), state_directory=tmp_path):
            pass
