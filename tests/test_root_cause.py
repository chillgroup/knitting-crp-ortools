"""
Root-cause classification tests.

Each test constructs a minimal payload that is designed to trigger a specific
root_cause_code, then asserts that at least one overload entry carries that code.

Fixture design rules:
  - PINNED_TASK_CONFLICT : a pinned task locks the only machine during the
    window the late task needs, forcing it into a late slot.
  - MACHINE_OVERLOAD     : one machine, two tasks whose combined duration
    exceeds the first task's deadline — the second is always late.
  - WORKFORCE_SHORTAGE   : max_factory_machines=1 with a capacity_block that
    fills the sole slot, leaving no room for the production task.
"""
import copy

import pytest

from app.engine.model import Engine
from tests.conftest import make_payload


# ── Helpers ────────────────────────────────────────────────────────────────

def _late_overloads(result):
    return [o for o in result.get("overloads", []) if o["status"] == "LATE"]


# ── PINNED_TASK_CONFLICT ───────────────────────────────────────────────────

def test_pinned_task_conflict():
    """
    Setup: one knitting machine (KM_00).
      - Task A: pinned to KM_00, runs [0, 300). Due at 500.
      - Task B: free, compatible only with KM_00. Due at 200.
        Task B can only start at 300 (after A), which is past its due=200.
    Expected: Task B is LATE with root_cause_code=PINNED_TASK_CONFLICT.
    """
    payload = {
        "job_id": "test_pinned_conflict",
        "config": {
            "horizon_minutes": 1000,
            "max_search_time": 15,
            "setup_time_minutes": 0,
            "max_factory_machines": 5,
            "random_seed": 42,
            "num_search_workers": 1,
        },
        "machines": [
            {"id": "KM_00", "type": "serial", "capacity": 1, "worker_req": 1,
             "routing": [{"operation": "knitting", "design_item_id": "", "duration": 0.0, "setup_time": 0.0}],
             "design_item_id": "", "color_config": ""},
        ],
        "resources": [
            {"id": "KM_00", "type": "serial", "capacity": 1, "operation": "knitting",
             "unavailability": [], "design_item_id": "", "color_config": "", "available_at_min": 0},
        ],
        "tasks": [
            # Task A — pinned [0, 300)
            {
                "task_id": "KA-pinned", "original_order_id": "ORDER_A", "group_id": "ORDER_A",
                "operation": "knitting", "qty": 10.0, "total_qty": 100.0, "priority": 1,
                "original_depends_on": [], "final_depends_on": [],
                "start_after_min": 0, "due_at_min": 500, "duration": 300,
                "is_slice": False, "parent_task_id": "", "internal_dep": "", "slice_index": 0,
                "is_batch": False, "sub_tasks": None,
                "design_item_id": "", "color_config": "",
                "compatible_resource_ids": ["KM_00"],
                "sub_task_completion_offsets": None, "WaitOffsets": None,
                "is_pinned": True,
                "pinned_machine_id": "KM_00",
                "pinned_start_time": 0,
                "pinned_end_time": 300,
                "demand": 1,
            },
            # Task B — free, must wait for KM_00 → starts at 300, due at 200 → LATE
            {
                "task_id": "KB-victim", "original_order_id": "ORDER_B", "group_id": "ORDER_B",
                "operation": "knitting", "qty": 10.0, "total_qty": 100.0, "priority": 3,
                "original_depends_on": [], "final_depends_on": [],
                "start_after_min": 0, "due_at_min": 200, "duration": 100,
                "is_slice": False, "parent_task_id": "", "internal_dep": "", "slice_index": 0,
                "is_batch": False, "sub_tasks": None,
                "design_item_id": "", "color_config": "",
                "compatible_resource_ids": ["KM_00"],
                "sub_task_completion_offsets": None, "WaitOffsets": None,
                "is_pinned": False, "pinned_machine_id": None,
                "pinned_start_time": None, "pinned_end_time": None,
                "demand": 1,
            },
        ],
    }

    result = Engine(payload).solve()
    assert result["status"] in ("feasible", "optimal")

    late = _late_overloads(result)
    assert any(o["task_id"] == "KB-victim" for o in late), (
        "KB-victim should be LATE — pinned task occupies the only machine"
    )
    victim = next(o for o in late if o["task_id"] == "KB-victim")
    assert victim["root_cause_code"] == "PINNED_TASK_CONFLICT", (
        f"Expected PINNED_TASK_CONFLICT, got {victim['root_cause_code']}"
    )


# ── MACHINE_OVERLOAD ───────────────────────────────────────────────────────

def test_machine_overload():
    """
    Setup: one knitting machine (KM_00).
      - Task A: duration=400, due=500.
      - Task B: duration=200, due=300. Only machine is KM_00.
        Combined duration 600 > K-long's due 500 — one task is always late.
        Solver prefers K-short first (less total lateness), so K-long ends at 600 > 500.
        The late task should see MACHINE_OVERLOAD (other task ran on same machine).
    """
    payload = {
        "job_id": "test_machine_overload",
        "config": {
            "horizon_minutes": 1000,
            "max_search_time": 15,
            "setup_time_minutes": 0,
            "max_factory_machines": 5,
            "random_seed": 42,
            "num_search_workers": 1,
        },
        "machines": [
            {"id": "KM_00", "type": "serial", "capacity": 1, "worker_req": 1,
             "routing": [{"operation": "knitting", "design_item_id": "", "duration": 0.0, "setup_time": 0.0}],
             "design_item_id": "", "color_config": ""},
        ],
        "resources": [
            {"id": "KM_00", "type": "serial", "capacity": 1, "operation": "knitting",
             "unavailability": [], "design_item_id": "", "color_config": "", "available_at_min": 0},
        ],
        "tasks": [
            {
                "task_id": "K-long", "original_order_id": "ORDER_A", "group_id": "ORDER_A",
                "operation": "knitting", "qty": 10.0, "total_qty": 100.0, "priority": 3,
                "original_depends_on": [], "final_depends_on": [],
                "start_after_min": 0, "due_at_min": 500, "duration": 400,
                "is_slice": False, "parent_task_id": "", "internal_dep": "", "slice_index": 0,
                "is_batch": False, "sub_tasks": None,
                "design_item_id": "", "color_config": "",
                "compatible_resource_ids": ["KM_00"],
                "sub_task_completion_offsets": None, "WaitOffsets": None,
                "is_pinned": False, "pinned_machine_id": None,
                "pinned_start_time": None, "pinned_end_time": None,
                "demand": 1,
            },
            {
                "task_id": "K-short", "original_order_id": "ORDER_B", "group_id": "ORDER_B",
                "operation": "knitting", "qty": 10.0, "total_qty": 100.0, "priority": 3,
                "original_depends_on": [], "final_depends_on": [],
                "start_after_min": 0, "due_at_min": 300, "duration": 200,
                "is_slice": False, "parent_task_id": "", "internal_dep": "", "slice_index": 0,
                "is_batch": False, "sub_tasks": None,
                "design_item_id": "", "color_config": "",
                "compatible_resource_ids": ["KM_00"],
                "sub_task_completion_offsets": None, "WaitOffsets": None,
                "is_pinned": False, "pinned_machine_id": None,
                "pinned_start_time": None, "pinned_end_time": None,
                "demand": 1,
            },
        ],
    }

    result = Engine(payload).solve()
    assert result["status"] in ("feasible", "optimal")

    late = _late_overloads(result)
    assert len(late) >= 1, "At least one task must be LATE on a single overloaded machine"
    for o in late:
        assert o["root_cause_code"] == "MACHINE_OVERLOAD", (
            f"Task {o['task_id']} got {o['root_cause_code']}, expected MACHINE_OVERLOAD"
        )


# ── WORKFORCE_SHORTAGE ─────────────────────────────────────────────────────

def test_workforce_shortage():
    """
    Setup: max_factory_machines=1.
      - capacity_block task: demand=1, pins the sole slot for [0, 400).
      - Knitting task: due=200, can only start at 400 → always LATE.
        At start_val=400, concurrent_knitting(0) + blocked_demand(0 after block ends)
        may not trigger WORKFORCE_SHORTAGE, but the capacity_block's overlap
        at start=0 would have been the cause.

    Alternate approach: block runs [0, 600), knitting due=300, starts at 600.
    At start_val=600, the block has ended so WORKFORCE_SHORTAGE won't fire
    at that instant — MACHINE_OVERLOAD also won't fire (no other task on KM_00).
    So we test CAPACITY_FULL here as the fallback.

    For a true WORKFORCE_SHORTAGE test: capacity_block runs [0, 400),
    max_factory_machines=1, knitting task has start_after_min=0, due=100.
    The knitting task is forced to start at 400 (after the block).
    At start_val=400, block has ended — won't hit the cap check.

    True trigger: capacity_block is STILL RUNNING when the knitting task starts.
    So: block runs [0, 800), knitting due=300, no other machine → starts at 800.
    At start_val=800, block [0,800) has other_end=800, other_start=0.
    Check: other_start(0) <= start_val(800) < other_end(800) → False (800 < 800 is False).
    Block ends exactly at 800, so the knitting task starts right after — no overlap.

    Correct fixture: block runs [100, 900), knitting due=200, starts at 900.
    At start_val=900: block [100,900) → 100 <= 900 < 900 → False again.

    The condition `other_start <= start_val < other_end` catches tasks RUNNING
    at start_val, meaning start < start_val < end (exclusive end).
    To trigger it: block must still be running when we start.
    But capacity_block forces knitting to start AFTER the block ends (NoOverlap).
    Therefore the block is never running when the knitting task starts.

    Conclusion: WORKFORCE_SHORTAGE fires when TWO OR MORE tasks are already
    running at our start time (not a single block).
    Correct fixture: 2 long-running tasks on 2 separate machines, max=2,
    plus our task needs a 3rd machine that doesn't exist.
    But with max=2 and 2 tasks running, concurrent=2 >= max=2 → WORKFORCE_SHORTAGE.
    """
    # Task A and Task B run concurrently on KM_00 and KM_01.
    # Task C needs KM_02 which doesn't exist, so it shares KM_00 or KM_01.
    # max_factory_machines=2 means when A+B run, the cap is full.
    # C starts after A or B finishes — at that point only 1 is running, cap not hit.
    # This proves WORKFORCE_SHORTAGE requires tasks to be STILL running at our start.
    #
    # Simpler: use a capacity_block that has demand=2 on a max=2 factory.
    # The knitting task needs 1 slot. Block demand=2 fills both slots [0, 400).
    # Knitting due=200, must wait until 400 → LATE.
    # At start_val=400: block [0,400) → 0 <= 400 < 400 is False (end is exclusive).
    #
    # The only reliable way: block end > start_val.
    # Knitting has start_after_min = 0, and there's only KM_00.
    # Block is a capacity_block (no machine), so it doesn't occupy KM_00 via NoOverlap.
    # Knitting can start at 0 IF block demand + knitting <= max.
    # If block demand=2 and max=2: knitting can't run (2+1 > 2).
    # So knitting is pushed to start=400 (after block ends).
    # At start_val=400 the block is done → still no WORKFORCE_SHORTAGE at that instant.
    #
    # CORRECT design: block runs [0, 500), knitting task is forced to start AT 500.
    # But CP-SAT will start it at exactly 500 (block ends, slot opens).
    # other_start(0) <= 500 < other_end(500) → 500 < 500 is False.
    #
    # The classifier checks WORKFORCE_SHORTAGE at the ACTUAL start of the late task.
    # By definition, if NoOverlap+Cumulative pushed the task to start after the block,
    # the block is no longer consuming capacity at that moment.
    #
    # REAL trigger: use TWO capacity_blocks with staggered ends, both demand=1, max=2.
    # Block1: [0, 600), Block2: [0, 400). Knitting due=200.
    # Knitting starts at 600 (must wait for both to clear).
    # At start_val=600: Block1 ends at 600 → 0 <= 600 < 600 is False.
    # Still doesn't trigger!
    #
    # True trigger requires the factory to STILL be at capacity when our task starts.
    # This happens when TWO knitting tasks are running at our start time
    # (not capacity_blocks, since they force us to wait until they end).
    #
    # Design: 3 knitting tasks, max=2, tasks A and B have long durations and
    # early start_after. Task C has a tight due. A and B occupy both slots,
    # C must wait → starts when A or B finishes. At that point only 1 running.
    # Still doesn't hit 2 concurrent at C's start.
    #
    # CONCLUSION: WORKFORCE_SHORTAGE fires when max_factory_machines tasks are
    # running at our actual start time. This happens when the solver is forced
    # (by dependencies or pinning) to start our task while the factory is still full.
    # In a well-formed schedule without such constraints, it's hard to construct
    # because the solver will try to start us as early as possible.
    #
    # Create it with a dependency: A and B run concurrently [0, 400).
    # C depends on A (final_depends_on=[A]) → C starts at 400.
    # But at start_val=400: B is [0,400) → 0 <= 400 < 400 is False.
    #
    # The check `other_start <= start_val < other_end` uses strict < on end.
    # Use `<=` on end to catch tasks that end exactly at our start:
    # But that would be a running task ending at our exact start, which is adjacent
    # (not concurrent) — semantically borderline.
    #
    # PRACTICAL FIX: change the check to `other_start <= start_val <= other_end`
    # (inclusive end) to catch the "factory was full right up to our start" case.
    # OR: trigger with a dependency that forces C to start mid-block.
    #
    # SIMPLEST CORRECT FIXTURE:
    # Use start_after_min to force the task to start while the factory is at capacity.
    # A: [0, 600), B: [0, 600) (pinned). C: start_after_min=300 (forced mid-block), due=100.
    # At start_val=300: A running (0<=300<600 ✓), B running (0<=300<600 ✓).
    # concurrent_knitting = 2 >= max_factory_machines=2 → WORKFORCE_SHORTAGE ✓

    payload = {
        "job_id": "test_workforce_shortage",
        "config": {
            "horizon_minutes": 1000,
            "max_search_time": 15,
            "setup_time_minutes": 0,
            "max_factory_machines": 2,
            "random_seed": 42,
            "num_search_workers": 1,
        },
        "machines": [
            {"id": "KM_00", "type": "serial", "capacity": 1, "worker_req": 1,
             "routing": [{"operation": "knitting", "design_item_id": "", "duration": 0.0, "setup_time": 0.0}],
             "design_item_id": "", "color_config": ""},
            {"id": "KM_01", "type": "serial", "capacity": 1, "worker_req": 1,
             "routing": [{"operation": "knitting", "design_item_id": "", "duration": 0.0, "setup_time": 0.0}],
             "design_item_id": "", "color_config": ""},
            {"id": "KM_02", "type": "serial", "capacity": 1, "worker_req": 1,
             "routing": [{"operation": "knitting", "design_item_id": "", "duration": 0.0, "setup_time": 0.0}],
             "design_item_id": "", "color_config": ""},
        ],
        "resources": [
            {"id": "KM_00", "type": "serial", "capacity": 1, "operation": "knitting",
             "unavailability": [], "design_item_id": "", "color_config": "", "available_at_min": 0},
            {"id": "KM_01", "type": "serial", "capacity": 1, "operation": "knitting",
             "unavailability": [], "design_item_id": "", "color_config": "", "available_at_min": 0},
            {"id": "KM_02", "type": "serial", "capacity": 1, "operation": "knitting",
             "unavailability": [], "design_item_id": "", "color_config": "", "available_at_min": 0},
        ],
        "tasks": [
            # Task A: pinned [0, 600) on KM_00
            {
                "task_id": "K-A", "original_order_id": "ORDER_A", "group_id": "ORDER_A",
                "operation": "knitting", "qty": 10.0, "total_qty": 100.0, "priority": 1,
                "original_depends_on": [], "final_depends_on": [],
                "start_after_min": 0, "due_at_min": 700, "duration": 600,
                "is_slice": False, "parent_task_id": "", "internal_dep": "", "slice_index": 0,
                "is_batch": False, "sub_tasks": None,
                "design_item_id": "", "color_config": "",
                "compatible_resource_ids": ["KM_00"],
                "sub_task_completion_offsets": None, "WaitOffsets": None,
                "is_pinned": True, "pinned_machine_id": "KM_00",
                "pinned_start_time": 0, "pinned_end_time": 600,
                "demand": 1,
            },
            # Task B: pinned [0, 600) on KM_01
            {
                "task_id": "K-B", "original_order_id": "ORDER_B", "group_id": "ORDER_B",
                "operation": "knitting", "qty": 10.0, "total_qty": 100.0, "priority": 1,
                "original_depends_on": [], "final_depends_on": [],
                "start_after_min": 0, "due_at_min": 700, "duration": 600,
                "is_slice": False, "parent_task_id": "", "internal_dep": "", "slice_index": 0,
                "is_batch": False, "sub_tasks": None,
                "design_item_id": "", "color_config": "",
                "compatible_resource_ids": ["KM_01"],
                "sub_task_completion_offsets": None, "WaitOffsets": None,
                "is_pinned": True, "pinned_machine_id": "KM_01",
                "pinned_start_time": 0, "pinned_end_time": 600,
                "demand": 1,
            },
            # Task C: forced to start at 300 via start_after_min, due=100 → LATE
            # At start_val=300: A[0,600) and B[0,600) are both running → concurrent=2 >= max=2
            {
                "task_id": "K-C", "original_order_id": "ORDER_C", "group_id": "ORDER_C",
                "operation": "knitting", "qty": 10.0, "total_qty": 100.0, "priority": 5,
                "original_depends_on": [], "final_depends_on": [],
                "start_after_min": 300, "due_at_min": 100, "duration": 50,
                "is_slice": False, "parent_task_id": "", "internal_dep": "", "slice_index": 0,
                "is_batch": False, "sub_tasks": None,
                "design_item_id": "", "color_config": "",
                "compatible_resource_ids": ["KM_02"],
                "sub_task_completion_offsets": None, "WaitOffsets": None,
                "is_pinned": False, "pinned_machine_id": None,
                "pinned_start_time": None, "pinned_end_time": None,
                "demand": 1,
            },
        ],
    }

    result = Engine(payload).solve()
    assert result["status"] in ("feasible", "optimal")

    late = _late_overloads(result)
    c_overload = next((o for o in late if o["task_id"] == "K-C"), None)
    assert c_overload is not None, "K-C must be LATE (due=100, start_after=300)"
    assert c_overload["root_cause_code"] == "WORKFORCE_SHORTAGE", (
        f"Expected WORKFORCE_SHORTAGE, got {c_overload['root_cause_code']}"
    )


# ── Fallback: CAPACITY_FULL ────────────────────────────────────────────────

def test_capacity_full_fallback():
    """
    A task is late but has no competitors — the only possible code is CAPACITY_FULL.
    Setup: one task, due_at_min shorter than its own duration → always late.
    No other task → no PINNED, no WORKFORCE, no MACHINE_OVERLOAD → CAPACITY_FULL.
    """
    payload = {
        "job_id": "test_capacity_full",
        "config": {
            "horizon_minutes": 1000,
            "max_search_time": 15,
            "setup_time_minutes": 0,
            "max_factory_machines": 5,
            "random_seed": 42,
            "num_search_workers": 1,
        },
        "machines": [
            {"id": "KM_00", "type": "serial", "capacity": 1, "worker_req": 1,
             "routing": [{"operation": "knitting", "design_item_id": "", "duration": 0.0, "setup_time": 0.0}],
             "design_item_id": "", "color_config": ""},
        ],
        "resources": [
            {"id": "KM_00", "type": "serial", "capacity": 1, "operation": "knitting",
             "unavailability": [], "design_item_id": "", "color_config": "", "available_at_min": 0},
        ],
        "tasks": [
            {
                "task_id": "K-impossible", "original_order_id": "ORDER_X", "group_id": "ORDER_X",
                "operation": "knitting", "qty": 10.0, "total_qty": 100.0, "priority": 3,
                "original_depends_on": [], "final_depends_on": [],
                "start_after_min": 0,
                "due_at_min": 50,     # due before duration completes
                "duration": 200,
                "is_slice": False, "parent_task_id": "", "internal_dep": "", "slice_index": 0,
                "is_batch": False, "sub_tasks": None,
                "design_item_id": "", "color_config": "",
                "compatible_resource_ids": ["KM_00"],
                "sub_task_completion_offsets": None, "WaitOffsets": None,
                "is_pinned": False, "pinned_machine_id": None,
                "pinned_start_time": None, "pinned_end_time": None,
                "demand": 1,
            },
        ],
    }

    result = Engine(payload).solve()
    assert result["status"] in ("feasible", "optimal")

    late = _late_overloads(result)
    assert len(late) == 1
    assert late[0]["root_cause_code"] == "CAPACITY_FULL", (
        f"Solo late task should fall back to CAPACITY_FULL, got {late[0]['root_cause_code']}"
    )
