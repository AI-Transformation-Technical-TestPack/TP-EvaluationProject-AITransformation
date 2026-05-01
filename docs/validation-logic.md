# Validation Logic — Billing Validation Agent System

## Purpose

This document explains how billing discrepancies are detected and how the report values are
calculated. The README stays focused on running the project; this file explains the reasoning
behind the validation layer.

## Input Data

| File | Required Columns | Role |
|---|---|---|
| `data/input/timesheet.csv` | `Employee_ID`, `Employee_Name`, `Project`, `Hours_Worked` | Source of actual work performed |
| `data/input/contracts.csv` | `Project`, `Rate_per_Hour`, `Max_Hours_Per_Week` | Source of contractual rules |
| `data/input/billing.csv` | `Employee_ID`, `Project`, `Hours_Billed`, `Rate_Charged` | Source of proposed billing |

`DataIngestionAgent` strips column-name whitespace and raises a descriptive error if required
columns are missing.

## Validation Rules

Each flag is independent — multiple may co-occur on a single row.

| Flag | Rule | Why It Matters |
|---|---|---|
| `RATE_MISMATCH` | `abs(Rate_Charged - Rate_per_Hour) > allow_rate_tolerance` | Charge does not match the contracted hourly rate |
| `OVERBILLING` | `Hours_Billed - Hours_Worked > overbilling_threshold` | Billed hours exceed the actual timesheet hours |
| `UNDERBILLING` | `Hours_Worked - Hours_Billed > underbilling_threshold` | Billed hours fall below worked hours — revenue leakage |
| `CONTRACT_VIOLATION` | `Hours_Worked > Max_Hours_Per_Week` when `max_hours_enforcement` is true | Work performed beyond the agreed weekly cap |
| `BILLING_OVER_MAX` | `Hours_Billed > Max_Hours_Per_Week` when `max_hours_enforcement` is true | Invoice charges beyond the cap, even if worked hours were within it |
| `GHOST_BILLING` | Billing row has no matching timesheet (Hours_Worked is missing) | Billed work cannot be substantiated |
| `MISSING_BILLING` | Timesheet row has no corresponding billing entry | Work was performed but never invoiced |
| `MISSING_CONTRACT` | Project in billing/timesheet not present in contracts | Rate and cap cannot be validated |
| `DUPLICATE_RECORD` | Two or more billing rows share the same Employee_ID + Project | Risk of double-invoicing |

## Calculated Fields

| Field | Formula | Notes |
|---|---|---|
| `Expected_Amount` | `Hours_Worked * Contract_Rate` | Loose interpretation: bill what was worked |
| `Billed_Amount` | `Hours_Billed * Rate_Charged` | What is on the invoice |
| `Difference` | `Billed_Amount - Expected_Amount` | Positive = overbilled, negative = underbilled |
| `Capped_Expected_Amount` | `min(Hours_Worked, Max_Hours_Per_Week) * Contract_Rate` | Strict interpretation: invoice cannot exceed the cap |
| `Over_Cap_Exposure` | `Billed_Amount - Capped_Expected_Amount` | The portion of the bill that exceeds the contracted cap |
| `Status` | `ERROR` if one or more flags are present, else `OK` | |
| `Flags` | Comma-separated active discrepancy codes | |

### Loose vs. strict interpretation of contract cap

`Difference` and `Expected_Amount` use the **loose** interpretation: bill the client for what
the employee actually worked. `Capped_Expected_Amount` and `Over_Cap_Exposure` use the
**strict** interpretation: the contractual weekly cap is a hard ceiling on billable hours.

Both are reported because the right interpretation is a business-policy choice. The
deterministic explanation calls out `BILLING_OVER_MAX` whenever the strict interpretation
would change the conclusion, so a supervisor can choose which view to honour per client.

## Financial Deviation (`financial_deviation`)

The explanation JSON contract includes a `financial_deviation` object. It is a structured
summary of how much the proposed billing differs from what the contract and timesheet imply.

The values come directly from the report calculations:

- `expected_amount = Hours_Worked * Contract_Rate`
- `billed_amount = Hours_Billed * Rate_Charged`
- `difference = billed_amount - expected_amount`

The `direction` field is derived from `difference`:

- `overbilled` when `difference > 0`
- `underbilled` when `difference < 0`
- `no_amount_delta` when `difference == 0`

The `business_meaning` field summarizes what that deviation represents for the business
in plain language, such as revenue leakage, credit exposure, client trust risk, or audit
exposure.

## Client Rules

Validation behavior is configured in `config/client_rules.json`:

```json
{
  "client_a": {
    "allow_rate_tolerance": 0,
    "max_hours_enforcement": true,
    "overbilling_threshold": 0,
    "underbilling_threshold": 0
  },
  "client_b": {
    "allow_rate_tolerance": 2,
    "max_hours_enforcement": false,
    "overbilling_threshold": 1,
    "underbilling_threshold": 1
  }
}
```

`underbilling_threshold` defaults to `overbilling_threshold` if omitted, so older configs
remain compatible.

This keeps policy changes out of source code. A new client can be added by adding a new rules
object and passing `--client <name>` to the CLI.

## Explanation Strategy

Only rows with `Status = ERROR` receive explanations. `AIExplanationAgent` uses the provider
selected by `AI_PROVIDER` with the version-controlled prompt in
`prompts/discrepancy_prompt.txt`. The current provider options are Anthropic, OpenAI, and
OpenAI-compatible endpoints configured through `OPENAI_BASE_URL`. When the selected provider key
is absent or `--no-ai` is used, the agent generates deterministic explanations from the active
flags.

The output report includes an `AI_Explanation` field for each error row. `AIExplanationAgent`
writes a JSON string into that field so downstream agents can reuse a stable contract. The
payload includes:

- The affected employee and project.
- Active discrepancy flags.
- Plain-English explanation.
- Corrective action recommendation.
- `financial_deviation`, including expected amount, billed amount, difference, direction,
  and business meaning.
- Human review requirements for supervisor approval.
