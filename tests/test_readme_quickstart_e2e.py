"""End-to-end coverage for the README quick start workflow."""
from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


EXPECTED_COLUMNS = [
    "Employee_ID",
    "Employee_Name",
    "Project",
    "Hours_Worked",
    "Hours_Billed",
    "Rate_Charged",
    "Contract_Rate",
    "Max_Hours",
    "Status",
    "Flags",
    "Expected_Amount",
    "Billed_Amount",
    "Difference",
    "Capped_Expected_Amount",
    "Over_Cap_Exposure",
    "AI_Explanation",
]

EXPECTED_ROWS = {
    "101": {"status": "OK", "flags": set(), "difference": 0.0},
    "102": {"status": "ERROR", "flags": {"RATE_MISMATCH", "OVERBILLING"}, "difference": 120.0},
    "103": {"status": "ERROR", "flags": {"OVERBILLING", "CONTRACT_VIOLATION", "BILLING_OVER_MAX"}, "difference": 125.0},
    "104": {"status": "ERROR", "flags": {"CONTRACT_VIOLATION", "BILLING_OVER_MAX"}, "difference": 0.0},
    "105": {"status": "ERROR", "flags": {"RATE_MISMATCH", "CONTRACT_VIOLATION", "BILLING_OVER_MAX"}, "difference": -72.0},
}

REQUIRED_EXPLANATION_FIELDS = {
    "schema_version",
    "record",
    "status",
    "flags",
    "explanation",
    "corrective_action",
    "financial_deviation",
    "human_review",
    "metadata",
}


def test_readme_quickstart_runs_successfully_from_fresh_copy(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    fresh_repo = tmp_path / "EvaluationProject"
    execution_report: dict[str, object] = {"commands": []}

    shutil.copytree(
        repo_root,
        fresh_repo,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            "__pycache__",
            ".pytest_cache",
            ".coverage",
            "*.pyc",
            "data/output/validation_report.csv",
            "data/output/audit.log",
            "data/output/e2e_quickstart_report.json",
            "PROYECTO_TELEPERFORMANCE",
            "PROYECTO TELEPERFORMANCE",
        ),
    )

    venv_dir = fresh_repo / ".venv"
    _run([sys.executable, "-m", "venv", str(venv_dir)], fresh_repo, execution_report)
    python_bin = _venv_python(venv_dir)

    _run([str(python_bin), "-m", "pip", "install", "-r", "requirements.txt"], fresh_repo, execution_report, timeout=240)
    shutil.copyfile(fresh_repo / ".env.example", fresh_repo / ".env")

    env = os.environ.copy()
    env.update({
        "AI_PROVIDER": "anthropic",
        "ANTHROPIC_API_KEY": "",
        "OPENAI_API_KEY": "",
    })
    # Match the simplified Quick Start in README.md exactly: `python main.py --verbose`.
    # If the README's Quick Start drifts, this test should fail.
    _run(
        [str(python_bin), "main.py", "--verbose"],
        fresh_repo,
        execution_report,
        env=env,
    )

    report_path = fresh_repo / "data" / "output" / "validation_report.csv"
    audit_path = fresh_repo / "data" / "output" / "audit.log"
    assert report_path.exists()
    assert audit_path.exists()

    rows = _read_report(report_path)
    _assert_report_matches_requirements(rows)
    _assert_audit_log_covers_pipeline(audit_path)

    execution_report["result"] = {
        "report_path": str(report_path.relative_to(fresh_repo)),
        "audit_log_path": str(audit_path.relative_to(fresh_repo)),
        "row_count": len(rows),
        "status_counts": _status_counts(rows),
    }
    e2e_report_path = fresh_repo / "data" / "output" / "e2e_quickstart_report.json"
    e2e_report_path.write_text(json.dumps(execution_report, indent=2), encoding="utf-8")
    assert e2e_report_path.exists()


def test_documented_cli_commands_are_runnable_for_normal_user():
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.update({
        "AI_PROVIDER": "anthropic",
        "ANTHROPIC_API_KEY": "",
        "OPENAI_API_KEY": "",
    })
    execution_report: dict[str, object] = {"commands": []}

    _run(
        [sys.executable, "main.py", "--mode", "orchestrated", "--input", "data/input/billing.csv", "--verbose"],
        repo_root,
        execution_report,
        env=env,
    )
    _assert_report_matches_requirements(_read_report(repo_root / "data" / "output" / "validation_report.csv"))

    _run(
        [sys.executable, "main.py", "--ai-provider", "openai", "--input", "data/input/billing.csv"],
        repo_root,
        execution_report,
        env=env,
    )
    _assert_report_matches_requirements(_read_report(repo_root / "data" / "output" / "validation_report.csv"))

    _run(
        [sys.executable, "main.py", "--no-ai", "--input", "data/input/billing.csv"],
        repo_root,
        execution_report,
        env=env,
    )
    _assert_report_matches_requirements(_read_report(repo_root / "data" / "output" / "validation_report.csv"))

    _run(
        [sys.executable, "main.py", "--client", "client_b", "--input", "data/input/billing.csv"],
        repo_root,
        execution_report,
        env=env,
    )
    client_b_rows = _read_report(repo_root / "data" / "output" / "validation_report.csv")
    assert _status_counts(client_b_rows) == {"OK": 3, "ERROR": 2}

    interactive_quit = _run(
        [sys.executable, "main.py", "--interactive"],
        repo_root,
        execution_report,
        env=env,
        input_text="Q\n",
    )
    assert "BILLING VALIDATION AGENT SYSTEM" in interactive_quit.stdout
    assert "Goodbye." in interactive_quit.stdout


def _run(
    command: list[str],
    cwd: Path,
    execution_report: dict[str, object],
    *,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    execution_report["commands"].append({
        "command": command,
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-2000:],
        "stderr_tail": completed.stderr[-2000:],
    })
    assert completed.returncode == 0, completed.stderr or completed.stdout
    return completed


def _venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _read_report(report_path: Path) -> list[dict[str, str]]:
    with report_path.open(newline="", encoding="utf-8") as report_file:
        reader = csv.DictReader(report_file)
        assert reader.fieldnames == EXPECTED_COLUMNS
        return list(reader)


def _assert_report_matches_requirements(rows: list[dict[str, str]]) -> None:
    assert len(rows) == 5
    assert _status_counts(rows) == {"OK": 1, "ERROR": 4}

    for row in rows:
        employee_id = row["Employee_ID"]
        expected = EXPECTED_ROWS[employee_id]
        flags = {flag.strip() for flag in row["Flags"].split(",") if flag.strip()}

        assert row["Status"] == expected["status"]
        assert flags == expected["flags"]
        assert float(row["Difference"]) == expected["difference"]

        if row["Status"] == "ERROR":
            explanation = json.loads(row["AI_Explanation"])
            assert REQUIRED_EXPLANATION_FIELDS <= explanation.keys()
            assert explanation["schema_version"] == "1.0"
            assert explanation["status"] == "ERROR"
            assert explanation["human_review"]["required"] is True
            assert explanation["financial_deviation"]["difference"] == expected["difference"]
        else:
            assert row["AI_Explanation"] == ""


def _assert_audit_log_covers_pipeline(audit_path: Path) -> None:
    audit_text = audit_path.read_text(encoding="utf-8")
    for expected_entry in [
        "Orchestrator",
        "DataIngestionAgent",
        "ValidationAgent",
        "AIExplanationAgent",
        "ReportAgent",
        "COMPLETE | data/output/validation_report.csv",
    ]:
        assert expected_entry in audit_text


def _status_counts(rows: list[dict[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["Status"]] = counts.get(row["Status"], 0) + 1
    return counts
