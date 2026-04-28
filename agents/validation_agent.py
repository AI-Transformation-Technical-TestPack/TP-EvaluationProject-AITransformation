from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


ClientRules = dict[str, Any]

DEFAULT_RULES: ClientRules = {
    "allow_rate_tolerance": 0,
    "max_hours_enforcement": True,
    "overbilling_threshold": 0,
    "underbilling_threshold": 0,
}


class ValidationAgent:
    """Detects billing discrepancies using contract rules.

    Raised flags
    ------------
    - RATE_MISMATCH        rate charged differs from the contracted rate
    - OVERBILLING          hours billed exceed hours actually worked
    - UNDERBILLING         hours billed are below hours actually worked
    - CONTRACT_VIOLATION   hours worked exceed the contractual weekly cap
    - BILLING_OVER_MAX     hours billed exceed the contractual cap (even if worked is within cap)
    - GHOST_BILLING        billing row has no matching timesheet entry
    - MISSING_BILLING      timesheet row has no corresponding billing entry
    - MISSING_CONTRACT     project in billing/timesheet has no contract record
    - DUPLICATE_RECORD     multiple billing rows for the same Employee_ID + Project
    """

    name = "ValidationAgent"

    def __init__(self, rules_path: str = "config/client_rules.json") -> None:
        self._rules_path = Path(rules_path)

    def run(
        self,
        timesheet_df: pd.DataFrame,
        contracts_df: pd.DataFrame,
        billing_df: pd.DataFrame,
        client: str = "teleperformance",
    ) -> pd.DataFrame:
        rules = self._load_rules(client)
        return self._validate(timesheet_df, contracts_df, billing_df, rules)

    def _load_rules(self, client: str) -> ClientRules:
        if not self._rules_path.exists():
            return DEFAULT_RULES
        with open(self._rules_path) as f:
            all_rules: dict = json.load(f)
        return all_rules.get(client, DEFAULT_RULES)

    def _validate(
        self,
        timesheet_df: pd.DataFrame,
        contracts_df: pd.DataFrame,
        billing_df: pd.DataFrame,
        rules: ClientRules,
    ) -> pd.DataFrame:
        rate_tol: float = rules.get("allow_rate_tolerance", 0)
        max_hours_enforced: bool = rules.get("max_hours_enforcement", True)
        overbilling_threshold: float = rules.get("overbilling_threshold", 0)
        underbilling_threshold: float = rules.get(
            "underbilling_threshold", overbilling_threshold
        )

        duplicate_keys = self._duplicate_keys(billing_df)
        billing_keys = set(zip(billing_df["Employee_ID"], billing_df["Project"]))

        merged = (
            billing_df
            .merge(timesheet_df, on=["Employee_ID", "Project"], how="left")
            .merge(contracts_df, on="Project", how="left")
        )

        rows: list[dict] = []
        for _, row in merged.iterrows():
            rows.append(
                self._validate_billing_row(
                    row,
                    duplicate_keys=duplicate_keys,
                    rate_tol=rate_tol,
                    max_hours_enforced=max_hours_enforced,
                    overbilling_threshold=overbilling_threshold,
                    underbilling_threshold=underbilling_threshold,
                )
            )

        # Surface timesheet rows with no corresponding billing entry.
        for _, ts_row in timesheet_df.iterrows():
            key = (ts_row["Employee_ID"], ts_row["Project"])
            if key in billing_keys:
                continue
            rows.append(
                self._build_missing_billing_row(
                    ts_row, contracts_df, max_hours_enforced
                )
            )

        return pd.DataFrame(rows)

    @staticmethod
    def _duplicate_keys(billing_df: pd.DataFrame) -> set[tuple]:
        dup_mask = billing_df.duplicated(
            subset=["Employee_ID", "Project"], keep=False
        )
        if not dup_mask.any():
            return set()
        return set(
            tuple(k)
            for k in billing_df.loc[dup_mask, ["Employee_ID", "Project"]].values
        )

    def _validate_billing_row(
        self,
        row: pd.Series,
        *,
        duplicate_keys: set[tuple],
        rate_tol: float,
        max_hours_enforced: bool,
        overbilling_threshold: float,
        underbilling_threshold: float,
    ) -> dict:
        flags: list[str] = []

        emp_id = int(row["Employee_ID"])
        project = row["Project"]
        hours_billed = row["Hours_Billed"]
        rate_charged = row["Rate_Charged"]

        hours_worked = row.get("Hours_Worked")
        emp_name = row.get("Employee_Name")
        if pd.isna(emp_name):
            emp_name = ""
        contract_rate = row.get("Rate_per_Hour")
        max_hours = row.get("Max_Hours_Per_Week")

        if (emp_id, project) in duplicate_keys:
            flags.append("DUPLICATE_RECORD")

        if pd.isna(hours_worked):
            flags.append("GHOST_BILLING")

        if pd.isna(contract_rate):
            flags.append("MISSING_CONTRACT")

        if pd.notna(contract_rate) and abs(rate_charged - contract_rate) > rate_tol:
            flags.append("RATE_MISMATCH")

        if pd.notna(hours_worked):
            hour_delta = hours_billed - hours_worked
            if hour_delta > overbilling_threshold:
                flags.append("OVERBILLING")
            elif hour_delta < -underbilling_threshold:
                flags.append("UNDERBILLING")

            if (
                max_hours_enforced
                and pd.notna(max_hours)
                and hours_worked > max_hours
            ):
                flags.append("CONTRACT_VIOLATION")

        if (
            max_hours_enforced
            and pd.notna(max_hours)
            and hours_billed > max_hours
        ):
            flags.append("BILLING_OVER_MAX")

        billed_amount = round(hours_billed * rate_charged, 2)

        if pd.notna(hours_worked) and pd.notna(contract_rate):
            expected_amount = round(hours_worked * contract_rate, 2)
            difference = round(billed_amount - expected_amount, 2)
        else:
            expected_amount = None
            difference = None

        if (
            pd.notna(hours_worked)
            and pd.notna(contract_rate)
            and pd.notna(max_hours)
        ):
            capped_expected = round(min(hours_worked, max_hours) * contract_rate, 2)
            over_cap_exposure = round(billed_amount - capped_expected, 2)
        else:
            capped_expected = None
            over_cap_exposure = None

        return {
            "Employee_ID": emp_id,
            "Employee_Name": emp_name,
            "Project": project,
            "Hours_Worked": hours_worked,
            "Hours_Billed": hours_billed,
            "Rate_Charged": rate_charged,
            "Contract_Rate": contract_rate,
            "Max_Hours": max_hours,
            "Status": "ERROR" if flags else "OK",
            "Flags": ", ".join(flags),
            "Expected_Amount": expected_amount,
            "Billed_Amount": billed_amount,
            "Difference": difference,
            "Capped_Expected_Amount": capped_expected,
            "Over_Cap_Exposure": over_cap_exposure,
            "AI_Explanation": "",
        }

    @staticmethod
    def _build_missing_billing_row(
        ts_row: pd.Series,
        contracts_df: pd.DataFrame,
        max_hours_enforced: bool,
    ) -> dict:
        flags = ["MISSING_BILLING"]

        contract = contracts_df[contracts_df["Project"] == ts_row["Project"]]
        if contract.empty:
            flags.append("MISSING_CONTRACT")
            contract_rate = None
            max_hours = None
        else:
            contract_rate = contract.iloc[0]["Rate_per_Hour"]
            max_hours = contract.iloc[0]["Max_Hours_Per_Week"]

        if (
            max_hours_enforced
            and max_hours is not None
            and ts_row["Hours_Worked"] > max_hours
        ):
            flags.append("CONTRACT_VIOLATION")

        if contract_rate is not None:
            expected = round(ts_row["Hours_Worked"] * contract_rate, 2)
            capped_expected = (
                round(min(ts_row["Hours_Worked"], max_hours) * contract_rate, 2)
                if max_hours is not None
                else None
            )
        else:
            expected = None
            capped_expected = None

        return {
            "Employee_ID": int(ts_row["Employee_ID"]),
            "Employee_Name": ts_row["Employee_Name"],
            "Project": ts_row["Project"],
            "Hours_Worked": ts_row["Hours_Worked"],
            "Hours_Billed": 0,
            "Rate_Charged": None,
            "Contract_Rate": contract_rate,
            "Max_Hours": max_hours,
            "Status": "ERROR",
            "Flags": ", ".join(flags),
            "Expected_Amount": expected,
            "Billed_Amount": 0,
            "Difference": -expected if expected is not None else None,
            "Capped_Expected_Amount": capped_expected,
            "Over_Cap_Exposure": (
                -capped_expected if capped_expected is not None else None
            ),
            "AI_Explanation": "",
        }
