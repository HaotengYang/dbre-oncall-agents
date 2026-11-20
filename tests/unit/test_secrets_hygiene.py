"""Completion criterion 9: secrets stay out of the repository."""

import pathlib
import subprocess

import pytest

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[2]
SECRET_VARIABLE_SUFFIXES = ("_PASSWORD", "_KEY", "_TOKEN", "_SECRET")


def read_assignments(env_path):
    assignments = {}
    for line in env_path.read_text().splitlines():
        stripped_line = line.strip()
        if stripped_line and not stripped_line.startswith("#") and "=" in stripped_line:
            name, _, value = stripped_line.partition("=")
            assignments[name.strip()] = value.strip()
    return assignments


def test_env_file_is_ignored_by_git():
    completed = subprocess.run(
        ["git", "check-ignore", "--quiet", ".env"], cwd=REPOSITORY_ROOT
    )

    assert completed.returncode == 0, ".env is not ignored by git"


def test_env_file_is_not_tracked():
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", ".env"],
        cwd=REPOSITORY_ROOT, capture_output=True,
    )

    assert tracked.returncode != 0, ".env is tracked by git"


def test_example_file_contains_only_placeholders():
    example_assignments = read_assignments(REPOSITORY_ROOT / ".env.example")

    secret_names = [
        name for name in example_assignments if name.endswith(SECRET_VARIABLE_SUFFIXES)
    ]
    assert secret_names, ".env.example should document the secret variables"
    for name in secret_names:
        assert example_assignments[name].startswith("replace-with-"), name


def test_no_real_secret_value_appears_in_any_tracked_file():
    env_path = REPOSITORY_ROOT / ".env"
    if not env_path.exists():
        pytest.skip("no .env on this machine, nothing to compare against")
    secret_values = {
        value
        for name, value in read_assignments(env_path).items()
        if name.endswith(SECRET_VARIABLE_SUFFIXES) and len(value) >= 8
    }
    assert secret_values, ".env has no secret values to check"

    tracked_and_new_files = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=REPOSITORY_ROOT, check=True, capture_output=True, text=True,
    ).stdout.splitlines()

    leaking_files = []
    for relative_path in tracked_and_new_files:
        file_path = REPOSITORY_ROOT / relative_path
        if not file_path.is_file():
            continue
        file_text = file_path.read_text(errors="ignore")
        if any(secret_value in file_text for secret_value in secret_values):
            leaking_files.append(relative_path)

    assert leaking_files == []
