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
    All actions are logged to data/output/audit.log.
    """

    def __init__(
        self,
        kill_switch_path: str = "config/kill_switch.json",
        audit_log_path: str = "data/output/audit.log",
        verbose: bool = False,
    ) -> None:
        self._kill_switch = Path(kill_switch_path)
        self._audit_log = Path(audit_log_path)
        self._audit_log.parent.mkdir(parents=True, exist_ok=True)
        self._verbose = verbose

    def run(
        self,
        billing_input: str,
        client: str = "client_a",
        use_ai: bool = True,
    ) -> Path:
        self._log("Orchestrator", "START", f"client={client} input={billing_input}")

        # 1. Ingestion ---------------------------------------------------------
        self._check_kill_switch()
        self._log("DataIngestionAgent", "INFO", f"Reading input data from {billing_input} and the matching timesheet and contracts files in the same folder…")
        timesheet_df, contracts_df, billing_df = self._step(
            DataIngestionAgent(), "run", billing_input
        )
        self._log(
            "DataIngestionAgent", "INFO",
            f"Loaded {len(timesheet_df)} employee timesheet rows, {len(contracts_df)} project contracts, and {len(billing_df)} billing entries.",
        )

        # 2. Validation --------------------------------------------------------
        self._check_kill_switch()
        self._log(
            "ValidationAgent", "INFO",
            f"Comparing each billing entry against its timesheet and contract using the rules configured for client '{client}'…",
        )
        report_df: pd.DataFrame = self._step(
            ValidationAgent(), "run", timesheet_df, contracts_df, billing_df, client
        )
        ok_count = int((report_df["Status"] == "OK").sum())
        err_count = int((report_df["Status"] == "ERROR").sum())
        self._log(
            "ValidationAgent", "INFO",
            f"Validation complete: {len(report_df)} record{'s' if len(report_df) != 1 else ''} checked — {ok_count} passed, {err_count} flagged for review.",
        )
        if err_count:
            flag_counts: dict[str, int] = {}
            for flags in report_df["Flags"]:
                if not flags:
                    continue
                for flag in [f.strip() for f in str(flags).split(",") if f.strip()]:
                    flag_counts[flag] = flag_counts.get(flag, 0) + 1
            flag_summary = ", ".join(f"{v}× {k}" for k, v in sorted(flag_counts.items(), key=lambda kv: (-kv[1], kv[0])))
            self._log("ValidationAgent", "INFO", f"Discrepancy types found: {flag_summary}.")

        # 3. AI explanations ---------------------------------------------------
        self._check_kill_switch()
        ai_agent = AIExplanationAgent()
        if err_count:
            self._log(
                "AIExplanationAgent", "INFO",
                f"Generating plain-English explanations and corrective actions for the {err_count} flagged record{'s' if err_count != 1 else ''}.",
            )
            provider_status = ai_agent.describe_provider(use_ai)
            self._log("AIExplanationAgent", "INFO", provider_status + ".")
        report_df = self._step(ai_agent, "run", report_df, use_ai, self._verbose)
        if err_count:
            modes = self._summarise_generation_modes(report_df)
            self._log("AIExplanationAgent", "INFO", self._describe_modes(modes, err_count))

        # 4. Report ------------------------------------------------------------
        self._check_kill_switch()
        self._log("ReportAgent", "INFO", "Writing the structured CSV report and the per-agent audit log…")
        output_path: Path = self._step(ReportAgent(), "run", report_df, self._verbose)
        try:
            size_kb = output_path.stat().st_size / 1024
            self._log("ReportAgent", "INFO", f"Report saved to {output_path} ({size_kb:.1f} KB). Audit log at {self._audit_log}.")
        except OSError:
            pass

        self._log("Orchestrator", "COMPLETE", str(output_path))
        return output_path

    @staticmethod
    def _describe_modes(modes: dict[str, int], err_count: int) -> str:
        """Plain-English summary of how the explanations were generated."""
        if not modes:
            return "No explanations were generated."
        # Break out 'real' AI calls vs. fallbacks for readability
        real = {k: v for k, v in modes.items() if k in {"anthropic", "openai"}}
        fallback = {k: v for k, v in modes.items() if k not in real}
        parts: list[str] = []
        for provider, n in sorted(real.items()):
            parts.append(f"{n} from {provider}")
        for reason, n in sorted(fallback.items()):
            human = reason.replace("_", " ").replace("deterministic", "deterministic fallback")
            parts.append(f"{n} from {human}")
        if not parts:
            return f"{err_count} explanations recorded."
        if len(parts) == 1:
            return f"All {err_count} explanations generated: {parts[0]}."
        return f"{err_count} explanations generated — " + " · ".join(parts) + "."

    @staticmethod
    def _summarise_generation_modes(report_df: pd.DataFrame) -> dict[str, int]:
        modes: dict[str, int] = {}
        for raw in report_df["AI_Explanation"]:
            if not raw:
                continue
            try:
                payload = json.loads(raw)
                mode = payload.get("metadata", {}).get("generation_mode", "unknown")
            except (TypeError, ValueError, json.JSONDecodeError):
                mode = "unparsable"
            modes[mode] = modes.get(mode, 0) + 1
        return modes

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

    # Events shown on the verbose console. START / DONE / COMPLETE are mechanical
    # bookends that only clutter the surface — they still go to the audit log file
    # for forensic / replay purposes.
    _CONSOLE_EVENTS = {"INFO", "ERROR", "HALT"}

    def _log(self, agent: str, event: str, detail: str) -> None:
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        line = f"{ts} | {agent:<24} | {event:<8} | {detail}"
        with open(self._audit_log, "a") as f:
            f.write(line + "\n")
        if self._verbose and event in self._CONSOLE_EVENTS:
            arrow = "▸" if event == "INFO" else ("✗" if event == "ERROR" else "⏸")
            suffix = f": {detail}" if detail else ""
            print(f"  {arrow} {agent}{suffix}")
