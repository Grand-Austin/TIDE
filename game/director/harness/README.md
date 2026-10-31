# Director harness package v1

This package is versioned application source. It is not a Codex skill installation and does not use pi at runtime.

`SKILL.md` and `WORKFLOWS.md` are loaded into the Director's system message on every graph run. `tools.json` is the source for the actual native function schemas; `Director._schema` filters tools by the harness phase. Registry changes must be accompanied by implementation, workflow and authority tests. Adding a prompt description alone never grants a capability.

The graph injects the operation ID and expected revision. The model supplies no control parameters. The pending resume authorization belongs to the local Session and can be revoked immediately by the player. At each node, the harness specifies the required next function; unknown calls or nonempty arguments are returned as validation errors within a finite round budget.

Tool results are released state projections. The graph checkpointer never receives the complete Session object. State-changing tools persist operation receipts in the same snapshot as their physical revision. Repeated operations return the committed receipt; an interrupted operation restores paused. A new explicit player resume creates a new graph invocation.

Tests in `tests/test_director.py` exercise native schemas, injected report instructions, argument rejection, terminal handling, tool budget, provider failures and actual SQLite persistence. `tests/test_session.py` verifies game authority, rollback, snapshot consistency and interrupted-operation recovery. These are independent checks: prompt compliance cannot replace deterministic authority enforcement.
