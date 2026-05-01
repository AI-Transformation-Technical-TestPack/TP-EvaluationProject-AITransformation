"""Unit tests for ValidationAgent — covers all 5 employee scenarios plus
edge cases for ghost billing, missing billing, missing contracts, duplicates,
underbilling, billing-over-max, and the strict-cap (capped) expected amount.
"""
import json

import pandas as pd
import pytest

from agents.ai_explanation_agent import AIExplanationAgent
from agents.validation_agent import ValidationAgent

TIMESHEET = pd.DataFrame([
    {"Employee_ID": 101, "Employee_Name": "Alice",   "Project": "A", "Hours_Worked": 40},
    {"Employee_ID": 102, "Employee_Name": "Bob",     "Project": "A", "Hours_Worked": 38},
    {"Employee_ID": 103, "Employee_Name": "Charlie", "Project": "B", "Hours_Worked": 45},
    {"Employee_ID": 104, "Employee_Name": "Diana",   "Project": "B", "Hours_Worked": 42},
    {"Employee_ID": 105, "Employee_Name": "Eve",     "Project": "C", "Hours_Worked": 36},
])

CONTRACTS = pd.DataFrame([
    {"Project": "A", "Rate_per_Hour": 20, "Max_Hours_Per_Week": 40},
    {"Project": "B", "Rate_per_Hour": 25, "Max_Hours_Per_Week": 40},
    {"Project": "C", "Rate_per_Hour": 30, "Max_Hours_Per_Week": 35},
])

BILLING = pd.DataFrame([
    {"Employee_ID": 101, "Project": "A", "Hours_Billed": 40, "Rate_Charged": 20},
    {"Employee_ID": 102, "Project": "A", "Hours_Billed": 40, "Rate_Charged": 22},
    {"Employee_ID": 103, "Project": "B", "Hours_Billed": 50, "Rate_Charged": 25},
    {"Employee_ID": 104, "Project": "B", "Hours_Billed": 42, "Rate_Charged": 25},
    {"Employee_ID": 105, "Project": "C", "Hours_Billed": 36, "Rate_Charged": 28},
])

RULES_STRICT = {
    "allow_rate_tolerance": 0,
    "max_hours_enforcement": True,
    "overbilling_threshold": 0,
    "underbilling_threshold": 0,
}


@pytest.fixture
def agent(tmp_path):
    rules_file = tmp_path / "client_rules.json"
    rules_file.write_text(json.dumps({"client_a": RULES_STRICT}))
    return ValidationAgent(rules_path=str(rules_file))


@pytest.fixture
def report(agent):
    return agent.run(TIMESHEET, CONTRACTS, BILLING, client="client_a")


def _row(report, employee_id):
    return report[report["Employee_ID"] == employee_id].iloc[0]


# ──────────────────────────────────────────────────────────────────────────────
# Per-employee assertions (sample dataset)
# ──────────────────────────────────────────────────────────────────────────────


class TestAlice:
    def test_status_ok(self, report):
        assert _row(report, 101)["Status"] == "OK"

    def test_no_flags(self, report):
        assert _row(report, 101)["Flags"] == ""

    def test_difference_zero(self, report):
        assert _row(report, 101)["Difference"] == 0.0

    def test_capped_equals_expected_when_within_max(self, report):
        row = _row(report, 101)
        assert row["Capped_Expected_Amount"] == row["Expected_Amount"]


class TestBob:
    def test_status_error(self, report):
        assert _row(report, 102)["Status"] == "ERROR"

    def test_overbilling_flag(self, report):
        assert "OVERBILLING" in _row(report, 102)["Flags"]

    def test_rate_mismatch_flag(self, report):
        assert "RATE_MISMATCH" in _row(report, 102)["Flags"]

    def test_no_billing_over_max(self, report):
        # Bob's billed=40 is exactly Max=40; not strictly > so no flag
        assert "BILLING_OVER_MAX" not in _row(report, 102)["Flags"]

    def test_difference_positive(self, report):
        # Billed: 40*22=880, Expected: 38*20=760, diff=+120
        assert _row(report, 102)["Difference"] == 120.0


class TestCharlie:
    def test_status_error(self, report):
        assert _row(report, 103)["Status"] == "ERROR"

    def test_overbilling_flag(self, report):
        assert "OVERBILLING" in _row(report, 103)["Flags"]

    def test_contract_violation_flag(self, report):
        assert "CONTRACT_VIOLATION" in _row(report, 103)["Flags"]

    def test_billing_over_max_flag(self, report):
        # Billed 50 hours > max 40 — independent of worked hours
        assert "BILLING_OVER_MAX" in _row(report, 103)["Flags"]

    def test_difference_positive(self, report):
        # Billed: 50*25=1250, Expected: 45*25=1125, diff=+125
        assert _row(report, 103)["Difference"] == 125.0

    def test_capped_expected_uses_max_not_worked(self, report):
        # Strict-cap interpretation: 40*25=1000, not 45*25=1125
        assert _row(report, 103)["Capped_Expected_Amount"] == 1000.0

    def test_over_cap_exposure(self, report):
        # Billed 1250 - capped expected 1000 = 250
        assert _row(report, 103)["Over_Cap_Exposure"] == 250.0


class TestDiana:
    def test_status_error(self, report):
        assert _row(report, 104)["Status"] == "ERROR"

    def test_contract_violation_flag(self, report):
        assert "CONTRACT_VIOLATION" in _row(report, 104)["Flags"]

    def test_billing_over_max_flag(self, report):
        assert "BILLING_OVER_MAX" in _row(report, 104)["Flags"]

    def test_no_overbilling(self, report):
        assert "OVERBILLING" not in _row(report, 104)["Flags"]

    def test_no_rate_mismatch(self, report):
        assert "RATE_MISMATCH" not in _row(report, 104)["Flags"]

    def test_capped_exposure_when_loose_difference_is_zero(self, report):
        # Diana's loose Difference is $0, but strict-cap exposure is $50
        assert _row(report, 104)["Difference"] == 0.0
        assert _row(report, 104)["Over_Cap_Exposure"] == 50.0


class TestEve:
    def test_status_error(self, report):
        assert _row(report, 105)["Status"] == "ERROR"

    def test_rate_mismatch_flag(self, report):
        assert "RATE_MISMATCH" in _row(report, 105)["Flags"]

    def test_contract_violation_flag(self, report):
        assert "CONTRACT_VIOLATION" in _row(report, 105)["Flags"]

    def test_billing_over_max_flag(self, report):
        assert "BILLING_OVER_MAX" in _row(report, 105)["Flags"]

    def test_difference(self, report):
        # Billed: 36*28=1008, Expected: 36*30=1080, diff=-72
        assert _row(report, 105)["Difference"] == -72.0


# ──────────────────────────────────────────────────────────────────────────────
# New edge-case flags
# ──────────────────────────────────────────────────────────────────────────────


class TestUnderbilling:
    def test_underbilling_flag_when_billed_below_worked(self, agent):
        timesheet = pd.DataFrame([
            {"Employee_ID": 200, "Employee_Name": "Frank", "Project": "A", "Hours_Worked": 40},
        ])
        billing = pd.DataFrame([
            {"Employee_ID": 200, "Project": "A", "Hours_Billed": 30, "Rate_Charged": 20},
        ])
        contracts = pd.DataFrame([
            {"Project": "A", "Rate_per_Hour": 20, "Max_Hours_Per_Week": 40},
        ])
        report = agent.run(timesheet, contracts, billing, client="client_a")
        assert "UNDERBILLING" in _row(report, 200)["Flags"]
        assert "OVERBILLING" not in _row(report, 200)["Flags"]


class TestBillingOverMaxIndependentOfWorked:
    def test_billing_exceeds_cap_even_when_worked_is_within_cap(self, agent):
        # Worked is within cap, but invoice still exceeds cap
        timesheet = pd.DataFrame([
            {"Employee_ID": 201, "Employee_Name": "Grace", "Project": "A", "Hours_Worked": 30},
        ])
        billing = pd.DataFrame([
            {"Employee_ID": 201, "Project": "A", "Hours_Billed": 50, "Rate_Charged": 20},
        ])
        contracts = pd.DataFrame([
            {"Project": "A", "Rate_per_Hour": 20, "Max_Hours_Per_Week": 40},
        ])
        report = agent.run(timesheet, contracts, billing, client="client_a")
        flags = _row(report, 201)["Flags"]
        assert "BILLING_OVER_MAX" in flags
        assert "CONTRACT_VIOLATION" not in flags  # worked is within cap
        assert "OVERBILLING" in flags  # billed > worked


class TestGhostBilling:
    def test_billing_with_no_matching_timesheet(self, agent):
        timesheet = pd.DataFrame([
            {"Employee_ID": 300, "Employee_Name": "Hank", "Project": "A", "Hours_Worked": 40},
        ])
        billing = pd.DataFrame([
            {"Employee_ID": 999, "Project": "A", "Hours_Billed": 20, "Rate_Charged": 20},
        ])
        contracts = pd.DataFrame([
            {"Project": "A", "Rate_per_Hour": 20, "Max_Hours_Per_Week": 40},
        ])
        report = agent.run(timesheet, contracts, billing, client="client_a")
        ghost = report[report["Employee_ID"] == 999].iloc[0]
        assert "GHOST_BILLING" in ghost["Flags"]
        assert ghost["Status"] == "ERROR"


class TestMissingBilling:
    def test_timesheet_without_billing_appears_in_report(self, agent):
        timesheet = pd.DataFrame([
            {"Employee_ID": 400, "Employee_Name": "Ivy", "Project": "A", "Hours_Worked": 40},
        ])
        billing = pd.DataFrame(
            columns=["Employee_ID", "Project", "Hours_Billed", "Rate_Charged"]
        )
        contracts = pd.DataFrame([
            {"Project": "A", "Rate_per_Hour": 20, "Max_Hours_Per_Week": 40},
        ])
        report = agent.run(timesheet, contracts, billing, client="client_a")
        ivy = report[report["Employee_ID"] == 400].iloc[0]
        assert "MISSING_BILLING" in ivy["Flags"]
        assert ivy["Status"] == "ERROR"
        assert ivy["Hours_Billed"] == 0
        # Difference is the negative of expected (lost revenue)
        assert ivy["Difference"] == -800.0


class TestMissingContract:
    def test_billing_for_project_without_contract(self, agent):
        timesheet = pd.DataFrame([
            {"Employee_ID": 500, "Employee_Name": "Jack", "Project": "Z", "Hours_Worked": 40},
        ])
        billing = pd.DataFrame([
            {"Employee_ID": 500, "Project": "Z", "Hours_Billed": 40, "Rate_Charged": 25},
        ])
        contracts = pd.DataFrame(
            columns=["Project", "Rate_per_Hour", "Max_Hours_Per_Week"]
        )
        report = agent.run(timesheet, contracts, billing, client="client_a")
        assert "MISSING_CONTRACT" in _row(report, 500)["Flags"]


class TestDuplicateRecord:
    def test_duplicate_employee_project_flagged(self, agent):
        timesheet = pd.DataFrame([
            {"Employee_ID": 600, "Employee_Name": "Kim", "Project": "A", "Hours_Worked": 40},
        ])
        billing = pd.DataFrame([
            {"Employee_ID": 600, "Project": "A", "Hours_Billed": 40, "Rate_Charged": 20},
            {"Employee_ID": 600, "Project": "A", "Hours_Billed": 40, "Rate_Charged": 20},
        ])
        contracts = pd.DataFrame([
            {"Project": "A", "Rate_per_Hour": 20, "Max_Hours_Per_Week": 40},
        ])
        report = agent.run(timesheet, contracts, billing, client="client_a")
        kim_rows = report[report["Employee_ID"] == 600]
        assert len(kim_rows) == 2
        assert all("DUPLICATE_RECORD" in flags for flags in kim_rows["Flags"])


# ──────────────────────────────────────────────────────────────────────────────
# Client-rule tolerances
# ──────────────────────────────────────────────────────────────────────────────


class TestClientRulesTolerance:
    def test_rate_tolerance_suppresses_mismatch(self, tmp_path):
        rules_file = tmp_path / "client_rules.json"
        rules_file.write_text(json.dumps({
            "lenient": {
                "allow_rate_tolerance": 5,
                "max_hours_enforcement": False,
                "overbilling_threshold": 0,
                "underbilling_threshold": 0,
            }
        }))
        agent = ValidationAgent(rules_path=str(rules_file))
        report = agent.run(TIMESHEET, CONTRACTS, BILLING, client="lenient")
        # Bob's rate diff is $2 — within $5 tolerance, so no RATE_MISMATCH
        assert "RATE_MISMATCH" not in _row(report, 102)["Flags"]
        # Diana and Eve: max_hours_enforcement=False → no CONTRACT_VIOLATION
        assert "CONTRACT_VIOLATION" not in _row(report, 104)["Flags"]
        assert "CONTRACT_VIOLATION" not in _row(report, 105)["Flags"]


# ──────────────────────────────────────────────────────────────────────────────
# AI explanation contract
# ──────────────────────────────────────────────────────────────────────────────


class TestAIExplanationContract:
    def test_prompt_template_renders_two_field_contract(self, report):
        """Prompt should ask the LLM for only two prose fields."""
        prompt = AIExplanationAgent()._build_prompt(_row(report, 102))

        # Identifies the record
        assert "(ID: 102)" in prompt
        # Asks for the two prose fields
        assert '"explanation"' in prompt
        assert '"corrective_action"' in prompt
        # Does NOT ask the LLM to invent numeric / structural fields
        assert '"schema_version"' not in prompt
        assert '"financial_deviation"' not in prompt
        assert '"risk_score"' not in prompt

    def test_deterministic_explanation_uses_json_contract(self, report):
        explained = AIExplanationAgent().run(report, use_ai=False)
        bob_payload = json.loads(_row(explained, 102)["AI_Explanation"])

        assert bob_payload["schema_version"] == "1.0"
        assert bob_payload["record"]["employee_id"] == 102
        assert bob_payload["status"] == "ERROR"
        assert "RATE_MISMATCH" in bob_payload["flags"]
        assert "OVERBILLING" in bob_payload["flags"]
        assert bob_payload["financial_deviation"]["expected_amount"] == 760.0
        assert bob_payload["financial_deviation"]["billed_amount"] == 880.0
        assert bob_payload["financial_deviation"]["difference"] == 120.0
        assert bob_payload["financial_deviation"]["direction"] == "overbilled"
        assert "client trust risk" in bob_payload["financial_deviation"]["business_meaning"]
        assert bob_payload["human_review"]["required"] is True
        assert bob_payload["human_review"]["reviewer_role"] == "billing_supervisor"
        assert bob_payload["confidence"] == 1.0
        assert bob_payload["risk_score"] == "medium"

    def test_risk_score_low_for_single_flag_with_no_money_impact(self):
        # Synthetic row: one flag, zero financial impact
        agent = AIExplanationAgent()
        fake_row = pd.Series({
            "Employee_ID": 999,
            "Employee_Name": "TestUser",
            "Project": "Z",
            "Hours_Worked": 41,
            "Hours_Billed": 41,
            "Rate_Charged": 25,
            "Contract_Rate": 25,
            "Max_Hours": 40,
            "Status": "ERROR",
            "Flags": "CONTRACT_VIOLATION",
            "Expected_Amount": 1025.0,
            "Billed_Amount": 1025.0,
            "Difference": 0.0,
        })
        contract = agent._build_deterministic_contract(fake_row)
        assert contract["risk_score"] == "low"

    def test_risk_score_medium_for_two_flags(self, report):
        # Diana now has 2 flags (CONTRACT_VIOLATION + BILLING_OVER_MAX)
        explained = AIExplanationAgent().run(report, use_ai=False)
        diana_payload = json.loads(_row(explained, 104)["AI_Explanation"])
        assert diana_payload["risk_score"] == "medium"

    def test_risk_score_high_for_three_or_more_flags(self, report):
        # Eve has RATE_MISMATCH + CONTRACT_VIOLATION + BILLING_OVER_MAX
        explained = AIExplanationAgent().run(report, use_ai=False)
        eve_payload = json.loads(_row(explained, 105)["AI_Explanation"])
        assert eve_payload["risk_score"] == "high"

    def test_valid_provider_response_merges_ai_prose_into_deterministic_contract(self, report, monkeypatch):
        """AI returns two prose fields; agent merges them into the full deterministic contract."""
        monkeypatch.setenv("AI_PROVIDER", "openai")
        agent = AIExplanationAgent()

        ai_response = json.dumps({
            "explanation": "Bob was overcharged on Project A by $120 — wrong rate and extra hours.",
            "corrective_action": "Re-issue the invoice with 38 hours at $20/hr; refund the $120 difference.",
        })
        explanation = agent._finalize_provider_response(ai_response, _row(report, 102))
        parsed = json.loads(explanation)

        # AI prose was merged
        assert "Bob was overcharged" in parsed["explanation"]
        assert "Re-issue the invoice" in parsed["corrective_action"]
        # Deterministic fields are untouched and correctly typed
        assert parsed["schema_version"] == "1.0"
        assert parsed["financial_deviation"]["difference"] == 120.0
        assert isinstance(parsed["financial_deviation"]["over_cap_exposure"], float)
        assert parsed["risk_score"] == "medium"
        assert parsed["confidence"] == 1.0
        # Generation-mode metadata reflects the AI provider
        assert parsed["metadata"]["generation_mode"] == "openai"
        assert parsed["metadata"]["source"] == "AIExplanationAgent"

    def test_invalid_provider_response_falls_back_to_deterministic_contract(self, report):
        agent = AIExplanationAgent()
        explanation = agent._finalize_provider_response(
            "This is not JSON.",
            _row(report, 102),
        )
        parsed = json.loads(explanation)

        assert parsed["schema_version"] == "1.0"
        assert parsed["metadata"]["generation_mode"] == "deterministic_fallback_after_ai_contract_error"
        assert "ai_error" in parsed["metadata"]

    def test_provider_response_missing_required_fields_falls_back(self, report):
        agent = AIExplanationAgent()
        # AI response is JSON but missing the 'corrective_action' field
        explanation = agent._finalize_provider_response(
            json.dumps({"explanation": "an explanation but no action"}),
            _row(report, 102),
        )
        parsed = json.loads(explanation)

        assert parsed["metadata"]["generation_mode"] == "deterministic_fallback_after_ai_contract_error"
        assert "corrective_action" in parsed["metadata"]["ai_error"]
