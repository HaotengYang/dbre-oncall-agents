"""The committed eval data is well-formed, thresholds behave, and the
diagnosis side cannot see the answers."""

import ast
import json
import pathlib
import subprocess
import sys

import pytest

from eval_runner.cases import load_cases
from eval_runner.cli import EVALS_DIRECTORY, select_cases
from eval_runner.scoring import CaseResult, classify_outcome
from eval_runner.thresholds import ThresholdError, load_thresholds, threshold_violations
from fault_injector.registry import find_fault

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[2]
ALL_CASES = load_cases(EVALS_DIRECTORY / "cases")


def test_the_case_set_is_the_approved_eighteen():
    by_tier = {tier: [c for c in ALL_CASES if c.tier == tier] for tier in ("basic", "hard")}
    kinds = [case.kind for case in ALL_CASES]

    assert len(by_tier["basic"]) == 8 and len(by_tier["hard"]) == 10
    assert kinds.count("single_fault") == 6 and kinds.count("healthy") == 2
    assert kinds.count("cascade") == 2 and kinds.count("healthy_lookalike") == 2
    assert kinds.count("incomplete_evidence") == 3 and kinds.count("hostile_input") == 1
    assert kinds.count("metrics_gap") == 2


@pytest.mark.parametrize("case", [c for c in ALL_CASES if c.scenario], ids=lambda c: c.case_id)
def test_case_agrees_with_the_scenario_it_names(case):
    scenario = find_fault(case.scenario)  # raises if the scenario does not exist

    assert scenario.root_cause == case.expected_root_cause
    assert set(scenario.expected_symptoms) == set(case.expected_symptoms)


def test_every_missing_index_case_states_the_expected_index():
    for case in ALL_CASES:
        if case.expected_root_cause == "missing_index_slow_query":
            assert case.expected_index is not None, case.case_id
        else:
            assert case.expected_index is None, case.case_id


def test_incomplete_evidence_and_the_no_admin_port_case_accept_undetermined():
    for case in ALL_CASES:
        if case.kind == "incomplete_evidence" or not case.admin_port_available:
            assert case.also_acceptable == ("undetermined",), case.case_id
        else:
            assert case.also_acceptable == (), case.case_id


def test_quick_set_names_existing_cases_and_covers_both_tiers():
    quick_cases = select_cases(ALL_CASES, "quick", None)

    assert 5 <= len(quick_cases) <= 7
    assert {case.tier for case in quick_cases} == {"basic", "hard"}
    assert any(case.kind == "healthy" for case in quick_cases)
    assert any(case.kind == "cascade" for case in quick_cases)


def test_committed_thresholds_are_the_ones_the_owner_set():
    thresholds = load_thresholds(EVALS_DIRECTORY / "thresholds.json")

    assert thresholds == {
        "full": {
            "basic": {"min_hit_rate": 1.0, "max_false_positive_rate": 0.0},
            "hard": {"min_hit_rate": 0.625, "max_false_positive_rate": 0.0},
        },
        "quick": {"max_false_positives": 0, "max_misses": 1},
    }


OWNER_THRESHOLDS = {
    "full": {
        "basic": {"min_hit_rate": 0.83, "max_false_positive_rate": 0.0},
        "hard": {"min_hit_rate": 0.625, "max_false_positive_rate": 0.0},
    },
    "quick": {"max_false_positives": 0, "max_misses": 1},
}


def scored(case_id, tier, kind, expected, answer):
    case = next(c for c in ALL_CASES if c.kind == kind and c.tier == tier)
    case = type(case)(**{**case.__dict__, "case_id": case_id, "expected_root_cause": expected,
                         "also_acceptable": ()})
    return CaseResult(case, answer, (), classify_outcome(case, answer), None, None, 1.0, None)


def basic_tier_results(hits, misses, false_positives=0):
    results = [scored(f"hit{n}", "basic", "single_fault", "lock_wait", "lock_wait")
               for n in range(hits)]
    results += [scored(f"miss{n}", "basic", "single_fault", "lock_wait", "healthy")
                for n in range(misses)]
    results += [scored(f"fp{n}", "basic", "healthy", "healthy", "lock_wait")
                for n in range(false_positives)]
    return results


def test_full_set_five_of_six_passes_and_four_of_six_fails():
    assert threshold_violations(basic_tier_results(5, 1), OWNER_THRESHOLDS, "full") == []

    violations = threshold_violations(basic_tier_results(4, 2), OWNER_THRESHOLDS, "full")

    assert len(violations) == 1 and "66.7% (4/6)" in violations[0]


def test_full_set_hard_tier_five_of_eight_passes_and_four_of_eight_fails():
    def hard_results(hits, misses):
        return (
            [scored(f"h{n}", "hard", "cascade", "lock_wait", "lock_wait") for n in range(hits)]
            + [scored(f"m{n}", "hard", "cascade", "lock_wait", "healthy") for n in range(misses)]
        )

    assert threshold_violations(hard_results(5, 3), OWNER_THRESHOLDS, "full") == []
    assert len(threshold_violations(hard_results(4, 4), OWNER_THRESHOLDS, "full")) == 1


def test_full_set_any_false_positive_fails():
    violations = threshold_violations(basic_tier_results(6, 0, false_positives=1),
                                      OWNER_THRESHOLDS, "full")

    assert len(violations) == 1 and "false-positive rate" in violations[0]


def test_quick_set_is_judged_by_counts_not_rates():
    one_miss = basic_tier_results(2, 1)   # a hit rate of 67% would fail 0.83
    two_misses = basic_tier_results(1, 2)
    one_false_positive = basic_tier_results(3, 0, false_positives=1)

    assert threshold_violations(one_miss, OWNER_THRESHOLDS, "quick") == []
    quick_violations = threshold_violations(two_misses, OWNER_THRESHOLDS, "quick")
    assert len(quick_violations) == 1 and "2 miss(es)" in quick_violations[0]
    assert "miss0" in quick_violations[0] and "miss1" in quick_violations[0]
    false_positive_violations = threshold_violations(one_false_positive, OWNER_THRESHOLDS, "quick")
    assert len(false_positive_violations) == 1 and "fp0" in false_positive_violations[0]


def test_undetermined_on_a_healthy_case_uses_the_quick_set_miss_allowance():
    healthy_case = next(c for c in ALL_CASES if c.kind == "healthy")
    undetermined = CaseResult(healthy_case, "undetermined", (),
                              classify_outcome(healthy_case, "undetermined"), None, None, 1.0, None)
    one_real_miss = basic_tier_results(2, 1)

    assert undetermined.outcome == "undetermined_on_healthy"
    # Alone it fits the allowance of one and is not a false positive.
    assert threshold_violations([undetermined], OWNER_THRESHOLDS, "quick") == []
    # With one real miss it makes two, which is over the allowance.
    violations = threshold_violations(one_real_miss + [undetermined], OWNER_THRESHOLDS, "quick")
    assert len(violations) == 1 and "2 miss(es)" in violations[0]
    assert "false positive" not in violations[0]


def test_acceptable_answers_are_not_misses_on_the_quick_set():
    case = next(c for c in ALL_CASES if c.kind == "incomplete_evidence")
    acceptable = [CaseResult(case, "undetermined", (), classify_outcome(case, "undetermined"),
                             None, None, 1.0, None)] * 3

    assert {r.outcome for r in acceptable} == {"acceptable"}
    assert threshold_violations(acceptable, OWNER_THRESHOLDS, "quick") == []


def test_invalid_cases_cannot_make_a_judged_run_pass():
    def invalid(case_id):
        case = next(c for c in ALL_CASES if c.kind == "single_fault")
        case = type(case)(**{**case.__dict__, "case_id": case_id})
        return CaseResult(case, "lock_wait", (), "invalid", None, None, 1.0,
                          "injector recorded lock_wait, case expects healthy")

    four_hits_two_invalid = basic_tier_results(4, 0) + [invalid("x1"), invalid("x2")]
    all_invalid = [invalid(f"x{n}") for n in range(6)]

    # 4/4 would pass "at least 5/6" if the invalid cases were merely dropped.
    full_violations = threshold_violations(four_hits_two_invalid, OWNER_THRESHOLDS, "full")
    assert len(full_violations) == 1 and "x1, x2" in full_violations[0]
    # Six invalid cases have no false positive and no miss.
    assert len(threshold_violations(all_invalid, OWNER_THRESHOLDS, "quick")) == 1
    # Outside the judged sets nothing is judged.
    assert threshold_violations(all_invalid, OWNER_THRESHOLDS, None) == []


def test_a_single_case_or_custom_selection_is_not_judged_against_thresholds():
    assert threshold_violations(basic_tier_results(0, 3, false_positives=2),
                                OWNER_THRESHOLDS, None) == []


def test_any_case_without_an_answer_fails_whatever_was_run():
    healthy_case = next(c for c in ALL_CASES if c.kind == "healthy")
    crashed = CaseResult(healthy_case, "(no answer)", (), "diagnosis_error", None, None, 0.1,
                         "RuntimeError: backend failed")

    # A backend that crashes on a healthy case raised no false positive, and
    # must still not pass.
    for set_name in ("full", "quick", None):
        violations = threshold_violations([crashed], OWNER_THRESHOLDS, set_name)
        assert len(violations) == 1 and "no answer" in violations[0]


@pytest.mark.parametrize(
    "content",
    ["{not json", '{"full": {}}',
     json.dumps({"full": OWNER_THRESHOLDS["full"]}),
     json.dumps({**OWNER_THRESHOLDS, "quick": {"max_false_positives": 0}}),
     json.dumps({**OWNER_THRESHOLDS, "quick": {"max_false_positives": 0.5, "max_misses": 1}}),
     json.dumps({"full": {"basic": {"min_hit_rate": 2, "max_false_positive_rate": None},
                          "hard": OWNER_THRESHOLDS["full"]["hard"]},
                 "quick": OWNER_THRESHOLDS["quick"]}),
     json.dumps({"full": {"basic": {"min_hit_rate": "high", "max_false_positive_rate": None},
                          "hard": OWNER_THRESHOLDS["full"]["hard"]},
                 "quick": OWNER_THRESHOLDS["quick"]})],
)
def test_malformed_thresholds_are_rejected(tmp_path, content):
    thresholds_path = tmp_path / "thresholds.json"
    thresholds_path.write_text(content)

    with pytest.raises(ThresholdError):
        load_thresholds(thresholds_path)


# ---- ground-truth isolation (CLAUDE.md) --------------------------------

# This scan is a tripwire, not a sandbox: it catches the ways the diagnosis side
# could plausibly come to read the answers, by accident or by a careless
# change. Code written to defeat it could.

# Modules that hold the answers, or that could be used to reach them.
FORBIDDEN_IMPORTS = ("fault_injector", "eval_runner", "evals", "importlib", "runpy",
                     "builtins", "subprocess", "glob", "shutil", "pkgutil")
FORBIDDEN_MODULES = ("fault_injector", "eval_runner", "evals")
# Names that must not appear anywhere inside a string or bytes literal.
FORBIDDEN_SUBSTRINGS = ("fault_injector", "eval_runner", "injections", "active_fault")
# Short names that must not appear as a whole path or module part.
FORBIDDEN_PARTS = ("evals", "var", "reports")
# Calls that import or run code, or that search the filesystem, however they
# are reached (bare name or attribute).
FORBIDDEN_CALLS = ("__import__", "import_module", "run_module", "run_path", "exec", "eval",
                   "spec_from_file_location", "system", "popen",
                   "rglob", "glob", "walk", "scandir", "listdir", "iterdir")
# Only as a bare name: re.compile() is a regular expression, not the builtin.
FORBIDDEN_BARE_CALLS = ("compile",)
# Attributes that expose modules already loaded by someone else.
FORBIDDEN_ATTRIBUTES = ("modules", "meta_path", "path_hooks")
DIAGNOSIS_DIRECTORY = REPOSITORY_ROOT / "diagnosis"
DIAGNOSIS_SOURCES = sorted(DIAGNOSIS_DIRECTORY.rglob("*.py"))


def isolation_violations(source_text):
    """Returns why a piece of diagnosis-side source could reach the answers."""
    tree = ast.parse(source_text)
    violations = []
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant):
                docstrings.add(id(first.value))
    for node in ast.walk(tree):
        imported = []
        if isinstance(node, ast.Import):
            imported = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported = [node.module or ""]
        for module_name in imported:
            if module_name.split(".")[0] in FORBIDDEN_IMPORTS:
                violations.append(f"imports {module_name}")
        if isinstance(node, ast.Call):
            called_name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if called_name in FORBIDDEN_CALLS:
                violations.append(f"calls {called_name}()")
            if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_BARE_CALLS:
                violations.append(f"calls {node.func.id}()")
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRIBUTES:
            violations.append(f"uses .{node.attr}")
        if isinstance(node, ast.Constant) and id(node) not in docstrings:
            literal = node.value.decode("utf-8", "replace") if isinstance(node.value, bytes) \
                else node.value
            if not isinstance(literal, str):
                continue
            for forbidden in FORBIDDEN_SUBSTRINGS:
                if forbidden in literal:
                    violations.append(f"names {forbidden!r}")
            parts = literal.replace("\\", "/").replace(".", "/").split("/")
            for forbidden in FORBIDDEN_PARTS:
                if forbidden in parts:
                    violations.append(f"names the path part {forbidden!r}")
    return violations


@pytest.mark.parametrize("source_path", DIAGNOSIS_SOURCES, ids=lambda p: p.name)
def test_diagnosis_source_cannot_reach_the_answers(source_path):
    assert isolation_violations(source_path.read_text()) == []


@pytest.mark.parametrize(
    "offending_source, expected_fragment",
    [
        ("import fault_injector.records", "imports fault_injector.records"),
        ("from eval_runner.cases import load_cases", "imports eval_runner.cases"),
        ("import importlib\nimportlib.import_module('x')", "imports importlib"),
        ("def f():\n    return __import__('fault_' + 'injector')", "calls __import__()"),
        ("def f(m):\n    return m.import_module('fault_injector.records')", "calls import_module()"),
        ("exec('import fault_injector')", "calls exec()"),
        ("code = compile('import x', 'f', 'exec')", "calls compile()"),
        ("__builtins__.exec('import fault_' + 'injector')", "calls exec()"),
        ("__builtins__.eval('1')", "calls eval()"),
        ("path = ROOT / 'evals' / 'cases'", "'evals'"),
        ("path = ROOT / 'var' / 'reports'", "'var'"),
        ("path = 'var/injections.jsonl'", "'injections'"),
        ("name = 'active_fault.json'", "'active_fault'"),
        # Found by the reviewer in round 2: each of these used to pass.
        ("import sys\nrecords = sys.modules['fault_injector.records']", ".modules"),
        ("import sys\nrecords = sys.modules['fault_injector.records']", "'fault_injector'"),
        ("import subprocess\nsubprocess.run(['python', '-m', 'fault_injector.cli'])",
         "imports subprocess"),
        ("from builtins import __import__ as load\nm = load('x')", "imports builtins"),
        ("found = [p.read_text() for p in ROOT.rglob('*.jsonl')]", "calls rglob()"),
        ("data = open(b'var/injections.jsonl').read()", "'injections'"),
        ("import os\nos.system('cat var/injections.jsonl')", "calls system()"),
        ("name = 'evals.cases'", "'evals'"),
    ],
)
def test_the_isolation_scan_catches_each_kind_of_violation(offending_source, expected_fragment):
    violations = isolation_violations(offending_source)

    assert any(expected_fragment in violation for violation in violations), violations


def test_the_isolation_scan_ignores_docstrings_and_ordinary_code():
    harmless = (
        '"""Must not read var/ or evals/ or import fault_injector."""\n'
        "import re, time\n"
        "label = 'healthy'\n"
        "pattern = re.compile('SELECT|EXECUTE')\n"
        "def f():\n"
        '    """Not allowed: evals."""\n'
        "    return time.time()\n"
    )

    assert isolation_violations(harmless) == []


def test_importing_every_diagnosis_module_loads_no_answer_holding_module():
    module_names = sorted(
        "diagnosis." + ".".join(source_path.relative_to(DIAGNOSIS_DIRECTORY).with_suffix("").parts)
        for source_path in DIAGNOSIS_SOURCES if source_path.name != "__init__.py"
    )
    assert len(module_names) >= 5  # the scan is looking at real modules
    check = (
        "import importlib, sys\n"
        f"for name in {module_names!r}:\n"
        "    importlib.import_module(name)\n"
        f"print(','.join(m for m in sys.modules if m.split('.')[0] in {FORBIDDEN_MODULES!r}))\n"
    )
    completed = subprocess.run([sys.executable, "-c", check], cwd=REPOSITORY_ROOT,
                               capture_output=True, text=True, check=True)

    assert completed.stdout.strip() == ""
