from __future__ import annotations

import csv
import datetime
from pathlib import Path

import pandas as pd


class ReportAgent:
    """Writes the final validation report to CSV and prints a terminal summary."""

    name = "ReportAgent"

    def __init__(self, output_dir: str = "output") -> None:
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(exist_ok=True)

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
                    console.print("[bold white]── AI Explanations ──[/bold white]")
                    for _, row in error_rows.iterrows():
                        console.print(f"\n[bold yellow]{row['Employee_Name']} (ID {row['Employee_ID']}):[/bold yellow]")
                        console.print(row["AI_Explanation"] or "[dim]No explanation available.[/dim]")

            console.print()
            console.print(f"[dim]Report saved to output/validation_report.csv[/dim]")

        except ImportError:
            _print_plain(df, verbose)


def _print_plain(df: pd.DataFrame, verbose: bool) -> None:
    total = len(df)
    errors = (df["Status"] == "ERROR").sum()
    print(f"\nBilling Validation Report")
    print(f"Total: {total} | OK: {total - errors} | ERROR: {errors}\n")
    for _, row in df.iterrows():
        flag_str = f" [{row['Flags']}]" if row["Flags"] else ""
        print(f"  {row['Status']:5s} | {row['Employee_Name']:8s} | Project {row['Project']}{flag_str}")
    print("\nReport saved to output/validation_report.csv")
