# Requirements — Billing Validation Agent System

For the implementation mapping, see
[`Requirements-to-Agent Mapping`](architecture.md#requirements-to-agent-mapping).

## User Stories

### US-001: Source Data Intake
**As a** billing analyst,  
**I want** the system to load timesheet, contract, and billing CSVs automatically,  
**So that** I do not need to manually prepare or transform data before validation.

**Acceptance Criteria:**
- Accepts comma-separated files and spreadsheet uploads
- Validates expected columns exist; raises a descriptive error if not
- Normalizes column names (strip whitespace, consistent casing)

---

### US-002: Billing Amount Reconciliation
**As a** billing analyst,  
**I want** the system to compute what each employee should have been billed based on their timesheet and contract,  
**So that** I can see the delta between expected and actual billed amounts.

**Acceptance Criteria:**
- Expected amount = hours worked × contract rate
- Actual amount = hours billed × rate charged
- Difference = actual − expected (positive = overbilled, negative = underbilled)

---

### US-003: Billing Exception Detection
**As a** billing analyst,  
**I want** the system to flag incorrect rates, excess billed hours, and contract-limit breaches,
**So that** errors are surfaced before invoices reach the client.

**Acceptance Criteria:**
- `RATE_MISMATCH`: rate charged differs from contract rate beyond the configured tolerance (formula documented in `docs/validation-logic.md`)
- `OVERBILLING`: hours billed exceed hours worked beyond the configured threshold (formula documented in `docs/validation-logic.md`)
- `CONTRACT_VIOLATION`: hours worked exceed max hours/week when enforcement is enabled (formula documented in `docs/validation-logic.md`)

---

### US-004: Reviewable Validation Report
**As a** billing manager,  
**I want** a clean output CSV with OK/ERROR flags and discrepancy details,  
**So that** I can review and act on findings without re-processing raw files.

**Acceptance Criteria:**
- Output columns: Employee_ID, Employee_Name, Project, Hours_Worked, Hours_Billed, Rate_Charged, Contract_Rate, Max_Hours, Status, Flags, Expected_Amount, Billed_Amount, Difference, AI_Explanation
- Status is either `OK` or `ERROR`
- Flags is a comma-separated list of active discrepancy codes

---

### US-005: Orchestrated Pipeline Execution
**As a** billing analyst,  
**I want** to run the entire validation pipeline with a single command,  
**So that** the process is repeatable and does not require manual steps.

**Acceptance Criteria:**
- `python main.py --mode orchestrated --input data/input/billing.csv --verbose`
- Pipeline runs DataIngestion → Validation → AIExplanation → Report in sequence
- Kill switch halts the pipeline safely between steps

---

### US-006: Structured Discrepancy Explanations
**As a** billing manager,  
**I want** each error to include a plain-English explanation and a corrective action,  
**So that** non-technical stakeholders can understand findings and make informed decisions.

**Acceptance Criteria:**
- Configured AI provider called for each ERROR row when the selected provider key is set
- Deterministic fallback explanation used when the selected provider key is absent
- Explanation included in output CSV and UI as a structured JSON contract
- Explanation includes financial deviation and business impact for ERROR rows

---

### US-007: User Review Interfaces
**As a** billing analyst,  
**I want** a visual interface to upload files and view results,  
**So that** I can use the system without knowing CLI commands.

**Acceptance Criteria:**
- Streamlit UI with file uploaders for 3 CSVs
- Color-coded results table (green = OK, red = ERROR)
- Expandable AI explanation per ERROR row
- Download button for output CSV

## Delivery Requirements

The product requirements above describe what the billing validation system does. The delivery
requirements below describe how the project is packaged so it can be inspected, run, and
maintained.

- The README explains setup, execution, and the expected output.
- The repository separates source data, agents, orchestration, prompts, configuration,
  governance controls, documentation, tests, and generated output.
- Architecture, validation logic, and governance decisions are documented under `docs/`.
- Prompt instructions are version-controlled under `prompts/`.
- Validation behavior is protected by tests under `tests/`.
- Generated reports and logs are written under `data/output/`.
