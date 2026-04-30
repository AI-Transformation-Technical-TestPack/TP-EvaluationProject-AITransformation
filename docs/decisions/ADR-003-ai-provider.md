# ADR-003: Configurable AI Provider with Stable Explanation Contract

**Status:** Accepted

## Context
Billing discrepancies need plain-English explanations that non-technical stakeholders can
understand and act on. A hosted AI model improves the quality and consistency of those
explanations, but the system should not be tied to a single vendor or fail when a provider key is
unavailable.

## Decision
Use a configurable AI provider selected by `AI_PROVIDER`. The current implementation supports:

- `anthropic` with `ANTHROPIC_API_KEY` and `ANTHROPIC_MODEL`
- `openai` with `OPENAI_API_KEY`, `OPENAI_MODEL`, and optional `OPENAI_BASE_URL` for
  OpenAI-compatible provider endpoints

Both providers use the same version-controlled prompt in `prompts/discrepancy_prompt.txt` and
must return the same structured JSON explanation contract. The `--no-ai` CLI flag explicitly
forces deterministic explanations when an operator needs a non-networked run.

## Consequences
- The system can use Anthropic, OpenAI, or an OpenAI-compatible endpoint without changing
  validation logic.
- The output contract remains stable even when the provider changes.
- Prompt instructions stay version-controlled and auditable.
- Provider-specific code paths must be maintained behind the same `AIExplanationAgent`
  interface.
