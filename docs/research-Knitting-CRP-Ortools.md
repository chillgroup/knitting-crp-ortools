## Deep Research Request: APS Solver Engine — OR-Tools CP-SAT (builder.py)

<context>
I'm an Operations Research Engineer working on an enterprise-grade
Advanced Planning and Scheduling (APS) system for a textile manufacturing
factory. The core solver module (builder.py) uses Google OR-Tools CP-SAT
(Python 3.9+, pinned at v9.8+) to solve a Job Shop Scheduling problem
modeled as a DAG (Directed Acyclic Graph).

System flow: Go backend prepares and sends a JSON Payload →
Python (FastAPI + Celery worker) builds mathematical model (variables,
constraints, objective) → CP-SAT solver runs → Python returns a JSON
result (CompletedJobResultPayload) with schedule assignments, overloads,
and bottleneck diagnostics back to Go via HTTP callback.

**Technical Context:**
- Stack: Python 3.9+, OR-Tools CP-SAT v9.8+ (pinned), FastAPI, Celery +
  Redis, Go backend, Pydantic validation
- Infrastructure: Stateless Python workers (max 8GB RAM), isolated Private
  VPC (port 8083), no DB access from Python
- Hard constraints: max_search_time 60s–5min, deterministic output
  (fixed num_search_workers + random_seed), integer-only math
  (Virtual Minutes), Pydantic DAG cycle detection on all payloads
- Business context: Enterprise internal system — correctness and
  determinism are non-negotiable
</context>

<instructions>

### Research Objectives:
Answer these 4 core algorithmic questions with mathematical rigor,
benchmarked code examples, and actionable implementation guidance
for builder.py:

---

### Question 1 — Objective Function Calibration
"How can the CP-SAT objective function be mathematically calibrated
to optimally balance soft lateness penalties against machine activation
and setup/affinity changeovers without causing search stagnation?"

Investigate:
- Normalization strategies for multi-objective penalty functions in CP-SAT:
  static coefficients vs. dynamic (computed from input data distribution)
- Mathematical frameworks from OR literature (Weighted Job Shop,
  Earliness-Tardiness problems) for calibrating penalty ratios
- How coefficient imbalance (e.g., lateness >> setup) drives the solver
  toward local optima or infeasible regions — with concrete examples
- Techniques to avoid search stagnation: linearization tricks, symmetry
  breaking, warm-start from a greedy initial solution
- Recommended formula pattern: how to compute lateness_weight,
  activation_weight, and affinity_weight relative to each other as a
  function of job count, machine count, and planning horizon

---

### Question 2 — Workforce Capacity Block Modeling
"What is the impact of utilizing virtual capacity_block tasks within a
Cumulative constraint on solver performance when modeling dynamic,
shift-based workforce limitations?"

Investigate:
- Detailed behavior of AddCumulative when intervals array is inflated
  with hundreds/thousands of ghost (capacity_block) tasks: search tree
  size, propagation cost, RAM usage
- Head-to-head comparison of two modeling approaches:
    Option A: AddCumulative + ghost tasks (demand > 0) for unavailable
              windows
    Option B: Boolean availability arrays + OnlyEnforceIf to exclude
              intervals during off-shift periods
- Benchmarks (solve time, RAM, optimality gap) for each approach at
  scale: 200 tasks, 500 tasks, 1000+ tasks
- Preprocessing strategies: how Go should aggregate overlapping
  unavailability windows into minimal Unavailability Blocks before
  sending the Payload, and the mathematical threshold at which
  aggregation yields measurable performance gains
- Best practices for modeling shift-break patterns in CP-SAT without
  exploding the variable count

---

### Question 3 — Pipelined Production (wait_offsets) and Makespan
"How effectively do wait_offsets constraints model continuous, pipelined
batch production (e.g., Knitting → Linking) compared to strict
end-to-start precedence, and how does this affect makespan?"

Investigate:
- Formal comparison of two DAG edge types:
    Strict: task_B.start >= task_A.end
    Offset: task_B.start >= task_A.start + offset
  — and their effect on the solver's constraint propagation and makespan
- Mathematical proof or empirical evidence that fractional offset
  constraints reduce total makespan for pipelined batch production
- Bottleneck/starvation risk: conditions under which downstream
  (Linking) stage outpaces upstream (Knitting), creating work starvation
  — and how to detect or prevent this in the model
- Whether wait_offsets should be Hard Constraints or Soft Constraints
  (penalized in objective) when the factory is overloaded — and the
  trade-offs for schedule feasibility
- DAG complexity analysis: Big-O of building the offset-enriched DAG
  as a function of number of jobs, stages, and offset density

---

### Question 4 — Bottleneck / Root Cause Extraction
"By what deterministic mechanism can the solver isolate and extract the
precise bottleneck resource (root cause) when an order is forced into
LATE or DROPPED status due to conflicting hard constraints?"

Investigate:
- CP-SAT solution inspection API after solver.Solve(): which methods
  (Value(), ObjectiveValue(), interval variable states) reliably expose
  the binding constraints that forced lateness > 0
- Heuristic ruleset design for post-solve root cause classification.
  Provide a decision-tree or priority-ordered rule set that assigns:
    root_cause_code ∈ {MACHINE_OVERLOAD, WORKFORCE_SHORTAGE,
                       PINNED_TASK_CONFLICT, AFFINITY_CONFLICT, ...}
    bottleneck_resource_id → specific machine_id or worker_group_id
- How to distinguish between: (a) order late because of machine
  capacity, (b) order late because a higher-priority pinned task
  displaced it, (c) order dropped because no feasible slot exists
  within the time box
- Architecture decision: should extraction logic live in Python
  (post-solve interval scan) or Go (post-callback result comparison)?
  Pros/cons with concrete reasoning
- Callback result structure: what fields must CompletedJobResultPayload
  carry in overloads[] to give Go enough context to generate actionable
  UI suggestions (e.g., bottleneck_resource_id for machine reassignment)

---

### Scope Definition:
- **Include:** CP-SAT constraint API deep-dives, objective tuning math,
  benchmarked implementation options (A/B), post-solve inspection,
  JSON data contract design, Pydantic validation patterns for DAG
  cycle detection, Go-side preprocessing recommendations
- **Exclude:** Alternative solvers (Gurobi, CPLEX, Genetic Algorithms),
  UI/UX rendering, inventory/DB management, real-time MES/IoT
  integration, commercial APS product comparisons

### Depth Requirements:
- Technical Architecture: Comprehensive
- Implementation Options: Deep (code examples + benchmarks required)
- Cost Analysis: Deep (RAM/CPU profiling at scale + objective cost math)
- Market/Competitor Analysis: Surface only
- Source Priority: Official OR-Tools docs → GitHub (google/or-tools) →
  OR academic papers → Case studies → StackOverflow/forums

### Required Analysis:
- Benchmarks: solve time + RAM + optimality gap for each modeled
  approach at 200 / 500 / 1000+ tasks
- Big-O complexity analysis of DAG construction with offsets
- Recommended CP-SAT parameter configuration for enterprise time-box
  (num_search_workers, random_seed, max_time_in_seconds)
- Determinism guarantee: how to ensure identical input → identical
  output across CP-SAT versions and thread counts

</instructions>

<output_format>
- Provide detailed technical findings with Python code examples
  (using OR-Tools CP-SAT API)
- Include architecture diagrams in Mermaid.js or structured ASCII
- **Cite sources with URLs and access dates** for each major finding
- Use tables for benchmarks and comparisons
- **Explicitly note where sources disagree** or behavior is
  version-dependent (CP-SAT API changes between major versions)
- Include pros/cons for each major implementation option
- For each Question, end with a concrete "Recommendation" block:
  what to implement in builder.py and why
</output_format>
