# ADR-007: CLI-First Interface with Optional Streamlit UI

**Status:** Accepted

## Context
The system should be easy to run repeatedly from a terminal while still offering a visual
review path for users who prefer a browser interface. Options considered: real-time AI chat,
web UI, CLI only, CLI with interactive menu.

## Decision
Two interface modes:

**Primary — CLI (argparse):**
```
python main.py --mode orchestrated --input data/input/billing.csv --verbose
```
Deterministic, fast, and easy to review. Shows the orchestrated pipeline executing step by step.

**Secondary — Interactive menu (`--interactive`):**
A static menu presented in the terminal when `--interactive` is passed.
No LLM calls in the menu itself — options are numbered, selection triggers the pipeline.

**Tertiary — Streamlit web UI (`app.py`):**
Visual interface with file uploaders, color-coded results table, and AI explanation panel.
Uses a neutral, professional design system (slate-900 brand surface, blue accent for CTAs, status colors for OK / ERROR / risk levels).

## Consequences
- ✅ CLI is deterministic and reliable for repeatable validation runs
- ✅ Interactive menu adds UX personality without LLM variability
- ✅ Streamlit UI demonstrates full-stack thinking
- ❌ Three interfaces to maintain — scope is small enough that this is acceptable
