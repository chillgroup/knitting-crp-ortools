# Code Patterns

## Architecture Pattern

- **Primary pattern:** Service-oriented — `Engine` (orchestrator) + `TaskModelBuilder` (domain service)
- **Rule:** All CP-SAT model construction goes in `TaskModelBuilder` methods. `Engine.solve()` only parses, chains, and runs.
- **Rule:** Reuse existing builder methods before adding new ones. Add to `build_resource_allocations()` if it's resource-related; add a new method only for a new constraint category.
- **Rule:** Builder methods return `self` — preserve the fluent chain in `Engine.solve()`.

## Builder Method Pattern

Every new builder step follows this structure:

```python
def apply_my_constraint(self) -> "TaskModelBuilder":
    """
    Docstring: what constraint this applies and why.
    Reference to research question if applicable (e.g., "Q2 workforce capacity").
    """
    logger.info("\n🔧 APPLYING MY CONSTRAINT:")
    for t_id, tv in self.task_vars.items():
        # Guard: skip if this task type doesn't apply
        task_info = next((t for t in self.tasks if t["task_id"] == t_id), {})
        if task_info.get("operation", "").lower() != "relevant_operation":
            continue

        # Build the constraint
        self.model.Add(...)
        logger.info(f"   ✅ {t_id}: constraint applied")

    return self  # Always return self for chaining
```

## Penalty Constants Pattern

```python
# Module-level constants with ratio documentation
# Target: LATENESS : AFFINITY : ACTIVATION ≈ 1000 : 10 : 2
PENALTY_PER_ROLL_SWAP: int = 100   # Per yarn roll that must be swapped on a machine
PENALTY_COLD_START: int = 200      # Machine has no thread — full reel-threading needed
PENALTY_ACTIVATE_RESOURCE: int = 50  # Activating a previously idle machine
PENALTY_CHANGE_DESIGN: int = 10    # Load new design file (fast — USB only)
```

When adding a new penalty constant:
1. Add at module level with type annotation `int`
2. Add inline comment explaining what physical action it represents
3. Verify it fits the intended ratio relative to existing constants
4. Add to the objective via `self.objective_terms.append(is_selected * penalty)`

## Conditional CP-SAT Constraints Pattern

```python
# CORRECT: OnlyEnforceIf takes the literal itself (not negation)
self.model.Add(start_var >= available_at).OnlyEnforceIf(is_selected)

# CORRECT: Negated literal for "when NOT selected"
self.model.Add(something).OnlyEnforceIf(is_selected.Not())

# WRONG: Never pass Python bool — CP-SAT requires BoolVar
# self.model.Add(...).OnlyEnforceIf(True)  ← WRONG
```

## task_vars Dictionary Pattern

`self.task_vars[t_id]` is the single source of truth per task during model construction:

```python
{
    "start": IntVar,          # CP-SAT start variable (or NewConstant for pinned)
    "end": IntVar,            # CP-SAT end variable (or NewConstant for pinned)
    "literals": [BoolVar],    # One per compatible resource, in same order as r_ids
    "r_ids": [str],           # Compatible resource IDs (same index as literals)
    "due": int,               # due_at_min in Virtual Minutes
    "original_order_id": str,
    "group_id": str,
    "depends_on": [str],      # final_depends_on list
    "qty": float,
}
```

When reading a literal for a specific resource:
```python
lit = next(
    (l for l in tv["literals"] if l.Name().endswith(f"_on_{r_id}")),
    None
)
```

## Data Fetching / State

- **No database.** All state comes from the `SolverPayload` JSON. Parse it in `Engine.__init__()`.
- **`self.task_vars`** is populated by `build_time_variables()` and mutated by later builder steps.
- **`self.resource_map`** is populated in `__init__` and mutated (intervals added) by `build_resource_allocations()`.
- **`self.objective_terms`** is a list — append to it in any builder step that adds a penalty.

## Error Handling

```python
# At JSON boundaries — always return a dict, never raise
def solve(self) -> Dict[str, Any]:
    try:
        # ... builder chain + solver
        return builder.extract_results(solver, status)
    except Exception as exc:
        logger.error(f"Solver error: {exc}", exc_info=True)
        return {"status": "infeasible", "assignments": [], "overloads": []}

# For missing/invalid inputs — warn and skip, don't crash
if actual_id not in self.task_vars:
    logger.warning(f"⚠️ Task '{t_id}' depends on '{raw_id}' — not found!")
    continue  # Do not raise
```

## Validation Pattern

- Pydantic validates the incoming `SolverPayload` at the FastAPI layer.
- Inside `builder.py`, trust that `task["task_id"]` and `task["duration"]` exist (Pydantic guarantee).
- Guard only against *business logic* edge cases: no compatible resources, missing translation map entries, zero-duration tasks.

## Change Discipline

- One builder method per commit — do not modify multiple constraint methods in one change.
- Do not change `request_schema.py` field aliases without notifying the Go team first.
- Do not upgrade the OR-Tools pin without a benchmark showing no regression on `payload_200_tasks.json`.
- Penalty constant changes require a note in `MEMORY.md` explaining the calibration reasoning.

## Naming for CP-SAT Variables

CP-SAT variable names must be unique within a model. Use these patterns:

| Variable Type | Name Pattern | Example |
|--------------|-------------|---------|
| Task start | `start_{task_id}` | `start_K1-order_001` |
| Task end | `end_{task_id}` | `end_K1-order_001` |
| Lateness | `lat_{task_id}` | `lat_K1-order_001` |
| Assignment literal | `{task_id}_on_{r_id}` | `K1-order_001_on_M01` |
| Optional interval | `int_{task_id}_{r_id}` | `int_K1-order_001_M01` |
| Unavailability | `unavail_{r_id}` | `unavail_M01` (may need index for multiple windows) |
| PO bounding start | `po_{po_id}_{r_id}_start` | `po_order_001_M01_start` |
| Resource activated | `activated_{r_id}` | `activated_M01` |
| Global interval | `global_interval_{t_id}` | `global_interval_K1-order_001` |
