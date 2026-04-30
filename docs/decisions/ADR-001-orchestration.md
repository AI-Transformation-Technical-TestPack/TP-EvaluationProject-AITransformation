# ADR-001: Hybrid Orchestration over Fully Decentralized Agents

**Status:** Accepted

## Context
We need a workflow that is autonomous, observable, and auditable. Fully decentralized
(peer-to-peer handoff) agents are harder to debug, log uniformly, and apply a kill switch to.

## Decision
Implement a central **Orchestrator** that decomposes the task and delegates to specialized
**Worker Agents** (DataIngestion, Validation, AIExplanation, Report). Workers do not
communicate directly with each other.

## Consequences
- ✅ Single logging point — audit trail is consistent and complete
- ✅ Kill switch enforced at orchestrator level before each agent step
- ✅ Easy to add, remove, or replace individual worker agents
- ✅ Clear error recovery — orchestrator catches and reports worker failures
- ❌ Single point of failure — mitigated by retry logic and kill switch
