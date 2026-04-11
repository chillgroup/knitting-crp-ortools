# Product Requirements Document: Knitting-CRP-Ortools MVP

## Executive Summary

**Product:** Knitting-CRP-Ortools
**Version:** MVP (1.0)
**Document Status:** Draft
**Last Updated:** 2026-04-11

### Product Vision

A production-grade CP-SAT solver microservice that acts as the scheduling brain of a textile manufacturing factory's APS system — consuming structured job payloads from a Go backend, solving a constrained job-shop scheduling problem, and returning deterministic, explainable schedules with bottleneck diagnostics.

### Success Criteria

- Deterministic output: same input → same schedule across worker restarts and CP-SAT version patches
- Solve within the configured time-box (60s–5min) with ≤ 1% optimality gap
- Zero infeasible crashes on valid inputs; infeasibility always returns a structured error with root-cause context
- Go backend receives actionable `overloads[]` with enough detail to drive UI-level suggestions

---

## Problem Statement

### Problem Definition

A knitting factory runs multi-stage production orders (Knitting → Linking → finishing operations) across a fleet of machines with heterogeneous capabilities, yarn-color setups, and operator shift constraints. Manual scheduling is brittle and can't handle:

- Hundreds of concurrent tasks with complex DAG dependencies
- Machine-affinity preferences (yarn reel swap penalties)
- Shift-based workforce capacity limits
- Pipelined batch production (Linking can start before Knitting finishes)
- Pinned/locked tasks from in-progress production

### Impact Analysis

- **User Impact:** Planners spend hours daily on manual re-scheduling; late orders go undetected until delivery
- **Business Impact:** Late shipments, machine idle time, and suboptimal yarn-setup sequences cost real margin
- **Technical Impact:** The Go backend cannot expose a viable planning UI without a reliable solver backend

---

## Target Audience

### Primary Persona: Operations Research / Backend Engineer ("the integrator")

- Maintains the solver service and Go-side payload preparation
- Deep knowledge of CP-SAT API, Pydantic, and the factory's domain model
- Owns correctness, determinism, and performance of the scheduling engine

### Secondary Persona: Factory Planner (via Go UI)

- Consumes schedule outputs through the Go frontend
- Needs clear LATE / ON_TIME statuses and bottleneck explanations
- Does not interact with Python directly

---

## User Stories

### Epic: Reliable Schedule Generation

**Primary:** "As the Go backend, I want to POST a SolverPayload and receive a CompletedJobResultPayload via webhook, so that the planning UI can display a conflict-free production schedule."

**Acceptance Criteria:**
- [ ] Valid payload → feasible result within `max_search_time` seconds
- [ ] Result includes `assignments[]` with `task_id`, `machine_id`, `start_time`, `end_time`, `status`
- [ ] Result includes `overloads[]` with `root_cause_code` and `bottleneck_resource_id`
- [ ] Deterministic: same payload with same `random_seed` → byte-identical assignments

### Supporting Stories

1. "As a planner, I want LATE tasks to report which specific machine caused the delay, so that I can reassign capacity from the UI."
   - AC: `overloads[].bottleneck_resource_id` is populated and maps to a real machine_id

2. "As the Go backend, I want pinned tasks to be immovable in the schedule, so that in-progress production is never re-ordered."
   - AC: pinned tasks have `start_time == pinned_start_time`, `machine_id == pinned_machine_id`

3. "As the solver engineer, I want the objective function to balance lateness vs. affinity penalties without stagnation, so that solution quality improves monotonically within the time-box."
   - AC: no regression in objective value for identical inputs across deploys

4. "As the solver engineer, I want workforce capacity enforced via `AddCumulative`, so that the total concurrent knitting tasks never exceeds `max_factory_machines`."
   - AC: no schedule violates the global machine cap; capacity_block tasks reduce headroom correctly

---

## Functional Requirements

### Core Features (MVP — P0)

#### Feature 1: Task Time Variable Construction (`build_time_variables`)

- **Description:** Create CP-SAT `start`/`end`/`interval` variables per task; handle pinned constants; apply `start_after_min` / `due_at_min`; compute weighted lateness penalty terms
- **User Value:** Foundation for all scheduling constraints
- **Acceptance Criteria:**
  - [ ] Pinned tasks use `NewConstant` (not `NewIntVar`)
  - [ ] Duplicate `task_id`s from Go are renamed with `_dupN` suffix and logged
  - [ ] `lateness` variable is bounded `[0, horizon]`; penalty weight scales `10^(6-priority)`
- **Dependencies:** None
- **Status:** Implemented — pending objective weight calibration (see Q1 in research)

#### Feature 2: Resource Allocation with Affinity Penalties (`build_resource_allocations`)

- **Description:** For each task × compatible machine, create `NewBoolVar`, `NewOptionalIntervalVar`, and add per-roll yarn-swap penalty to objective; enforce `AddExactlyOne` assignment; add machine-activation penalty
- **User Value:** Solver prefers machines already threaded with matching yarn, minimizing changeover time
- **Acceptance Criteria:**
  - [ ] Yarn penalty calculated per-roll: `PENALTY_PER_ROLL_SWAP × swaps_needed`
  - [ ] Cold-start penalty applied when `curr_color_str == ""`
  - [ ] Machine activation penalty (`PENALTY_ACTIVATE_RESOURCE = 50`) added per activated non-labor resource
  - [ ] Contiguous bounding-box enforced per PO on each machine (no idle gaps within a PO's knitting block)
- **Dependencies:** Feature 1
- **Status:** Implemented

#### Feature 3: NoOverlap + Unavailability Routing (`apply_routing_constraints`)

- **Description:** For each resource, collect all optional intervals and fixed unavailability windows; call `AddNoOverlap`
- **Acceptance Criteria:**
  - [ ] Unavailability windows are modeled as `NewFixedSizeIntervalVar`
  - [ ] No task is scheduled during any declared window
- **Dependencies:** Feature 2
- **Status:** Implemented

#### Feature 4: DAG Dependency Constraints (`apply_dependency_constraints`)

- **Description:** Apply `task.start >= parent.end` from `final_depends_on`; infer Knitting→Linking ordering for older payload versions that lack `wait_offsets`
- **Acceptance Criteria:**
  - [ ] All `final_depends_on` edges resolved through `task_translation_map`
  - [ ] Unresolvable deps log a warning, not a crash
  - [ ] Inferred K→L deps skipped for tasks that already have `wait_offsets`
- **Dependencies:** Feature 1
- **Status:** Implemented

#### Feature 5: Pipeline Offset Constraints (`apply_batch_offset_constraints`)

- **Description:** Apply `task.start >= batch.start + offset` from `WaitOffsets` dict — models pipelined batch production where Linking can begin before the full Knitting batch completes
- **Acceptance Criteria:**
  - [ ] All `WaitOffsets` entries resolved through `task_translation_map`
  - [ ] Multiple batch dependencies per task supported (dict iteration)
- **Dependencies:** Feature 1, Feature 4
- **Status:** Implemented — makespan impact vs. strict end-dependency not yet benchmarked (Q3)

#### Feature 6: Workforce / Factory Capacity (`build_workforce_constraints`)

- **Description:** Collect all `knitting` operation intervals + `capacity_block` ghost tasks; call `AddCumulative(intervals, demands, MAX_FACTORY_MACHINES)`
- **Acceptance Criteria:**
  - [ ] `capacity_block` tasks' `demand` field correctly reduces available machine slots
  - [ ] Cumulative constraint only added when at least one knitting interval exists
- **Dependencies:** Feature 2
- **Status:** Implemented — ghost-task vs. boolean-exclusion performance not yet compared (Q2)

#### Feature 7: Objective Definition + Solver Execution

- **Description:** `model.Minimize(sum(objective_terms))`; configure solver with `max_time_in_seconds`, `relative_gap_limit=0.01`, `num_search_workers=8`, and fixed `random_seed` (determinism)
- **Acceptance Criteria:**
  - [ ] `random_seed` is set and documented in `SolverConfig`
  - [ ] Solver stops at 1% gap or time-box, whichever comes first
  - [ ] Status `OPTIMAL` or `FEASIBLE` leads to result extraction; else returns `{"status": "infeasible"}`
- **Dependencies:** Features 1–6
- **Status:** Implemented — `random_seed` not yet wired from config (gap in determinism guarantee)

#### Feature 8: Result Extraction + Bottleneck Diagnostics (`extract_results`)

- **Description:** Walk `task_vars`; resolve selected machine from literals; emit `assignments[]` and `overloads[]` with `root_cause_code` and `bottleneck_resource_id`
- **Acceptance Criteria:**
  - [ ] `DUMMY_` and `capacity_block` tasks filtered before webhook callback
  - [ ] Each late task emits an overload entry with `delay_minutes` and `bottleneck_resource_id`
  - [ ] `root_cause_code` distinguishes `MACHINE_OVERLOAD`, `PINNED_TASK_CONFLICT`, `CAPACITY_FULL` (Q4)
- **Dependencies:** Feature 7
- **Status:** Partially implemented — `root_cause_code` currently hardcoded to `"CAPACITY_FULL"`

---

### Should Have (P1)

| Feature | Rationale |
|---------|-----------|
| Deterministic `random_seed` wired from `SolverConfig` | Closes the last gap in identical-output guarantee |
| Dynamic objective weight calibration (Q1) | Prevents search stagnation as job count scales |
| Structured root-cause classifier (Q4) | Enables Go UI to suggest specific remediation actions |
| Go-side payload preprocessing for unavailability aggregation (Q2) | Reduces CP-SAT variable count at scale |

### Could Have (P2)

| Feature | Rationale |
|---------|-----------|
| Benchmark harness (200/500/1000 tasks) | Validates ghost-task vs. boolean approach (Q2) |
| Warm-start from greedy initial solution | Speeds convergence for large instances |
| `DROPPED` status for tasks with no feasible slot | Richer diagnostics beyond LATE |

### Out of Scope (Won't Have for MVP)

| Feature | Why Excluded |
|---------|--------------|
| Alternative solvers (Gurobi, CPLEX) | Research scope explicitly excludes |
| Real-time MES/IoT integration | Not in current architecture |
| UI/UX rendering | Owned by Go frontend |
| Database access from Python | Stateless workers by design |
| Inventory / BOM management | Separate domain |

---

## Non-Functional Requirements

### Performance

- **Solve Time:** ≤ `max_search_time` (config, default 300s); target 60s for ≤ 200 tasks
- **RAM:** ≤ 8 GB per worker (hard infrastructure limit)
- **Optimality Gap:** ≤ 1% (enforced via `relative_gap_limit`)
- **Throughput:** Stateless workers — horizontal scale via Celery worker count

### Reliability & Determinism

- **Deterministic Output:** Fixed `num_search_workers` + `random_seed` → identical assignments for identical payloads
- **No Crashes on Bad Input:** Unknown `task_id` in deps, missing resources → logged warning, not exception
- **Infeasibility Handling:** Always returns structured `{"status": "infeasible", "assignments": [], "overloads": []}` — never HTTP 500 for solver failures

### Security

- **Network:** Private VPC only (port 8083); no public exposure
- **Auth:** Go backend is the sole caller; no user-facing auth layer needed for MVP
- **No Secrets in Payload:** `SolverPayload` contains no PII or credentials

### Scalability

- **Task Scale:** Target 200–1000 tasks per solve call
- **Worker Scale:** Stateless Celery workers; Redis as broker; horizontal scaling supported
- **Payload Size:** No DB access from Python — all context must be in the JSON payload

---

## Quality Standards

### Code Quality

- **Type Annotations:** All public methods typed; `Dict[str, Any]` acceptable at JSON boundaries
- **Architecture:** `Engine` orchestrates, `TaskModelBuilder` owns model logic — no CP-SAT calls outside `builder.py`
- **Error Handling:** CP-SAT failures return structured dicts; no bare `except Exception` swallowing
- **Integer Math:** All CP-SAT variables are integer Virtual Minutes — no floats in model

### What This Project Will NOT Accept

- Non-deterministic solver output (missing `random_seed`)
- Hardcoded penalty constants without justification comments
- Silent infeasibility (any infeasible call must return a structured response)
- New features touching the model builder without updating corresponding tests

---

## Success Metrics

### North Star Metric

**Schedule Quality Rate:** % of solve calls that return `FEASIBLE`/`OPTIMAL` within time-box with 0 hard-constraint violations.

### OKRs for MVP (First 90 Days)

**Objective 1:** Achieve production-grade determinism
- KR1: `random_seed` wired and documented — identical input → byte-identical output in 100% of regression tests
- KR2: Zero reports of non-deterministic schedules from Go backend team

**Objective 2:** Improve bottleneck diagnostics
- KR1: `root_cause_code` returns one of {`MACHINE_OVERLOAD`, `WORKFORCE_SHORTAGE`, `PINNED_TASK_CONFLICT`, `CAPACITY_FULL`} — not always `CAPACITY_FULL`
- KR2: Go UI can render a machine-reassignment suggestion for ≥ 80% of LATE tasks

**Objective 3:** Validate solver performance at scale
- KR1: Benchmark results documented for 200 / 500 / 1000 task payloads
- KR2: Solve time ≤ 60s for 200-task instances

### Metrics Framework

| Category | Metric | Target | Measurement |
|----------|--------|--------|-------------|
| Reliability | Feasible solve rate | > 95% of valid payloads | Celery task logs |
| Performance | P95 solve time (200 tasks) | < 60s | Engine timing logs |
| Correctness | No hard-constraint violations | 0 | Regression test suite |
| Determinism | Identical output on replay | 100% | Replay test harness |
| Diagnostics | Specific root_cause_code | > 80% specificity | Overload log analysis |

---

## Constraints & Assumptions

### Constraints

- **Budget:** Infrastructure only — no licensing costs (OR-Tools is Apache 2.0)
- **Runtime:** Max 8 GB RAM per Celery worker; stateless
- **OR-Tools Version:** Pinned at v9.8+ — no migration path in scope
- **Python:** 3.9+
- **Time-box:** 60s–5min configurable via `SolverConfig.max_search_time`
- **Network:** Private VPC, port 8083; Go backend at port 8082

### Assumptions

- Go backend sends valid DAGs (cycle detection is Go's responsibility via Pydantic pre-check)
- All time values are integer Virtual Minutes from a shared epoch
- `max_factory_machines` in `SolverConfig` is the authoritative machine cap
- `capacity_block` tasks' `demand` field accurately reflects the number of machines to reserve

### Open Questions

- Should `wait_offsets` constraints be hard or soft (penalized) when factory is overloaded? (Q3 from research)
- What is the optimal penalty ratio `lateness_weight : activation_weight : affinity_weight` as a function of job/machine count? (Q1 from research)
- Does boolean-exclusion outperform ghost-task `AddCumulative` at 500+ tasks? (Q2 from research)

---

## Risk Assessment

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| Objective imbalance causes search stagnation at large scale | Medium | High | Implement dynamic weight calibration from Q1 research |
| Ghost-task `AddCumulative` blows RAM at 1000+ tasks | Medium | High | Benchmark Q2 approaches; fallback to boolean-exclusion |
| Non-deterministic output from thread scheduling | Low | High | Fix `random_seed` wiring before any load test |
| Go sends malformed `wait_offsets` references | Medium | Medium | Translation map + warning logs already in place |
| Infeasible models with no diagnostic context | Medium | High | Implement root-cause classifier (P1) |

---

## MVP Definition of Done

### Feature Complete

- [ ] All 8 P0 features implemented and passing unit tests
- [ ] `random_seed` wired from `SolverConfig` (determinism gap closed)
- [ ] `root_cause_code` distinguishes at least 3 failure modes

### Quality Assurance

- [ ] Regression test with 3 known payloads produces byte-identical output on 5 consecutive runs
- [ ] No solve call returns HTTP 500 for any structurally valid payload
- [ ] All penalty constants documented with business justification inline

### Documentation

- [ ] `SolverConfig` fields documented in `request_schema.py`
- [ ] `CompletedJobResultPayload` contract documented in `response_schema.py`
- [ ] Research questions Q1–Q4 addressed in `docs/` with implementation notes

### Release Ready

- [ ] Celery worker starts cleanly in Docker with `REDIS_URL` and `WEBHOOK_URL` env vars
- [ ] Health endpoint returns 200
- [ ] Logs written to `/logs/scheduling_*.log` per run

---

## Appendices

### A. Current Codebase State

| Module | Status | Gap |
|--------|--------|-----|
| `builder.py` — `build_time_variables` | Complete | Objective weight calibration |
| `builder.py` — `build_resource_allocations` | Complete | Penalty constants need tuning |
| `builder.py` — `build_workforce_constraints` | Complete | Ghost-task performance unvalidated |
| `builder.py` — `apply_routing_constraints` | Complete | — |
| `builder.py` — `apply_dependency_constraints` | Complete | — |
| `builder.py` — `apply_batch_offset_constraints` | Complete | Makespan impact unvalidated |
| `builder.py` — `define_objective` | Complete | `random_seed` missing |
| `builder.py` — `extract_results` | Partial | `root_cause_code` hardcoded |
| `model.py` — `Engine.solve` | Complete | `random_seed` not wired |
| `schemas/request_schema.py` | Complete | — |
| `schemas/response_schema.py` | Complete | — |

### B. Research Questions → Implementation Mapping

| Research Q | Owner Module | Priority |
|------------|--------------|----------|
| Q1: Objective calibration | `builder.py` `define_objective` | P1 |
| Q2: Workforce capacity modeling | `builder.py` `build_workforce_constraints` | P1 |
| Q3: Pipeline offsets vs. strict deps | `builder.py` `apply_batch_offset_constraints` | P1 |
| Q4: Bottleneck root-cause extraction | `builder.py` `extract_results` | P1 |

---

*PRD Version: 1.0*
*Next Review: 2026-05-01*
*Owner: Operations Research Engineer*
*Stack: Python 3.9+, OR-Tools CP-SAT v9.8+, FastAPI, Celery + Redis*
*Status: Ready for Technical Design*
