from __future__ import annotations

import os
from pathlib import Path

import pandas as pd


REQUIRED_COLUMNS = {
    "timesheet": {"Employee_ID", "Employee_Name", "Project", "Hours_Worked"},
    "contracts": {"Project", "Rate_per_Hour", "Max_Hours_Per_Week"},
    "billing": {"Employee_ID", "Project", "Hours_Billed", "Rate_Charged"},
}


class DataIngestionAgent:
    """Loads, validates, and normalizes the three input CSVs."""

    name = "DataIngestionAgent"

    def run(self, billing_path: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        billing_path = Path(billing_path)
        data_dir = billing_path.parent

        timesheet_path = data_dir / "timesheet.csv"
        contracts_path = data_dir / "contracts.csv"

        timesheet_df = self._load(timesheet_path, "timesheet")
        contracts_df = self._load(contracts_path, "contracts")
        billing_df = self._load(billing_path, "billing")

        return timesheet_df, contracts_df, billing_df

    def _load(self, path: Path, kind: str) -> pd.DataFrame:
        if not path.exists():
            raise FileNotFoundError(f"[{self.name}] {kind} file not found: {path}")

        if path.suffix.lower() in {".xlsx", ".xls"}:
            df = pd.read_excel(path)
        else:
            df = pd.read_csv(path)

        df.columns = [c.strip() for c in df.columns]

        missing = REQUIRED_COLUMNS[kind] - set(df.columns)
        if missing:
            raise ValueError(
                f"[{self.name}] {kind} file is missing columns: {sorted(missing)}"
            )

        return df
