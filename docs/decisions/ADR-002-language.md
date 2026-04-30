# ADR-002: Implementation Runtime

**Status:** Accepted

## Context
The system needs rapid prototyping, reliable tabular-file processing,
easy integration with agentic patterns, and low setup friction for reviewers and maintainers.

## Decision
Use Python 3.12+ with:
- `pandas` for data processing
- `pytest` for unit testing
- `typing` for type annotations
- `rich` for terminal output
- `python-dotenv` for environment variable management

## Consequences
- Python provides a fast development cycle and a strong ecosystem for tabular data work.
- Reviewers can run the project immediately after `pip install -r requirements.txt` because
  there is no compilation step.
- `pandas` handles CSV and Excel files through the same DataFrame abstraction used by the
  validation pipeline.
- Python is slower than compiled languages, but that tradeoff is acceptable for this batch
  validation workflow.
