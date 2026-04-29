from __future__ import annotations

import datetime
import json
from pathlib import Path

import pandas as pd

from agents.data_ingestion_agent import DataIngestionAgent
from agents.validation_agent import ValidationAgent
from agents.ai_explanation_agent import AIExplanationAgent
from agents.report_agent import ReportAgent


class Orchestrator:
    """
    Central coordinator for the billing validation pipeline.
    Delegates to worker agents sequentially; checks kill switch before each step.
    All actions are logged to output/audit.log.
    """

    def __init__(
        self,
        kill_switch_path: str = "governance/kill_switch.json",
        audit_log_path: str = "output/audit.log",
        verbose: bool = False,
    ) -> None:
        self._kill_switch = Path(kill_switch_path)
        self._audit_log = Path(audit_log_path)
        self._audit_log.parent.mkdir(exist_ok=True)
        self._verbose = verbose

    def run(
        self,
        billing_input: str,
        client: str = "teleperformance",
        use_ai: bool = True,
    ) -> Path:
        self._log("Orchestrator", "START", f"client={client} input={billing_input}")

        self._check_kill_switch()
        timesheet_df, contracts_df, billing_df = self._step(
            DataIngestionAgent(), "run", billing_input
        )

        self._check_kill_switch()
        report_df: pd.DataFrame = self._step(
            ValidationAgent(), "run", timesheet_df, contracts_df, billing_df, client
        )

        self._check_kill_switch()
        report_df = self._step(
            AIExplanationAgent(), "run", report_df, use_ai
        )

        self._check_kill_switch()
        output_path: Path = self._step(
            ReportAgent(), "run", report_df, self._verbose
        )

        self._log("Orchestrator", "COMPLETE", str(output_path))
        return output_path

    def _step(self, agent, method: str, *args):
        self._log(agent.name, "START", "")
        try:
            result = getattr(agent, method)(*args)
            self._log(agent.name, "DONE", "")
            return result
        except Exception as exc:
            self._log(agent.name, "ERROR", str(exc))
            raise

    def _check_kill_switch(self) -> None:
        if not self._kill_switch.exists():
            return
        try:
            with open(self._kill_switch) as f:
                state = json.load(f)
            if not state.get("active", True):
                self._log("Orchestrator", "HALT", "kill switch activated")
                raise SystemExit("Pipeline halted: kill switch is inactive.")
        except (json.JSONDecodeError, OSError):
            pass

    def _log(self, agent: str, event: str, detail: str) -> None:
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        line = f"{ts} | {agent:<24} | {event:<8} | {detail}"
        with open(self._audit_log, "a") as f:
            f.write(line + "\n")
        if self._verbose:
            print(f"  [{ts}] {agent} → {event}" + (f": {detail}" if detail else ""))
