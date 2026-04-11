"""
Shared pytest fixtures and synthetic payload factory.

make_payload(n_orders, ...) generates a valid SolverPayload dict
without touching the filesystem. Tests import it directly.
"""
import random
from typing import Any, Dict, List

import pytest


def make_payload(
    n_orders: int = 5,
    n_knitting_machines: int = 5,
    n_linking_machines: int = 2,
    horizon_minutes: int = 10080,   # 1 week
    max_factory_machines: int = 5,
    max_search_time: int = 30,
    random_seed: int = 42,
    num_search_workers: int = 1,     # Default 1 for deterministic tests
    rng_seed: int = 0,               # Seed for the fixture generator itself
) -> Dict[str, Any]:
    """
    Build a synthetic SolverPayload dict (no DB, no files).

    Each order generates:
      - 1 Knitting task  (K1-ORDER_nnn)
      - 1 Linking task   (L1-ORDER_nnn)  with WaitOffsets → K task at half-duration

    All field names match SolverTask aliases (populate_by_name=True).
    """
    rng = random.Random(rng_seed)

    designs = ["DESIGN_A", "DESIGN_B", "DESIGN_C"]
    yarn_configs = [
        "MAT_WHT:2|MAT_BLK:1",
        "MAT_RED:3",
        "MAT_BLU:2|MAT_GRN:1",
        "MAT_YEL:1",
    ]

    knitting_ids = [f"KM_{i:02d}" for i in range(n_knitting_machines)]
    linking_ids  = [f"LM_{i:02d}" for i in range(n_linking_machines)]

    # ── Resources ──────────────────────────────────────────────────────────
    resources: List[Dict[str, Any]] = []
    resource_designs: Dict[str, str] = {}
    resource_colors: Dict[str, str] = {}

    for m_id in knitting_ids:
        design = rng.choice(designs)
        color  = rng.choice(yarn_configs)
        resource_designs[m_id] = design
        resource_colors[m_id]  = color
        resources.append({
            "id": m_id,
            "type": "serial",
            "capacity": 1,
            "operation": "knitting",
            "unavailability": [],
            "design_item_id": design,
            "color_config": color,
            "available_at_min": 0,
        })

    for m_id in linking_ids:
        resource_designs[m_id] = ""
        resource_colors[m_id]  = ""
        resources.append({
            "id": m_id,
            "type": "serial",
            "capacity": 1,
            "operation": "linking",
            "unavailability": [],
            "design_item_id": "",
            "color_config": "",
            "available_at_min": 0,
        })

    # ── Machines (current state for affinity scoring) ───────────────────────
    machines: List[Dict[str, Any]] = []
    for m_id in knitting_ids:
        machines.append({
            "id": m_id,
            "type": "serial",
            "capacity": 1,
            "worker_req": 1,
            "routing": [{"operation": "knitting", "design_item_id": resource_designs[m_id],
                         "duration": 0.0, "setup_time": 0.0}],
            "design_item_id": resource_designs[m_id],
            "color_config": resource_colors[m_id],
        })
    for m_id in linking_ids:
        machines.append({
            "id": m_id,
            "type": "serial",
            "capacity": 1,
            "worker_req": 1,
            "routing": [{"operation": "linking", "design_item_id": "",
                         "duration": 0.0, "setup_time": 0.0}],
            "design_item_id": "",
            "color_config": "",
        })

    # ── Tasks ───────────────────────────────────────────────────────────────
    tasks: List[Dict[str, Any]] = []
    for idx in range(n_orders):
        order_id     = f"ORDER_{idx:03d}"
        k_id         = f"K1-{order_id}"
        l_id         = f"L1-{order_id}"
        priority     = rng.randint(1, 5)
        k_dur        = rng.randint(120, 360)
        l_dur        = rng.randint(60, 180)
        due_at       = rng.randint(k_dur + l_dur + 120, horizon_minutes)
        design       = rng.choice(designs)
        color        = rng.choice(yarn_configs)
        k_compatible = rng.sample(knitting_ids, min(3, n_knitting_machines))
        l_compatible = rng.sample(linking_ids,  min(2, n_linking_machines))
        wait_offset  = k_dur // 2

        tasks.append({
            "task_id":                    k_id,
            "original_order_id":          order_id,
            "group_id":                   order_id,
            "operation":                  "knitting",
            "qty":                        float(rng.randint(10, 100)),
            "total_qty":                  float(rng.randint(100, 500)),
            "priority":                   priority,
            "original_depends_on":        [],
            "final_depends_on":           [],
            "start_after_min":            0,
            "due_at_min":                 due_at,
            "duration":                   k_dur,
            "is_slice":                   False,
            "parent_task_id":             "",
            "internal_dep":               "",
            "slice_index":                0,
            "is_batch":                   False,
            "sub_tasks":                  None,
            "design_item_id":             design,
            "color_config":               color,
            "compatible_resource_ids":    k_compatible,
            "sub_task_completion_offsets": None,
            "WaitOffsets":                None,
            "is_pinned":                  False,
            "pinned_machine_id":          None,
            "pinned_start_time":          None,
            "pinned_end_time":            None,
            "demand":                     1,
        })

        tasks.append({
            "task_id":                    l_id,
            "original_order_id":          order_id,
            "group_id":                   order_id,
            "operation":                  "linking",
            "qty":                        float(rng.randint(10, 100)),
            "total_qty":                  float(rng.randint(100, 500)),
            "priority":                   priority,
            "original_depends_on":        [k_id],
            "final_depends_on":           [],
            "start_after_min":            0,
            "due_at_min":                 due_at,
            "duration":                   l_dur,
            "is_slice":                   False,
            "parent_task_id":             "",
            "internal_dep":               "",
            "slice_index":                0,
            "is_batch":                   False,
            "sub_tasks":                  None,
            "design_item_id":             "",
            "color_config":               "",
            "compatible_resource_ids":    l_compatible,
            "sub_task_completion_offsets": None,
            "WaitOffsets":                {k_id: wait_offset},
            "is_pinned":                  False,
            "pinned_machine_id":          None,
            "pinned_start_time":          None,
            "pinned_end_time":            None,
            "demand":                     0,
        })

    return {
        "job_id": f"TEST_{n_orders}orders",
        "config": {
            "horizon_minutes":     horizon_minutes,
            "max_search_time":     max_search_time,
            "setup_time_minutes":  60,
            "max_factory_machines": max_factory_machines,
            "random_seed":         random_seed,
            "num_search_workers":  num_search_workers,
        },
        "machines":  machines,
        "resources": resources,
        "tasks":     tasks,
    }


# ── Shared pytest fixtures ──────────────────────────────────────────────────

@pytest.fixture(scope="session")
def payload_smoke():
    """10-task payload for fast smoke tests (5 orders)."""
    return make_payload(
        n_orders=5,
        n_knitting_machines=5,
        n_linking_machines=2,
        max_factory_machines=5,
        max_search_time=15,
    )


@pytest.fixture(scope="session")
def payload_200():
    """200-task payload (100 orders) — determinism baseline."""
    return make_payload(
        n_orders=100,
        n_knitting_machines=20,
        n_linking_machines=5,
        max_factory_machines=20,
        max_search_time=60,
        num_search_workers=1,   # Deterministic mode
        random_seed=42,
    )
