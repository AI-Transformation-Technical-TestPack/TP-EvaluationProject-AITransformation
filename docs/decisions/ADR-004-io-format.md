# ADR-004: CSV as Primary I/O Format

**Status:** Accepted

## Context
We need an input and output format that is simple, human-readable, version-controllable,
and fast to process while still accepting common spreadsheet workflows.

## Decision
Use CSV for all input and output. Accept Excel files via `pandas.read_excel()` at the
ingestion layer and convert to DataFrame internally — the rest of the pipeline only sees DataFrames.

## Consequences
- ✅ Human-readable — reviewers can inspect input and output files directly
- ✅ Version-controllable — CSV diffs are meaningful in git
- ✅ Fast — no parsing overhead beyond pandas
- ❌ No multi-sheet support — acceptable for this batch validation workflow
