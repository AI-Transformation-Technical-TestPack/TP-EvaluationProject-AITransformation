from __future__ import annotations

import csv
import datetime
from pathlib import Path

import pandas as pd


class ReportAgent:
    """Writes the final validation report to CSV and prints a terminal summary."""

    name = "ReportAgent"

    def __init__(self, output_dir: str = "data/output") -> None:
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def run(self, report_df: pd.DataFrame, verbose: bool = False) -> Path:
        output_path = self._output_dir / "validation_report.csv"
        report_df.to_csv(output_path, index=False)

        self._print_summary(report_df, verbose)
        return output_path

    def _print_summary(self, df: pd.DataFrame, verbose: bool) -> None:
        try:
            from rich.console import Console
            from rich.table import Table
            from rich import box

            console = Console()
            total = len(df)
            errors = (df["Status"] == "ERROR").sum()
            ok = total - errors

            console.print()
            console.print("[bold white]━━━ Billing Validation Report ━━━[/bold white]")
            console.print(f"  Total records : [bold]{total}[/bold]")
            console.print(f"  Passed (OK)   : [bold green]{ok}[/bold green]")
            console.print(f"  Failed (ERROR): [bold red]{errors}[/bold red]")
            console.print()

            table = Table(box=box.SIMPLE_HEAD, show_footer=False)
            table.add_column("ID",       style="dim",  no_wrap=True)
            table.add_column("Name",     style="white")
            table.add_column("Project",  style="cyan",  no_wrap=True)
            table.add_column("Status",   no_wrap=True)
            table.add_column("Flags",    style="yellow")
            table.add_column("Diff $",   justify="right")

            for _, row in df.iterrows():
                status_str = (
                    "[bold green]OK[/bold green]"
                    if row["Status"] == "OK"
                    else "[bold red]ERROR[/bold red]"
                )
                diff = row["Difference"]
                diff_str = f"[red]+{diff:.2f}[/red]" if diff > 0 else (
                    f"[yellow]{diff:.2f}[/yellow]" if diff < 0 else "[green]0.00[/green]"
                )
                table.add_row(
                    str(row["Employee_ID"]),
                    row["Employee_Name"],
                    row["Project"],
                    status_str,
                    row["Flags"] or "—",
                    diff_str,
                )

            console.print(table)

            if verbose:
                error_rows = df[df["Status"] == "ERROR"]
                if not error_rows.empty:
                    console.print()
                    console.print("[bold white]── Findings ──[/bold white]")
                    for _, row in error_rows.iterrows():
                        _print_finding(console, row)

            console.print()
            console.print("[dim]Full structured JSON contract for each ERROR row is in"
                          " data/output/validation_report.csv → AI_Explanation column.[/dim]")
            console.print("[dim]Per-agent audit trail: data/output/audit.log[/dim]")

        except ImportError:
            _print_plain(df, verbose)


def _print_finding(console, row) -> None:
    """Formatted, recruiter-readable per-error summary (no raw JSON)."""
    import json as _json

    risk_color = {"high": "red", "medium": "yellow", "low": "blue"}
    diff = row["Difference"] if row["Difference"] is not None else 0.0

    try:
        payload = _json.loads(row["AI_Explanation"])
    except (TypeError, ValueError, _json.JSONDecodeError):
        payload = {}

    risk = payload.get("risk_score", "—")
    explanation = payload.get("explanation", "(no explanation)")
    action = payload.get("corrective_action", "")
    fin = payload.get("financial_deviation", {}) or {}
    direction = fin.get("direction", "")
    capped_exposure_raw = fin.get("over_cap_exposure")
    try:
        capped_exposure = float(capped_exposure_raw) if capped_exposure_raw is not None else None
    except (TypeError, ValueError):
        capped_exposure = None
    mode = payload.get("metadata", {}).get("generation_mode", "?")

    diff_str = f"+${diff:,.2f}" if diff > 0 else (f"-${abs(diff):,.2f}" if diff < 0 else "$0.00")

    console.print()
    console.print(
        f"[bold red]✗ {row['Employee_Name']} (ID {int(row['Employee_ID'])})[/bold red] "
        f"· risk: [bold {risk_color.get(risk, 'white')}]{risk}[/bold {risk_color.get(risk, 'white')}] "
        f"· loose Δ: [bold]{diff_str}[/bold] {f'({direction})' if direction else ''}"
    )
    if capped_exposure is not None and abs(capped_exposure) > 0.01:
        cap_str = f"+${capped_exposure:,.2f}" if capped_exposure > 0 else f"-${abs(capped_exposure):,.2f}"
        console.print(f"    strict-cap exposure: [bold]{cap_str}[/bold]")
    console.print(f"    flags: [yellow]{row['Flags']}[/yellow]")
    console.print(f"    [white]what happened:[/white] {explanation}")
    if action:
        console.print(f"    [white]action:[/white] {action}")
    console.print(f"    [dim]source: {mode}[/dim]")


def _print_plain(df: pd.DataFrame, verbose: bool) -> None:
    total = len(df)
    errors = (df["Status"] == "ERROR").sum()
    print(f"\nBilling Validation Report")
    print(f"Total: {total} | OK: {total - errors} | ERROR: {errors}\n")
    for _, row in df.iterrows():
        flag_str = f" [{row['Flags']}]" if row["Flags"] else ""
        print(f"  {row['Status']:5s} | {row['Employee_Name']:8s} | Project {row['Project']}{flag_str}")
    print("\nFull JSON contract per ERROR row: data/output/validation_report.csv → AI_Explanation column.")
