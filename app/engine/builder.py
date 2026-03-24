import re
import logging
from typing import Dict, Any, List, Set

from ortools.sat.python import cp_model

logger = logging.getLogger(__name__)

# Affinity penalty constants — higher score = less preferred assignment
PENALTY_CHANGE_DESIGN: int = 500  # Must remove current mold and install a different one
PENALTY_COLD_START: int = 200     # Machine is idle and needs a mold installed from scratch
PENALTY_CHANGE_COLOR: int = 100   # Same mold but thread color must be changed
PENALTY_SETUP_COLOR: int = 50     # Machine has no thread color yet — light setup needed


class TaskModelBuilder:
    """
    Incrementally builds a CP-SAT scheduling model using the Builder Pattern.
    Each method returns `self` so calls can be chained fluently.

    Usage:
        builder = (
            TaskModelBuilder(config, resources, tasks, machine_states)
            .build_time_variables()
            .build_resource_allocations()
            .apply_routing_constraints()
            .apply_dependency_constraints()
            .apply_batch_offset_constraints()
            .define_objective()
        )
        status = solver.Solve(builder.model)
        result = builder.extract_results(solver, status)
    """

    def __init__(
        self,
        config: Dict[str, Any],
        resources: List[Dict[str, Any]],
        tasks: List[Dict[str, Any]],
        machine_states: Dict[str, Dict[str, str]],
    ) -> None:
        self.model = cp_model.CpModel()
        self.config = config
        self.tasks = tasks
        self.machine_states = machine_states

        # resource_map: id -> resource dict; "intervals" list is added during allocation
        self.resource_map: Dict[str, Dict[str, Any]] = {r["id"]: r for r in resources}

        # task_vars: task_id -> {start, end, literals, r_ids, due, ...}
        self.task_vars: Dict[str, Dict[str, Any]] = {}

        # Accumulated terms for the minimization objective
        self.objective_terms: List[Any] = []

        # Use config horizon, but guarantee it is large enough for all task durations
        total_duration = sum(int(t.get("duration", 0)) for t in self.tasks)
        config_horizon = int(self.config.get("horizon_minutes", 40320))
        self.horizon: int = max(config_horizon, total_duration + 5000)

        # Build the sub-task → batch-task translation map once during construction
        self.task_translation_map: Dict[str, str] = {}
        self._build_translation_map()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_translation_map(self) -> None:
        """
        Map every sub-task / original-order ID back to its parent batch task ID.
        This lets dependency resolution work even when Go sends raw sub-task IDs
        inside final_depends_on before batching IDs are available downstream.
        """
        for t in self.tasks:
            self.task_translation_map[t["task_id"]] = t["task_id"]
            if t.get("is_batch") and t.get("sub_tasks"):
                for sub in t["sub_tasks"]:
                    self.task_translation_map[sub["task_id"]] = t["task_id"]
                    if sub.get("original_order_id"):
                        self.task_translation_map[sub["original_order_id"]] = t["task_id"]

    def _compute_affinity_penalty(
        self,
        resource: Dict[str, Any],
        task_design: str,
        task_color: str,
    ) -> int:
        """
        Return the affinity penalty for assigning a task to a resource given its
        current design/color state.  A higher score means the assignment is less
        preferred, nudging the solver toward machines that are already set up.
        """
        curr_design = resource.get("design_item_id", "")
        curr_color = resource.get("color_config", "")
        penalty = 0

        if task_design:
            if curr_design == task_design:
                pass  # Perfect match — no penalty
            elif curr_design == "":
                # Machine is idle; installing a mold from scratch is cheaper than swapping
                penalty += PENALTY_COLD_START
            else:
                # Active machine running a different design — mold swap required
                penalty += PENALTY_CHANGE_DESIGN

        if task_color:
            if curr_color == task_color:
                pass  # Perfect match — no penalty
            elif curr_color == "":
                # Machine has no thread loaded yet — very light setup
                penalty += PENALTY_SETUP_COLOR
            else:
                # Thread color must be changed
                penalty += PENALTY_CHANGE_COLOR

        return penalty

    # ------------------------------------------------------------------
    # Builder steps
    # ------------------------------------------------------------------

    def build_time_variables(self) -> "TaskModelBuilder":
        """
        Create CP-SAT start/end/interval variables and a lateness penalty variable
        for every task that has at least one compatible resource.
        Tasks with no compatible resources are skipped with a warning so the
        solver never crashes on bad input (defensive programming).
        """
        for t in self.tasks:
            t_id = t["task_id"]
            compatible_ids = t.get("compatible_resource_ids", [])
            if not compatible_ids:
                logger.warning(f"⚠️ Task {t_id} has NO compatible resources — skipping.")
                continue

            duration = int(t["duration"])
            priority = int(t.get("priority", 5))
            due_at = int(t.get("due_at_min", self.horizon))
            start_after = int(t.get("start_after_min", 0))
            deps = t.get("final_depends_on") or []

            start_var = self.model.NewIntVar(0, self.horizon, f"start_{t_id}")
            end_var = self.model.NewIntVar(0, self.horizon, f"end_{t_id}")
            self.model.NewIntervalVar(start_var, duration, end_var, f"interval_{t_id}")
            self.model.Add(end_var == start_var + duration)

            if start_after > 0:
                self.model.Add(start_var >= start_after)

            # Weighted lateness: priority 1 tasks carry a much heavier penalty
            weight = 10 ** (6 - priority)
            lateness = self.model.NewIntVar(0, self.horizon, f"lat_{t_id}")
            self.model.Add(lateness >= end_var - due_at)
            self.objective_terms.append(lateness * weight * 100)
            # Secondary objective: prefer earlier starts (tie-breaking)
            # self.objective_terms.append(start_var)

            self.task_vars[t_id] = {
                "start": start_var,
                "end": end_var,
                "literals": [],        # populated in build_resource_allocations
                "r_ids": compatible_ids,
                "due": due_at,
                "original_order_id": t.get("original_order_id", ""),
                "group_id": t.get("group_id", ""),
                "depends_on": deps,
                "qty": t.get("qty", 0),
            }

            if deps:
                logger.info(f"   📌 {t_id} final_depends_on={deps}")

        return self

    def build_resource_allocations(self) -> "TaskModelBuilder":
        """
        For each task, create one Boolean selection variable per compatible resource.
        Optional interval variables hang off those Booleans so AddNoOverlap can
        enforce that a machine handles at most one task at a time.
        Affinity penalties are added to the objective to guide the solver toward
        machines that are already set up for the same design/color.
        NEW: Adds a heavy penalty for activating a new resource to minimize workforce.
        """
        # Dictionary to track which Boolean variables (literals) are assigned to each resource
        resource_usage_literals: Dict[str, List[cp_model.IntVar]] = {
            r_id: [] for r_id in self.resource_map.keys()
        }

        for t in self.tasks:
            t_id = t["task_id"]
            if t_id not in self.task_vars:
                continue  # Was skipped in build_time_variables (no compatible resources)

            task_design = t.get("design_item_id", "")
            task_color = t.get("color_config", "")
            tv = self.task_vars[t_id]
            start_var = tv["start"]
            end_var = tv["end"]
            duration = int(t["duration"])
            literals = []

            for r_id in tv["r_ids"]:
                if r_id not in self.resource_map:
                    continue

                is_selected = self.model.NewBoolVar(f"{t_id}_on_{r_id}")
                literals.append(is_selected)

                available_at = int(self.resource_map[r_id].get("available_at_min", 0))
                # print(f"Resource {r_id} available at {available_at} min")
                if available_at > 0:
                    self.model.Add(start_var >= available_at).OnlyEnforceIf(is_selected)
                
                # Track this literal for the resource activation penalty
                resource_usage_literals[r_id].append(is_selected)

                opt_interval = self.model.NewOptionalIntervalVar(
                    start_var, duration, end_var, is_selected, f"int_{t_id}_{r_id}"
                )
                # Accumulate intervals per resource for NoOverlap constraint
                self.resource_map[r_id].setdefault("intervals", []).append(opt_interval)

                penalty = self._compute_affinity_penalty(
                    self.resource_map[r_id], task_design, task_color
                )
                if penalty > 0:
                    # Penalise the objective only when this resource is actually selected
                    self.objective_terms.append(is_selected * penalty)

            # Exactly one resource must be selected for every scheduled task
            self.model.AddExactlyOne(literals)
            tv["literals"] = literals

        # ------------------------------------------------------------------
        # NEW: Resource Activation Penalty (Minimize Number of Workers/Machines)
        # ------------------------------------------------------------------
        PENALTY_ACTIVATE_RESOURCE = 50000  # Make this very high to prioritize fewer workers

        for r_id, assigned_literals in resource_usage_literals.items():
            if not assigned_literals:
                continue # No tasks can even run on this resource
            
            # Create a boolean variable that is True IF AND ONLY IF this resource is used
            is_resource_activated = self.model.NewBoolVar(f"activated_{r_id}")
            
            # Constraint: If ANY task is assigned to this resource (literal == 1),
            # then is_resource_activated MUST be 1.
            self.model.AddMaxEquality(is_resource_activated, assigned_literals)
            
            # Add the massive penalty to the objective function
            self.objective_terms.append(is_resource_activated * PENALTY_ACTIVATE_RESOURCE)

        return self

    def apply_routing_constraints(self) -> "TaskModelBuilder":
        """
        Enforce that no two tasks overlap on the same resource, and that tasks
        cannot be placed inside declared unavailability windows.
        """
        for r_id, resource in self.resource_map.items():
            intervals = resource.get("intervals", [])
            for window in resource.get("unavailability", []):
                w_start, w_end = int(window["start"]), int(window["end"])
                if w_end > w_start:
                    unavail = self.model.NewFixedSizeIntervalVar(
                        w_start, w_end - w_start, f"unavail_{r_id}"
                    )
                    intervals.append(unavail)
            if intervals:
                self.model.AddNoOverlap(intervals)

        return self

    def apply_dependency_constraints(self) -> "TaskModelBuilder":
        """
        Apply two kinds of ordering constraints:
        1. Explicit: task.start >= parent.end  (from final_depends_on)
        2. Inferred: Linking tasks must wait for all Knitting batches of the same
           order to finish — used as a fallback when Go hasn't yet set
           wait_for_batch_task_id on older payload versions.
        """
        # 1. Explicit final_depends_on
        logger.info("\n📋 APPLYING DEPENDENCY CONSTRAINTS:")
        for t_id, tv in self.task_vars.items():
            for parent_id in tv["depends_on"]:
                actual = self.task_translation_map.get(parent_id, parent_id)
                if actual in self.task_vars:
                    logger.info(f"   ✅ DEP: {t_id} waits for END of {actual} (raw: '{parent_id}')")
                    self.model.Add(tv["start"] >= self.task_vars[actual]["end"])
                else:
                    logger.warning(
                        f"⚠️ Task '{t_id}' depends on '{parent_id}' "
                        f"(resolved: '{actual}') — not found in task_vars!"
                    )

        # 2. Inferred K→L fallback
        # Collect task IDs that are already handled by batch-offset constraints
        # so we don't add a redundant (and potentially weaker) end-dependency.
        tasks_with_offset: Set[str] = {
            t["task_id"]
            for t in self.tasks
            if t.get("wait_offsets") or t.get("WaitOffsets") or t.get("wait_for_batch_task_id") or t.get("WaitForBatchTaskID")
        }
        print(f"\n🔍 Tasks with batch offsets (will skip inferred K→L constraints): {tasks_with_offset}")
        for l_id, l_tv in self.task_vars.items():
            if l_id in tasks_with_offset:
                continue
            m_l = re.match(r"^L\d+-(.+)$", l_id)
            if not m_l:
                continue
            l_base = re.sub(r"(?:_b\d+)?(?:_SLICE_\d+)?$", "", m_l.group(1))

            k_batch_ids: Set[str] = set()
            for key, batch_id in self.task_translation_map.items():
                if key == batch_id:
                    continue
                km = re.match(r"^K\d+-(.+?)(?:_SLICE_\d+)?$", key)
                if not km:
                    continue
                k_base = re.sub(r"_b\d+$", "", km.group(1))
                if l_base == k_base and batch_id in self.task_vars:
                    k_batch_ids.add(batch_id)

            for k_batch_id in sorted(k_batch_ids):
                logger.info(f"   🔗 INFERRED: {l_id} waits for END of {k_batch_id}")
                self.model.Add(l_tv["start"] >= self.task_vars[k_batch_id]["end"])

            if not k_batch_ids:
                logger.warning(f"   ⚠️ No K batch found for L task '{l_id}' (base: '{l_base}')")

        return self

    def apply_batch_offset_constraints(self) -> "TaskModelBuilder":
        """
        Apply pipelining constraints: a downstream slice (e.g. Linking) can start
        once the upstream batch (e.g. Knitting) has advanced past a known offset.
        Now supports multiple batch dependencies via the 'wait_offsets' dictionary.

        Go backend provides:
            WaitOffsets — dictionary mapping BatchTaskID -> offset in minutes
        """
        logger.info("\n⏱  BATCH OFFSET CONSTRAINTS (WaitOffsets):")
        for t in self.tasks:
            t_id = t["task_id"]
            if t_id not in self.task_vars:
                continue

            wait_offsets = t.get("wait_offsets") or t.get("WaitOffsets") or {}

            if not wait_offsets:
                continue

            for raw_batch_id, offset in wait_offsets.items():
                actual_batch = self.task_translation_map.get(raw_batch_id, raw_batch_id)
                
                if actual_batch not in self.task_vars:
                    logger.warning(
                        f"   ⚠️ '{t_id}': wait_for_batch '{raw_batch_id}' "
                        f"(resolved: '{actual_batch}') not found — skipping."
                    )
                    continue

                offset_val = int(offset)
                logger.info(f"   ⏱  {t_id} waits for {actual_batch} at offset +{offset_val}")
                
                self.model.Add(
                    self.task_vars[t_id]["start"]
                    >= self.task_vars[actual_batch]["start"] + offset_val
                )

        return self

    def define_objective(self) -> "TaskModelBuilder":
        """
        Minimise the weighted sum of:
        - Lateness penalties (higher weight for higher-priority tasks)
        - Early-start preference (secondary tie-breaker)
        - Affinity penalties (prefer machines already set up for the same design/color)
        """
        self.model.Minimize(sum(self.objective_terms))
        return self

    # ------------------------------------------------------------------
    # Result extraction
    # ------------------------------------------------------------------

    def extract_results(
        self,
        solver: cp_model.CpSolver,
        status: int,
    ) -> Dict[str, Any]:
        """Extract assignments and overloads from the solved model."""
        if status not in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            logger.warning("❌ Infeasible solution")
            return {"status": "infeasible", "assignments": [], "overloads": []}

        logger.info(f"✅ Feasible! Objective value: {solver.ObjectiveValue()}")
        assignments = []
        overloads = []

        for t_id, tv in self.task_vars.items():
            start_val = solver.Value(tv["start"])
            end_val = solver.Value(tv["end"])

            selected_res = None
            for i, lit in enumerate(tv["literals"]):
                if solver.Value(lit) == 1:
                    selected_res = tv["r_ids"][i]
                    break

            if selected_res:
                is_late = end_val > tv["due"]
                assignments.append({
                    "task_id": t_id,
                    "machine_id": selected_res,
                    "start_time": start_val,
                    "end_time": end_val,
                    "group_id": tv.get("group_id", ""),
                    "order_id": tv.get("original_order_id", ""),
                    "quantity": tv.get("qty", 0),
                    "status": "LATE" if is_late else "ON_TIME",
                })
                if is_late:
                    overloads.append({
                        "task_id": t_id,
                        "order_id": tv.get("original_order_id", ""),
                        "status": "LATE",
                        "delay_minutes": end_val - tv["due"],
                        "root_cause_code": "CAPACITY_FULL",
                        "bottleneck_resource_id": selected_res,
                        "quantity": tv.get("qty", 0),
                    })

        return {"status": "feasible", "assignments": assignments, "overloads": overloads}
