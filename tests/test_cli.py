"""CLI-level tests for main.py — focuses on the deterministic-mode confirmation flow.

These tests cover the API-key force-validation behavior:
- `--no-ai` skips the prompt entirely (explicit deterministic mode).
- Non-TTY context (CI / piped input) skips the prompt and runs deterministically
  with a stderr warning.
- `--yes` skips the prompt non-interactively.

We exercise the CLI as a subprocess so we hit the real argparse path.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _run_cli(args: list[str], extra_env: dict | None = None, stdin: str | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in {"ANTHROPIC_API_KEY", "OPENAI_API_KEY"}}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "main.py", *args],
        cwd=PROJECT_ROOT,
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
    )


class TestApiKeyConfirmation:
    def test_no_ai_flag_skips_prompt_entirely(self):
        """When --no-ai is passed, the deterministic-mode prompt should not appear."""
        result = _run_cli(["--no-ai"])

        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "No API key found for provider" not in result.stderr
        assert "Continue in deterministic mode" not in result.stderr

    def test_no_key_non_tty_proceeds_with_warning(self):
        """In a non-TTY context (piped stdin), the CLI should warn but proceed."""
        result = _run_cli([], stdin="")  # piped stdin → not a TTY

        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "No API key found for provider" in result.stderr
        assert "DETERMINISTIC" in result.stderr
        assert "Non-interactive context" in result.stderr
        # The pipeline still wrote the report
        assert (PROJECT_ROOT / "data" / "output" / "validation_report.csv").exists()

    def test_yes_flag_skips_prompt_with_warning(self):
        """--yes should accept the deterministic fallback without prompting."""
        result = _run_cli(["--yes"])

        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "No API key found for provider" in result.stderr
        # No prompt question asked of the user
        assert "[y/N]" not in result.stderr

    def test_with_api_key_no_prompt(self, monkeypatch):
        """When an API key is configured, no prompt and no warning."""
        # We don't actually call the API — the explainer's deterministic fallback
        # kicks in on any provider error. We just need the env var to be present
        # so the prompt is not triggered at the CLI layer.
        result = _run_cli([], extra_env={"ANTHROPIC_API_KEY": "sk-ant-test-not-real"})

        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "No API key found for provider" not in result.stderr

    def test_openai_provider_checks_openai_key(self):
        """When --ai-provider openai is selected, the OPENAI_API_KEY env is checked."""
        # No keys set, openai provider, --no-ai not passed, non-TTY → should warn about openai.
        result = _run_cli(["--ai-provider", "openai"], stdin="")

        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "provider: openai" in result.stderr
