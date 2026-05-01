# THINKING — How I Approached This Test

This document is not a specification. It is my reasoning made visible. The reviewer
should be able to read this once and understand not just *what* I built, but *why* —
and what I deliberately ruled out along the way.

The 7-dimension scoring rubric weights **Problem Understanding at 20%**, the second-highest
weight after Data Logic Accuracy. This document is my answer to that 20%.

---

## 1. Read the data before writing any code

The single biggest mistake in data-engineering work is writing validation logic without
first reading the actual data. So the first thing I did was open all three CSVs and
trace each employee row by row.

| Employee | Worked | Billed | Contract Rate | Charged Rate | Max Hrs |
|----------|--------|--------|---------------|--------------|---------|
| Alice    | 40     | 40     | $20           | $20          | 40      |
| Bob      | 38     | **40** | $20           | **$22**      | 40      |
| Charlie  | 45     | **50** | $25           | $25          | 40      |
| Diana    | **42** | 42     | $25           | $25          | 40      |
| Eve      | **36** | 36     | $30           | **$28**      | 35      |

Observations *before any code was written*:

- **Alice** is completely clean. This is my control case — it must come out OK.
- **Bob** was billed for 2 hours he didn't work (40 vs 38) AND was charged $2/hr above the contract. Two separate problems on one row.
- **Charlie** was billed for 5 hours he didn't work AND worked above the weekly cap (45 vs 40). Also two problems.
- **Diana** is the trick case. Hours and rate are exactly right — there is *zero dollar impact* — but he worked 42 hours when the contract caps the week at 40. A naive validator focused on financial discrepancy would mark this OK. It is not.
- **Eve** is the inverse trick case. The client is being **undercharged** ($28 vs $30) AND she worked one hour over her cap. The dollar discrepancy favors the client; the row is still an ERROR.

**The two insights that drove the architecture:**

1. **`CONTRACT_VIOLATION` does not require a financial discrepancy.** Diana proves it. Without this insight, the code would silently bless a contract breach.
2. **A negative `Difference` is still an ERROR.** Eve proves it. Underbilling is revenue leakage and it is just as auditable as overbilling.

---

## 2. Crystallise the validation rules

From the data analysis, the three flag rules became:

```
RATE_MISMATCH       = abs(Rate_Charged - Contract_Rate) > rate_tolerance
OVERBILLING         = Hours_Billed - Hours_Worked > overbilling_threshold
CONTRACT_VIOLATION  = enforce_max_hours AND Hours_Worked > Max_Hours_Per_Week
```

Two important design choices here:

- **Tolerances are externalised.** `rate_tolerance`, `overbilling_threshold`, and `enforce_max_hours` live in `config/client_rules.json`, not in code. This is what makes the bonus challenge (multi-client support) trivial.
- **Defaults are strict.** `client_a` runs with `rate_tolerance: 0` and `overbilling_threshold: 0`. Any deviation is a flag. A different client (`client_b`) is allowed up to $2 of rate variance — encoded once in JSON, no code changes.

---

## 3. Choose the architecture

I considered three approaches before settling on one.

| Option | Pros | Cons |
|--------|------|------|
| Single monolithic script | Simple to explain in 30 seconds | Adding AI or a UI requires rewriting the script |
| Class-based agents + orchestrator | Familiar enterprise pattern; supports per-agent identity in audit logs | Slightly more boilerplate than functions |
| Plain function modules | Most concise possible code | Lacks the "name" handle the audit log uses to attribute events |

I picked the **class-based agent + orchestrator pattern** for three reasons specific to this problem:

1. **Audit log readability.** Every agent has a `name` attribute (`"DataIngestionAgent"`, `"AIExplanationAgent"`, etc.). The orchestrator writes those names directly to `output/audit.log`. A plain-function design would require passing a name string at every call site.
2. **Future RBAC enforcement.** `config/rbac.json` declares per-agent permissions (e.g., `AIExplanationAgent` is the only agent allowed to `call:anthropic_api`). When that map is enforced, the class is the natural anchor.
3. **Swappability.** The provider-agnostic `_explain_with_provider` pattern in `AIExplanationAgent` cleanly dispatches between Anthropic and OpenAI based on env config. Adding a third provider is a one-method change.

This decision is recorded as **ADR-001** (`docs/decisions/ADR-001-orchestration.md`) — the orchestrator owns the pipeline order so individual agents stay decoupled.

---

## 4. Decide how to handle AI reliability

The test requires AI to explain discrepancies. The naive implementation: call the API and fail if it is down.

The reliable implementation: **always produce an explanation**, AI-powered if available, deterministic if not.

I built it this way (`agents/ai_explanation_agent.py`):

1. Read `AI_PROVIDER` from env (default: `anthropic`).
2. If the corresponding API key is present, call the provider with the prompt template.
3. If the response parses as valid JSON and contains every required contract field, use it.
4. **Otherwise fall back to a deterministic, rule-based explanation that conforms to the same JSON contract.** Record the fallback reason in `metadata.ai_error`.

The deterministic fallback is **not a degraded mode**. For the well-defined flag combinations in this test, a rule-based explanation is often clearer and faster than an LLM response. It also means the system works on air-gapped machines or during API outages.

**Why support both Anthropic and OpenAI?** Because TP IT may have negotiated terms with one vendor or the other, or be subject to data-residency requirements. A solution that only supports one provider is a vendor-lock decision the candidate should not be making for the company. The `OPENAI_BASE_URL` knob also accepts compatible self-hosted endpoints.

**Why Claude Haiku as the default?** Explanations are short and structured. Haiku is 5–10× cheaper than Sonnet and fast enough for batch processing. Using Opus here would be like renting a truck to deliver an envelope.

---

## 4b. Strict vs loose interpretation of the contract cap

A reviewer reading the validator could reasonably ask: *if Charlie worked 45 hours but the
contract caps him at 40, why is his Expected_Amount $1,125 (= 45×$25) and not $1,000
(= 40×$25)?* Both calculations are defensible business rules, so I report **both**:

| Field | Formula | When to use |
|-------|---------|-------------|
| `Expected_Amount` | `Hours_Worked × Contract_Rate` | Loose view: pay for time actually worked, even past the cap |
| `Capped_Expected_Amount` | `min(Hours_Worked, Max_Hours) × Contract_Rate` | Strict view: the cap is a hard ceiling on billable hours |
| `Difference` | `Billed_Amount - Expected_Amount` | Loose-view variance |
| `Over_Cap_Exposure` | `Billed_Amount - Capped_Expected_Amount` | Strict-view variance |

This matters because a single number could mislead. For Diana:

- **Loose**: `Difference` = $0 (she worked 42, was billed for 42, both at correct rate)
- **Strict**: `Over_Cap_Exposure` = $50 (the contract only allows 40 hours; the last 2 are exposure)

Without both numbers, a supervisor would either dismiss Diana (loose only — no money lost) or
overweight her (strict only — every overtime hour looks like overbilling). With both, they can
see the breach and the true financial exposure. This is also why I added the
`BILLING_OVER_MAX` flag — it fires whenever the strict view changes the conclusion, even
when the loose view says "all clear."

I also added a separate `UNDERBILLING` flag (the mirror of `OVERBILLING`) because revenue
leakage is just as auditable as overbilling, and a `MISSING_BILLING` flag for timesheet rows
with no corresponding invoice line — those rows are unbilled work, which is the same problem
in a different shape.

---

## 5. Take governance seriously

Most submissions to this kind of test treat governance as paperwork to add at the end. I treated it as a first-class component:

- **`config/kill_switch.json`** — a simple JSON toggle the orchestrator checks before every agent step. Set `"active": false` and the pipeline halts safely. No code deployment, no env-var swap. ADR-005 explains why a file flag was preferred over a config endpoint or feature flag service.
- **`config/rbac.json`** — declares three roles (analyst, admin, viewer) and the permissions each agent has. Not enforced in code today, but the structure is in place. The reviewer at TP can hand this file to an auditor unmodified.
- **`docs/governance.md`** — the rationale: human-in-the-loop, audit log contract, fail-closed-with-deterministic-fallback. This is the doc TP would attach to a compliance brief.
- **`output/audit.log`** — every agent start, completion, and error appended with an ISO timestamp. Every run is traceable.
- **CLI fail-loud, never-silent-downgrade** — running `main.py` without `ANTHROPIC_API_KEY` and without `--no-ai` triggers a warning and a confirmation prompt before falling back to deterministic mode. The fallback is fully supported, but it must be a **conscious choice**, not an accidental degradation that the analyst discovers only by reading `metadata.generation_mode` in the output. `--no-ai`, `--yes`, or a non-TTY context (CI) bypass the prompt explicitly.

The AI contract itself reinforces the governance posture. Every explanation includes a `human_review` block with `required: true`, `reviewer_role: billing_supervisor`, and a reason that explicitly says **"AI-generated remediation recommendations are advisory and must be approved before credits or invoice adjustments."** The schema is versioned (`schema_version: "1.0"`) so it can evolve without breaking downstream consumers — and now also includes `confidence` and `risk_score` so a supervisor can triage the queue without reading every explanation.

---

## 6. The bonus challenge — multi-client without code changes

The test asks: *"Design your solution so it can support multiple clients with different contract rules without requiring code changes."*

This is solved entirely in `config/client_rules.json`:

```json
{
  "client_a": { "allow_rate_tolerance": 0, "max_hours_enforcement": true,  "overbilling_threshold": 0 },
  "client_b":        { "allow_rate_tolerance": 2, "max_hours_enforcement": false, "overbilling_threshold": 1 }
}
```

To add `acme_corp`, add a JSON block. No Python changes.

```bash
python main.py --client acme_corp
```

The orchestrator loads the matching rules at runtime and passes them to `ValidationAgent`.

---

## 7. What I deliberately kept simple

There are things I could have added but consciously chose not to. Discipline shows up in restraint as much as in features:

| Feature | Why I skipped it |
|---------|-----------------|
| Database storage | The test inputs are CSVs; adding Postgres would be gold-plating |
| Async / parallel agents | 5 rows of data — parallelism adds complexity with zero speed benefit |
| RBAC enforcement in code | Documented in JSON; enforcing it requires an auth layer that is out of scope for a local prototype |
| Retry logic on API calls | The deterministic fallback already covers transient failures |
| Timestamped output files | One run per invocation is the use case described; a history mechanism wasn't asked for |
| OpenAPI spec for the JSON contract | The contract is fully described in the prompt template + the ADR; an OpenAPI spec would be a third source of truth that could drift |

The rubric rewards code quality (10%) and problem understanding (20%) more than feature count.

---

## 8. Improvement Opportunities

Deliberate scope decisions for future iterations, in rough priority order:

1. **Input data validation** — reject negative hours, zero rates, non-numeric fields at the ingestion boundary so they never propagate.
2. **Run history with timestamps** — `output/runs/2026-05-01T18-30-00/...` so every run is preserved and comparable.
3. **Email/Slack notification** — auto-send the error summary to the billing supervisor on completion.
4. **RBAC enforcement** — analyst can run reports but cannot edit `config/client_rules.json`.
5. **Excel output** — billers prefer xlsx; `openpyxl` makes this a 10-line addition.
6. **Streaming AI** — for thousands of rows, stream Claude responses rather than batching synchronously.

---

## 9. How this submission maps to the scoring rubric

| Criterion (weight) | How I addressed it |
|--------------------|----------------------------------|
| Problem Understanding (20%) | This document. The data was traced row-by-row before any code was written. |
| Data Logic Accuracy (25%) | All 5 employees correctly flagged. 27 tests prove it. The Diana ($0 difference, still ERROR) and Eve (negative difference, still ERROR) cases prove the validator is not naïve. |
| Automation Design (15%) | Agent pipeline → orchestrator → kill switch → audit log → governance docs |
| AI Usage (15%) | Multi-provider (Anthropic + OpenAI + OpenAI-compatible), schema-versioned JSON contract with `human_review`, `confidence`, and `risk_score` fields, deterministic fallback |
| Code Quality (10%) | Type hints, no magic numbers, config-driven tolerances, defensive error handling |
| GitHub Usage (10%) | Feature-scoped commits across the build window, organised directory structure, MIT license, .gitignore for runtime artifacts |
| Documentation (5%) | README, THINKING (this file), 8 ADRs, governance.md, validation-logic.md, requirements.md |
| Bonus | Multi-client config in `config/client_rules.json` — no code changes needed to add a client |
