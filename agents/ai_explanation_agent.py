from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd


REQUIRED_CONTRACT_FIELDS = {
    "schema_version",
    "record",
    "status",
    "flags",
    "explanation",
    "corrective_action",
    "financial_deviation",
    "human_review",
    "confidence",
    "risk_score",
    "metadata",
}

VALID_RISK_SCORES = {"low", "medium", "high"}


def _compute_risk_score(flags: list[str], abs_difference: float) -> str:
    """Triage signal for the billing supervisor. Higher score = review first."""
    if len(flags) >= 3 or abs_difference >= 500:
        return "high"
    if len(flags) >= 2 or abs_difference >= 50:
        return "medium"
    return "low"


class AIExplanationAgent:
    """Adds structured JSON explanations to ERROR rows using a configurable AI provider."""

    name = "AIExplanationAgent"

    def __init__(self, prompt_path: str = "prompts/discrepancy_prompt.txt") -> None:
        self._prompt_path = Path(prompt_path)
        self._provider = os.getenv("AI_PROVIDER", "anthropic").strip().lower()
        self._anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self._openai_api_key = os.getenv("OPENAI_API_KEY", "")
        self._anthropic_model = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
        self._openai_model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
        self._openai_base_url = os.getenv("OPENAI_BASE_URL", "")

    def run(self, report_df: pd.DataFrame, use_ai: bool = True) -> pd.DataFrame:
        df = report_df.copy()
        for idx, row in df[df["Status"] == "ERROR"].iterrows():
            if use_ai and self._has_configured_provider():
                explanation = self._explain_with_provider(row)
            else:
                explanation = self._explain_deterministic(row)
            df.at[idx, "AI_Explanation"] = explanation
        return df

    def _has_configured_provider(self) -> bool:
        if self._provider == "anthropic":
            return bool(self._anthropic_api_key)
        if self._provider == "openai":
            return bool(self._openai_api_key)
        return False

    def _explain_with_provider(self, row: pd.Series) -> str:
        if self._provider == "anthropic":
            return self._explain_with_anthropic(row)
        if self._provider == "openai":
            return self._explain_with_openai(row)

        explanation = self._build_deterministic_contract(row)
        explanation["metadata"]["generation_mode"] = "deterministic_fallback_after_ai_error"
        explanation["metadata"]["ai_error"] = f"Unsupported AI_PROVIDER: {self._provider}"
        return self._to_json(explanation)

    def _explain_with_anthropic(self, row: pd.Series) -> str:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self._anthropic_api_key)
            prompt = self._build_prompt(row)
            message = client.messages.create(
                model=self._anthropic_model,
                max_tokens=768,
                messages=[{"role": "user", "content": prompt}],
            )
            return self._finalize_provider_response(message.content[0].text, row)
        except Exception as exc:
            explanation = self._build_deterministic_contract(row)
            explanation["metadata"]["generation_mode"] = "deterministic_fallback_after_ai_error"
            explanation["metadata"]["ai_error"] = str(exc)
            return self._to_json(explanation)

    def _explain_with_openai(self, row: pd.Series) -> str:
        try:
            from openai import OpenAI
            client_kwargs = {"api_key": self._openai_api_key}
            if self._openai_base_url:
                client_kwargs["base_url"] = self._openai_base_url
            client = OpenAI(**client_kwargs)
            prompt = self._build_prompt(row)
            response = client.responses.create(
                model=self._openai_model,
                input=prompt,
                max_output_tokens=768,
            )
            return self._finalize_provider_response(response.output_text, row)
        except Exception as exc:
            explanation = self._build_deterministic_contract(row)
            explanation["metadata"]["generation_mode"] = "deterministic_fallback_after_ai_error"
            explanation["metadata"]["ai_error"] = str(exc)
            return self._to_json(explanation)

    def _build_prompt(self, row: pd.Series) -> str:
        template = self._prompt_path.read_text()
        return template.format(
            employee_name=row["Employee_Name"],
            employee_id=row["Employee_ID"],
            project=row["Project"],
            hours_worked=row["Hours_Worked"],
            hours_billed=row["Hours_Billed"],
            rate_charged=row["Rate_Charged"],
            contract_rate=row["Contract_Rate"],
            max_hours=row["Max_Hours"],
            flags=row["Flags"],
            expected_amount=row["Expected_Amount"],
            billed_amount=row["Billed_Amount"],
            difference=row["Difference"],
            generation_mode=self._provider,
        )

    def _explain_deterministic(self, row: pd.Series) -> str:
        return self._to_json(self._build_deterministic_contract(row))

    def _finalize_provider_response(self, raw_response: str, row: pd.Series) -> str:
        try:
            payload = json.loads(raw_response)
            self._validate_contract(payload)
            payload["metadata"]["source"] = "AIExplanationAgent"
            payload["metadata"].setdefault("generation_mode", self._provider)
            return self._to_json(payload)
        except (TypeError, json.JSONDecodeError, ValueError) as exc:
            explanation = self._build_deterministic_contract(row)
            explanation["metadata"]["generation_mode"] = "deterministic_fallback_after_ai_contract_error"
            explanation["metadata"]["ai_error"] = str(exc)
            return self._to_json(explanation)

    def _validate_contract(self, payload: dict) -> None:
        if not isinstance(payload, dict):
            raise ValueError("AI provider response must be a JSON object.")

        missing = sorted(REQUIRED_CONTRACT_FIELDS - payload.keys())
        if missing:
            raise ValueError(f"AI provider response is missing required fields: {missing}")

        if not isinstance(payload["metadata"], dict):
            raise ValueError("AI provider response metadata must be a JSON object.")

        confidence = payload.get("confidence")
        if not isinstance(confidence, (int, float)) or not 0.0 <= float(confidence) <= 1.0:
            raise ValueError("AI provider response confidence must be a number in [0, 1].")

        if payload.get("risk_score") not in VALID_RISK_SCORES:
            raise ValueError(f"AI provider response risk_score must be one of {sorted(VALID_RISK_SCORES)}.")

    def _build_deterministic_contract(self, row: pd.Series) -> dict:
        flags = [f.strip() for f in row["Flags"].split(",") if f.strip()]
        parts: list[str] = []
        actions: list[str] = []

        if "OVERBILLING" in flags:
            excess = row["Hours_Billed"] - row["Hours_Worked"]
            parts.append(
                f"{row['Employee_Name']} was billed for {row['Hours_Billed']} hours "
                f"but only worked {row['Hours_Worked']} hours on Project {row['Project']} "
                f"({excess:+.0f} hours overbilled)."
            )
            actions.append(f"Reduce billed hours from {row['Hours_Billed']} to {row['Hours_Worked']}.")

        if "UNDERBILLING" in flags:
            shortfall = row["Hours_Worked"] - row["Hours_Billed"]
            parts.append(
                f"{row['Employee_Name']} worked {row['Hours_Worked']} hours but only "
                f"{row['Hours_Billed']} were invoiced ({shortfall:+.0f} hours short). "
                f"This is revenue leakage."
            )
            actions.append(f"Increase billed hours to {row['Hours_Worked']} or document why hours were dropped.")

        if "RATE_MISMATCH" in flags:
            diff = row["Rate_Charged"] - row["Contract_Rate"]
            direction = "above" if diff > 0 else "below"
            parts.append(
                f"The rate charged (${row['Rate_Charged']}/hr) is ${abs(diff):.2f} {direction} "
                f"the contracted rate (${row['Contract_Rate']}/hr) for Project {row['Project']}."
            )
            actions.append(f"Correct rate to ${row['Contract_Rate']}/hr per the contract.")

        if "CONTRACT_VIOLATION" in flags:
            excess = row["Hours_Worked"] - row["Max_Hours"]
            parts.append(
                f"{row['Employee_Name']} worked {row['Hours_Worked']} hours, exceeding the "
                f"contractual maximum of {row['Max_Hours']} hours/week for Project {row['Project']} "
                f"by {excess:.0f} hours."
            )
            actions.append("Flag for manager review; obtain written approval for overtime before billing.")

        if "BILLING_OVER_MAX" in flags:
            excess = row["Hours_Billed"] - row["Max_Hours"]
            parts.append(
                f"The invoice charges {row['Hours_Billed']} hours, "
                f"{excess:+.0f} above the contractual cap of {row['Max_Hours']} hours/week. "
                f"Under a strict-cap interpretation, only {row['Max_Hours']} hours should appear on the invoice."
            )
            actions.append(
                f"Cap billed hours at {row['Max_Hours']} or attach written client approval for over-cap billing."
            )

        if "GHOST_BILLING" in flags:
            parts.append(
                f"Billing record for Employee {int(row['Employee_ID'])} on Project {row['Project']} "
                f"has no matching timesheet entry — the work cannot be substantiated."
            )
            actions.append(
                "Confirm the work was performed (locate the missing timesheet) or remove this billing line before invoicing."
            )

        if "MISSING_BILLING" in flags:
            parts.append(
                f"{row['Employee_Name']} worked {row['Hours_Worked']} hours on Project {row['Project']} "
                f"but no invoice line exists. This is unbilled work."
            )
            actions.append("Add the missing billing line so the client is invoiced for the work performed.")

        if "MISSING_CONTRACT" in flags:
            parts.append(
                f"Project {row['Project']} has no contract record — neither the rate nor the cap can be validated."
            )
            actions.append("Locate the contract for this project before sending the invoice.")

        if "DUPLICATE_RECORD" in flags:
            parts.append(
                f"Multiple billing rows exist for Employee {int(row['Employee_ID'])} on Project {row['Project']}. "
                f"This will inflate the invoice if not consolidated."
            )
            actions.append("Deduplicate the billing entries; keep the authoritative row only.")

        difference = float(row["Difference"]) if pd.notna(row.get("Difference")) else 0.0
        if difference > 0:
            direction = "overbilled"
            business_meaning = (
                "The invoice may charge the client more than the contract and timesheet support, "
                "creating credit exposure and client trust risk."
            )
        elif difference < 0:
            direction = "underbilled"
            business_meaning = (
                "The invoice may recover less revenue than expected, creating revenue leakage "
                "and reconciliation work."
            )
        else:
            direction = "no_amount_delta"
            business_meaning = (
                "The row has a validation issue even though the calculated billed amount matches "
                "the expected amount."
            )

        return {
            "schema_version": "1.0",
            "record": {
                "employee_id": int(row["Employee_ID"]),
                "employee_name": row["Employee_Name"],
                "project": row["Project"],
            },
            "status": row["Status"],
            "flags": flags,
            "explanation": " ".join(parts),
            "corrective_action": " ".join(actions),
            "financial_deviation": {
                "expected_amount": (
                    float(row["Expected_Amount"])
                    if pd.notna(row.get("Expected_Amount"))
                    else None
                ),
                "billed_amount": (
                    float(row["Billed_Amount"])
                    if pd.notna(row.get("Billed_Amount"))
                    else None
                ),
                "difference": difference,
                "direction": direction,
                "business_meaning": business_meaning,
                "capped_expected_amount": (
                    float(row["Capped_Expected_Amount"])
                    if pd.notna(row.get("Capped_Expected_Amount"))
                    else None
                ),
                "over_cap_exposure": (
                    float(row["Over_Cap_Exposure"])
                    if pd.notna(row.get("Over_Cap_Exposure"))
                    else None
                ),
            },
            "human_review": {
                "required": True,
                "reviewer_role": "billing_supervisor",
                "reason": "AI-generated remediation recommendations are advisory and must be approved before credits or invoice adjustments.",
            },
            "confidence": 1.0,
            "risk_score": _compute_risk_score(flags, abs(difference)),
            "metadata": {
                "generation_mode": "deterministic",
                "source": "AIExplanationAgent",
            },
        }

    def _to_json(self, payload: dict) -> str:
        return json.dumps(payload, ensure_ascii=False, indent=2)
