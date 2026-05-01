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

    def run(
        self,
        report_df: pd.DataFrame,
        use_ai: bool = True,
        verbose: bool = False,
    ) -> pd.DataFrame:
        df = report_df.copy()
        error_rows = df[df["Status"] == "ERROR"]
        for idx, row in error_rows.iterrows():
            label = f"{row['Employee_Name']} (ID {int(row['Employee_ID'])})"
            if verbose:
                print(f"    · {label}: generating explanation…", end="", flush=True)
            if use_ai and self._has_configured_provider():
                explanation = self._explain_with_provider(row)
            else:
                explanation = self._explain_deterministic(row)
            df.at[idx, "AI_Explanation"] = explanation
            if verbose:
                try:
                    mode = json.loads(explanation).get("metadata", {}).get("generation_mode", "?")
                except (TypeError, ValueError, json.JSONDecodeError):
                    mode = "?"
                friendly = {
                    "anthropic": "explained by Anthropic Claude",
                    "openai": "explained by OpenAI-compatible provider",
                    "deterministic": "explained by the rule-based fallback",
                }.get(mode, f"explained ({mode})")
                print(f" {friendly}", flush=True)
        return df

    def describe_provider(self, use_ai: bool) -> str:
        """Plain-English description of the active provider for verbose logs."""
        if not use_ai:
            return "AI calls were disabled (--no-ai), so the deterministic rule-based explainer is being used instead"
        if not self._has_configured_provider():
            return (
                f"No API key was found for provider '{self._provider}', so the deterministic "
                "rule-based explainer is being used instead"
            )
        if self._provider == "anthropic":
            return f"Calling Anthropic's Claude API (model: {self._anthropic_model})"
        if self._provider == "openai":
            base = self._openai_base_url or "https://api.openai.com/v1"
            return f"Calling the OpenAI-compatible Chat Completions endpoint at {base} (model: {self._openai_model})"
        return f"Using provider '{self._provider}'"

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
        """Uses the Chat Completions endpoint for broad compatibility.

        Most OpenAI-compatible providers (DeepSeek, Groq, Together, OpenRouter,
        vLLM, etc.) implement /v1/chat/completions but not the newer
        /v1/responses surface. Sticking to chat.completions keeps the explainer
        portable.
        """
        try:
            from openai import OpenAI
            client_kwargs = {"api_key": self._openai_api_key}
            if self._openai_base_url:
                client_kwargs["base_url"] = self._openai_base_url
            client = OpenAI(**client_kwargs)
            prompt = self._build_prompt(row)
            response = client.chat.completions.create(
                model=self._openai_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=768,
            )
            return self._finalize_provider_response(response.choices[0].message.content, row)
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
        """Take the AI's two-field prose response and merge it into the
        deterministic contract. The LLM never produces numbers, flags, risk
        scores, or any structural field — those are computed by
        ``_build_deterministic_contract``. This guarantees type-safety of every
        downstream consumer regardless of LLM behaviour.
        """
        try:
            ai_explanation, ai_action = self._extract_ai_prose(raw_response)
            contract = self._build_deterministic_contract(row)
            contract["explanation"] = ai_explanation
            contract["corrective_action"] = ai_action
            contract["metadata"]["generation_mode"] = self._provider
            contract["metadata"]["source"] = "AIExplanationAgent"
            return self._to_json(contract)
        except (TypeError, json.JSONDecodeError, ValueError) as exc:
            explanation = self._build_deterministic_contract(row)
            explanation["metadata"]["generation_mode"] = "deterministic_fallback_after_ai_contract_error"
            explanation["metadata"]["ai_error"] = str(exc)
            return self._to_json(explanation)

    @staticmethod
    def _extract_ai_prose(raw_response: str) -> tuple[str, str]:
        """Parse a two-field AI response. Returns (explanation, corrective_action)."""
        if not isinstance(raw_response, str) or not raw_response.strip():
            raise ValueError("AI provider response was empty.")
        payload = json.loads(raw_response)
        if not isinstance(payload, dict):
            raise ValueError("AI provider response must be a JSON object.")
        explanation = payload.get("explanation", "")
        action = payload.get("corrective_action", "")
        if not isinstance(explanation, str) or not explanation.strip():
            raise ValueError("AI response missing or empty 'explanation' field.")
        if not isinstance(action, str) or not action.strip():
            raise ValueError("AI response missing or empty 'corrective_action' field.")
        return explanation.strip(), action.strip()

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
