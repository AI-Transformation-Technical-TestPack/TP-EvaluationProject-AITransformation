# Docs Index

This folder explains why the Billing Validation Agent System is designed the way it is.
The root `README.md` is intentionally focused on understanding and running the project.

## Start Here

| Document | Purpose |
|---|---|
| [`usage.md`](usage.md) | Read this when you want CLI flags, alternative run modes, governance toggles, the per-feature reviewer self-guided tour, and test-suite invocations. |
| [`architecture.md`](architecture.md) | Read this when you want to understand how the agents work together and why each repository area exists. |
| [`validation-logic.md`](validation-logic.md) | Read this when you want to understand how billing values are calculated and how each exception flag is assigned. |
| [`governance.md`](governance.md) | Read this when you want to understand how audit logging, supervisor review, permissions, and the kill switch keep automation controlled. |
| [`requirements.md`](requirements.md) | Read this when you want to see the user-facing capabilities the system is expected to satisfy. |
| [`decisions/`](decisions/) | Read these records when you want to understand the tradeoffs behind major design choices. |

## Docs Principle

- `README.md`: "I understand this project and can run it."
- `docs/`: "I understand why it was designed this way."
