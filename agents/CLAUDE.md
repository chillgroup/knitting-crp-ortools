# 🏭 CAPACITY PLANNING SYSTEM - AI AGENT GUIDELINES

## 1. PROJECT OVERVIEW & OBJECTIVES
**System Name:** Advanced Planning and Scheduling (APS) System.
**Core Objective:** Optimize manufacturing capacity planning (Knitting, Washing, Linking, etc.) by efficiently assigning tasks to machines/workers while minimizing delays (lateness) and setup times (affinity penalties).
**Architecture:** - A 2-tier architecture: 
  1. **Golang Preprocessor (`go-fulfillment`)**: Ingests raw orders/machines, calculates time windows, batches interleaved tasks, handles data transformation, and prepares a mathematical blueprint.
  2. **Python Solver API (`cp-solver`)**: Consumes the JSON blueprint, translates it into a Google OR-Tools CP-SAT model, computes the optimal schedule, and returns feasible assignments and overloads.

---

## 2. WORKFLOW & INTEGRATION CONTRACT
- **Unidirectional Data Flow:** Go (Raw Data) -> Go (Preprocessor/Batching) -> JSON Payload -> Python (Solver) -> JSON Response -> Go (Post-processor/DB Save).
- **Strict Separation of Concerns:** - Go handles ALL business logic, batching algorithms, slice proportioning, and database state.
  - Python is strictly a "Math Engine". It must remain "dumb" regarding business rules. It only cares about intervals, capacities, constraints, and objective weights.

---

## 3. GOLANG PREPROCESSOR GUIDELINES (`capacityplanningutils`)

### 3.1. Code Structure & Responsibilities
- **Entry Point:** `Preprocessor.Process()` acts as the main Pipeline orchestrator.
- **Modularity:** Heavy transformations (e.g., `groupInterleavedTasks`, `sliceLargeTasks`, `generateDummyTasks`) must be isolated into independent, pure-like methods.

### 3.2. Coding Style & Naming Conventions
- **Language:** Go (1.20+).
- **Naming:** - Use `camelCase` for unexported variables/functions and `PascalCase` for exported structs/methods.
  - Acronyms should be all caps (e.g., `TaskID`, not `TaskId`).
  - Keep variable names short in narrow scopes (e.g., `t` for task, `m` for machine) but highly descriptive in broad scopes (e.g., `currentBatchDuration`).
- **Error Handling:** Avoid silent panics. Log errors properly or return them up the chain. For critical math mismatches (e.g., quantity loss during batching), explicit panics with detailed formatting are allowed during development to catch logical bugs early.

### 3.3. Design Patterns to Enforce
- **Pipeline Pattern:** Data should flow through discrete transformation steps.
  *Pattern: Input -> Split -> Classify -> Batch -> Resolve Dependencies -> Map Resources -> Output.*
- **Strategy Pattern (Implicit):** Different operations (Knitting vs. Washing) require different batching strategies. Keep these strategies cleanly separated.

### 3.4. Refactoring Goals for Agent
- Reduce cognitive complexity in massive loops (like `groupInterleavedTasks`). Break them down into helper functions (e.g., `calculateFairShareQty`, `fillBatch`).
- Ensure no floating-point precision loss during quantity division (`math.Floor`, `math.Ceil` must be used intentionally).

---

## 4. PYTHON SOLVER API GUIDELINES (`engine.py`)

### 4.1. Code Structure & Responsibilities
- **Core Library:** `ortools.sat.python.cp_model`.
- **Current State:** Monolithic `solve()` method. 
- **Target State:** The `Engine` class must be refactored using the **Builder Pattern** to construct the model step-by-step.

### 4.2. Coding Style & Naming Conventions
- **Language:** Python 3.10+.
- **Standard:** PEP-8 compliance. Use `Black` for formatting.
- **Typing:** Strict Type Hinting is mandatory (`from typing import Dict, List, Any, Optional`).
- **Naming:** - `snake_case` for variables, methods, and functions.
  - `PascalCase` for Classes.
  - `UPPER_SNAKE_CASE` for constants (e.g., `SETUP_DESIGN_PENALTY`).

### 4.3. Design Patterns to Enforce
- **Builder Pattern:** Break down the 200-line `solve()` method into logical private builders:
  - `_build_time_variables()`
  - `_build_resource_allocations()`
  - `_apply_affinity_penalties()`
  - `_apply_routing_constraints()`
  - `_define_objective()`
- **Factory Pattern (Optional):** If new operations require completely different constraint sets, use a constraint factory.

### 4.4. Refactoring Goals for Agent
- Extract the penalty scoring logic (`[NEW LOGIC V2] SETUP AFFINITY`) into its own method to improve readability.
- Maintain defensive programming: Use `.get('key', default)` extensively when parsing JSON payloads to prevent `KeyError` crashes.
- Clean up logging: Use structured logging or clear formatted prints (`print(f"🎯 ...")`) for debug traceablity.

---

## 5. AGENT INSTRUCTIONS (HOW YOU MUST BEHAVE)

1. **Think Before Coding:** Before modifying either codebase, outline the structural impact. If modifying Go's payload, you MUST update Python's Pydantic/Dictionary parsers simultaneously.
2. **Respect the Pipeline:** Do not put complex algorithmic slicing into Python. Python only reads bounds and constraints. Put business logic in Go.
3. **Immutability Concept:** When iterating over tasks in Go to create slices/batches, treat original arrays as immutable. Create and return new slices of pointers (`[]*SolverTask`) to avoid pointer mutation bugs.
4. **Commenting:** Explain the *WHY*, not the *WHAT*. (e.g., Explain *why* we use `math.Floor` instead of `math.Round` for batch quantities). Use English for all code comments.
5. **Idempotency:** Ensure that running the Go Preprocessor twice on the same data yields the exact same Solver Tasks.
6. **Zero Silent Failures:** If a task in Python has no compatible resources, log a clear warning and ignore it gracefully without crashing the solver.

---

## 6. BATCH OFFSET DEPENDENCY SYSTEM

### 6.1. Problem Statement
When Knitting tasks are batched with interleaving (e.g., `BATCH_F0-666_1` contains multiple slices completing at different offsets), downstream tasks like Linking must wait for the **specific completion offset** within the batch, not just the batch start.

### 6.2. Go Preprocessor Implementation
**Metadata Fields Added to `SolverTask`:**
```go
WaitForBatchTaskID string `json:"wait_for_batch_task_id"` // BatchTaskID to wait for
WaitForOffset      int64  `json:"wait_for_offset"`        // Offset in minutes within batch
```

**Linking Slice Dependency Logic:**
- `LINKING_SLICE_1` → waits for `BATCH_X` to reach `offset_1` (when `KNIT_SLICE_1` completes)
- `LINKING_SLICE_2` → waits for `BATCH_X` to reach `offset_2` AND waits for `LINKING_SLICE_1` to complete (sequential linking)
- Pattern continues for all slices

### 6.3. Python Solver Implementation Required

**Step 1: Parse Batch Offset Metadata**
```python
for t in self.tasks:
    task_id = t["task_id"]
    wait_batch_id = t.get("wait_for_batch_task_id", "")
    wait_offset = t.get("wait_for_offset", 0)
    
    if wait_batch_id and wait_batch_id in task_vars:
        # Create constraint: task can only start AFTER batch reaches offset
        self.model.Add(
            task_vars[task_id]["start"] >= 
            task_vars[wait_batch_id]["start"] + int(wait_offset)
        )
        print(f"🔗 {task_id} waits for {wait_batch_id} at offset +{wait_offset}")
```

**Step 2: Handle Regular Dependencies (remains unchanged)**
```python
for parent_id in t.get("final_depends_on", []):
    if parent_id in task_vars:
        # Regular dependency: wait for parent to finish
        self.model.Add(
            task_vars[task_id]["start"] >= task_vars[parent_id]["end"]
        )
```

**Critical Rules:**
1. Batch offset constraints MUST be applied **in addition to** regular dependency constraints
2. Sequential linking (LINKING_SLICE_2 depends on LINKING_SLICE_1) is handled via regular `final_depends_on`
3. If `wait_for_batch_task_id` is empty or batch not found, skip gracefully (defensive programming)

### 6.4. Example Flow
**Input Orders:**
- 20 Knitting items → split into 5 slices across 2 batches
- 20 Linking items → must wait for corresponding Knitting slices

**Go Output:**
```json
{
  "task_id": "LINKING_SLICE_1",
  "wait_for_batch_task_id": "BATCH_F0-666_1",
  "wait_for_offset": 25,
  "final_depends_on": ["BATCH_F0-666_1"]
},
{
  "task_id": "LINKING_SLICE_2",
  "wait_for_batch_task_id": "BATCH_F0-666_1",
  "wait_for_offset": 50,
  "final_depends_on": ["BATCH_F0-666_1", "LINKING_SLICE_1"]
}
```

**Python Constraints:**
```
LINKING_SLICE_1.start >= BATCH_F0-666_1.start + 25
LINKING_SLICE_2.start >= BATCH_F0-666_1.start + 50
LINKING_SLICE_2.start >= LINKING_SLICE_1.end
```

---

## 7. TESTING & VALIDATION
## 8. PROPOSED FOLDER STRUCTURE (cp-solver)
Cấu trúc này áp dụng Builder Pattern cho solver và tách biệt phần API giao tiếp với phần Worker xử lý tính toán nặng.

Plaintext
cp-solver/
├── app/
│   ├── __init__.py
│   ├── main.py                # Entry point cho FastAPI
│   ├── api/                   # REST Endpoints
│   │   ├── __init__.py
│   │   ├── v1/
│   │   │   ├── solver_route.py # Tiếp nhận yêu cầu lập lịch
│   │   │   └── health.py
│   ├── core/                  # Cấu hình hệ thống & Celery
│   │   ├── config.py          # Pydantic Settings (ENV vars)
│   │   └── celery_app.py      # Cấu hình Celery (Broker/Backend)
│   ├── schemas/               # Pydantic Models (Contract với Go)
│   │   ├── __init__.py
│   │   ├── request_schema.py  # Blueprint từ Go
│   │   └── response_schema.py # Giải pháp trả về cho Go
│   ├── tasks/                 # Celery Tasks
│   │   ├── __init__.py
│   │   └── solver_task.py     # Task bọc ngoài engine.solve()
│   └── engine/                # "Trái tim" Math Engine (CP-SAT)
│       ├── __init__.py
│       ├── builder.py         # Triển khai Builder Pattern
│       ├── model.py           # Định nghĩa Engine class
│       └── utils.py           # Helper cho việc xử lý thời gian/nhãn
├── tests/                     # Unit & Integration tests
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env
## 9. REFACTORING STRATEGY (PYTHON SIDE)
### 9.1. Áp dụng Builder Pattern trong engine/builder.py
Thay vì một hàm solve() khổng lồ, ta sẽ xây dựng model như sau:

Python
class TaskModelBuilder:
    def __init__(self, payload: SolverRequest):
        self.model = cp_model.CpModel()
        self.payload = payload
        self.variables = {}

    def build_time_variables(self):
        # Tạo start, end, interval vars cho từng task
        return self

    def apply_batch_offset_constraints(self):
        # Logic Section 6.3: Xử lý wait_for_offset
        for task in self.payload.tasks:
            # ... implementation ...
        return self

    def define_objective(self):
        # Tối ưu hóa lateness và affinity penalties
        return self

    def get_model(self):
        return self.model
### 9.2. Tích hợp Celery Worker
Do việc giải toán (solving) có thể mất từ vài giây đến vài phút, FastAPI không nên đợi (blocking).

Go gửi POST request tới FastAPI.

FastAPI đẩy payload vào Redis/RabbitMQ và trả về một job_id.

Celery Worker lấy task ra, khởi tạo Engine, chạy Solve().

Sau khi xong, Worker có thể WebHook ngược lại cho Go hoặc lưu vào DB để Go chủ động lấy.