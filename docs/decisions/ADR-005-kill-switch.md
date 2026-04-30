# ADR-005: Kill Switch via File Flag

**Status:** Accepted

## Context
Governance requires an emergency stop mechanism. The system runs autonomously across
multiple agent steps; stakeholders need a simple, reliable way to halt it mid-execution
without killing the OS process or requiring code changes.

## Decision
The Orchestrator reads `governance/kill_switch.json` before delegating to each agent.
If `{"active": false}` is found, it logs the halt event and raises `SystemExit`.

Default state: `{"active": true}` — system runs normally.
To halt: set `active` to `false` in the file while the process is running.

## Consequences
- ✅ Cross-platform — no OS signal handling required
- ✅ Auditable — halt events are logged to output/audit.log
- ✅ Simple for non-technical operators — edit one JSON field
- ❌ Polling adds small overhead (file read before each agent step) — milliseconds, acceptable
