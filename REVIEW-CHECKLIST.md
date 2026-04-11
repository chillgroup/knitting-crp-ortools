# Artifact Review Checklist 🔍

> **AGENTS:** Do not mark a feature or task as "Complete" until you verify these checks manually or via automated test runs. Provide `pytest` output or solver log excerpts as proof.
> **HUMANS:** Use this checklist before merging agent-generated code.

## Code Quality & Safety

- [ ] No floats passed to CP-SAT variable bounds or penalty terms (integer Virtual Minutes only).
- [ ] `SolverPayload` / `SolverResponse` schema field names unchanged (Go contract).
- [ ] No CP-SAT calls outside `builder.py` (architectural invariant).
- [ ] Protected files not modified without approval: `request_schema.py`, `response_schema.py`, `requirements.txt` OR-Tools pin, `solver_task.py` webhook.
- [ ] All public methods have type annotations.
- [ ] New penalty constants have inline comments explaining the business rationale and intended ratio.

## Execution & Testing

- [ ] `pytest tests/ -v` passes with 0 failures.
- [ ] Determinism test passes: 5 runs with `num_search_workers=1` produce byte-identical assignments.
- [ ] No-overlap constraint verified: no two tasks share a machine time slot.
- [ ] Pinned tasks land on exactly `pinned_machine_id` at `pinned_start_time`/`pinned_end_time`.
- [ ] `root_cause_code` is not always `"CAPACITY_FULL"` on overloaded payloads.
- [ ] Docker Compose starts cleanly: `docker compose up --build` → all containers healthy.
- [ ] Health endpoint responds: `curl localhost:8083/health` → `{"status": "ok"}`.

## Solver Correctness

- [ ] Model is not infeasible on known valid fixtures (run `pytest tests/test_constraints.py`).
- [ ] Infeasible payloads return `{"status": "infeasible", "assignments": [], "overloads": []}` — no HTTP 500.
- [ ] Ghost-task count warning fires in logs when `capacity_block` tasks > 200.
- [ ] Overload-ratio warning fires when factory load > 85%.

## Artifact Handoff

- [ ] `MEMORY.md` updated with any new architectural decisions made during this task.
- [ ] Completed phase items in `MEMORY.md` are checked off.
- [ ] `AGENTS.md` phase list updated if a phase was completed.
