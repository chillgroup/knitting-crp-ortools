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
        task_color_str: str, # Lúc này Go đang gửi chuỗi YarnConfig qua field 'color_config'
    ) -> int:
        """
        Tính toán điểm phạt (penalty) khi gán task vào một máy cụ thể.
        Điểm càng cao -> Setup càng lâu -> Solver càng né tránh máy này.
        """
        curr_design = resource.get("design_item_id", "")
        curr_color_str = resource.get("color_config", "")
        penalty = 0

        # ---------------------------------------------------------
        # 1. SETUP FILE THIẾT KẾ (Rất nhanh, chỉ cần nạp USB)
        # ---------------------------------------------------------
        PENALTY_CHANGE_DESIGN = 10 
        if task_design and curr_design and curr_design != task_design:
            penalty += PENALTY_CHANGE_DESIGN

        # ---------------------------------------------------------
        # 2. SETUP DÀN CỌC SỢI (Rất lâu, tốn công xỏ dây)
        # ---------------------------------------------------------
        PENALTY_COLD_START = 200     # Phạt máy trống, phải lên dàn cọc từ đầu
        PENALTY_PER_ROLL_SWAP = 100  # Phạt nặng cho MỖI MỘT cuộn sợi phải tháo/lắp

        if not task_color_str:
            return penalty # Không có thông tin sợi -> Bỏ qua

        if curr_color_str == task_color_str:
            # Dàn cọc giống HỆT NHAU -> Tuyệt vời, 0 điểm phạt sợi!
            pass
            
        elif curr_color_str == "":
            # Máy chưa có sợi, tốn công xỏ dây mới hoàn toàn
            # Đếm tổng số cuộn cần xỏ từ chuỗi (Ví dụ: "MAT_A:2|MAT_B:1" -> 3 cuộn)
            total_rolls = sum(int(item.split(':')[1]) for item in task_color_str.split('|') if ':' in item)
            
            # Xỏ mới từ đầu thường nhanh hơn việc tháo cái cũ ra rồi lắp cái mới vào
            penalty += PENALTY_COLD_START + (total_rolls * int(PENALTY_PER_ROLL_SWAP / 2))
            
        else:
            # CÓ SỰ LỆCH PHA -> TÍNH TOÁN SỐ CUỘN CẦN THAY THẾ
            def parse_yarns(y_str: str) -> Dict[str, int]:
                res = {}
                if not y_str: return res
                for item in y_str.split('|'):
                    if ':' in item:
                        mat, qty = item.split(':')
                        res[mat] = int(qty)
                    else:
                        res[item] = 1 # Fallback an toàn nếu chuỗi bị lỗi format
                return res

            # Parse chuỗi thành Dictionary. VD: {'MAT_BLK_05': 1, 'MAT_RED_01': 2}
            curr_yarns = parse_yarns(curr_color_str)
            task_yarns = parse_yarns(task_color_str)

            swaps_needed = 0
            
            # So sánh Sợi của Task vs Sợi đang cắm trên Máy
            for mat, target_qty in task_yarns.items():
                current_qty = curr_yarns.get(mat, 0)
                if target_qty > current_qty:
                    # Nếu thiếu bao nhiêu cuộn thì phải mất công chạy đi lấy và xỏ vào
                    swaps_needed += (target_qty - current_qty)
            
            # Cộng dồn hình phạt dựa trên số cuộn phải thao tác
            penalty += (swaps_needed * PENALTY_PER_ROLL_SWAP)

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
        # Deduplicate task_ids — Go backend may emit duplicate IDs for split segments.
        # Duplicate CP-SAT variable names cause silent constraint mis-binding and
        # double-add NoOverlap intervals on the same machine, making the model INFEASIBLE.
        seen_task_ids: Set[str] = set()
        for t in self.tasks:
            orig = t["task_id"]
            if orig in seen_task_ids:
                counter = 2
                candidate = f"{orig}_dup{counter}"
                while candidate in seen_task_ids:
                    counter += 1
                    candidate = f"{orig}_dup{counter}"
                t["task_id"] = candidate
                logger.warning(
                    f"⚠️ Duplicate task_id '{orig}' renamed → '{t['task_id']}' "
                    f"(pinned={t.get('is_pinned')}, start={t.get('pinned_start_time')}, end={t.get('pinned_end_time')})"
                )
            seen_task_ids.add(t["task_id"])

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

            # NẾU BỊ GHIM (PINNED)
            if t.get("is_pinned") and t.get("pinned_start_time") is not None and t.get("pinned_end_time") is not None:
                start_val = int(t["pinned_start_time"])
                end_val = int(t["pinned_end_time"])

                # Dùng biến hằng số (Constant), không cho Solver xê dịch
                start_var = self.model.NewConstant(start_val)
                end_var = self.model.NewConstant(end_val)
                
                # Không cần ép `end_var == start_var + duration` nữa vì nó đã cố định.

            # NẾU LÀ TASK MỚI (TỰ DO) — hoặc pinned nhưng thiếu thời gian ghim
            else:
                start_var = self.model.NewIntVar(0, self.horizon, f"start_{t_id}")
                end_var = self.model.NewIntVar(0, self.horizon, f"end_{t_id}")
                # Chỉ ép duration cho biến tự do
                self.model.Add(end_var == start_var + duration)
                
                if start_after > 0:
                    self.model.Add(start_var >= start_after)

            # Weighted lateness
            weight = 10 ** (6 - priority)
            lateness = self.model.NewIntVar(0, self.horizon, f"lat_{t_id}")
            self.model.Add(lateness >= end_var - due_at)
            self.objective_terms.append(lateness * weight * 100)

            self.task_vars[t_id] = {
                "start": start_var,
                "end": end_var,
                "literals": [],        
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
        resource_usage_literals: Dict[str, List[cp_model.IntVar]] = {
            r_id: [] for r_id in self.resource_map.keys()
        }

        for t in self.tasks:
            t_id = t["task_id"]
            if t_id not in self.task_vars:
                continue 

            task_design = t.get("design_item_id", "")
            task_color = t.get("color_config", "")
            tv = self.task_vars[t_id]
            start_var = tv["start"]
            end_var = tv["end"]
            
            # Nếu ghim, chỉ cần ghi đè danh sách r_ids thành 1 máy duy nhất trước khi vào vòng lặp
            if t.get("is_pinned"):
                tv["r_ids"] = [t["pinned_machine_id"]]

            literals = []

            for r_id in tv["r_ids"]:
                if r_id not in self.resource_map:
                    continue

                # Tạo biến is_selected CHUẨN (Chỉ tạo 1 lần)
                is_selected = self.model.NewBoolVar(f"{t_id}_on_{r_id}")
                literals.append(is_selected)

                # NẾU TASK BỊ GHIM -> ÉP BIẾN NÀY = 1
                if t.get("is_pinned"):
                    self.model.Add(is_selected == 1)

                available_at = int(self.resource_map[r_id].get("available_at_min", 0))
                # Skip available_at for pinned tasks — start_var is a NewConstant;
                # is_selected is forced to 1, so OnlyEnforceIf has no effect and
                # available_at > pinned_start would make the model INFEASIBLE.
                if available_at > 0 and not t.get("is_pinned"):
                    self.model.Add(start_var >= available_at).OnlyEnforceIf(is_selected)
                
                resource_usage_literals[r_id].append(is_selected)

                # OptionalIntervalVar dùng cho AddNoOverlap
                # Cần tính lại duration thực tế nếu đã ghim để tránh lỗi Infeasible
                pinned_start = t.get("pinned_start_time")
                pinned_end = t.get("pinned_end_time")
                is_fully_pinned = t.get("is_pinned") and pinned_start is not None and pinned_end is not None
                actual_duration = int(pinned_end) - int(pinned_start) if is_fully_pinned else int(t["duration"])
                
                opt_interval = self.model.NewOptionalIntervalVar(
                    start_var, actual_duration, end_var, is_selected, f"int_{t_id}_{r_id}"
                )
                self.resource_map[r_id].setdefault("intervals", []).append(opt_interval)

                penalty = self._compute_affinity_penalty(
                    self.resource_map[r_id], task_design, task_color
                )
                if penalty > 0:
                    self.objective_terms.append(is_selected * penalty)

            self.model.AddExactlyOne(literals)
            tv["literals"] = literals

        PENALTY_ACTIVATE_RESOURCE = 50000 
        PENALTY_ACTIVATE_LABOR = 0
        for r_id, assigned_literals in resource_usage_literals.items():
            if not assigned_literals:
                continue 
            is_labor = r_id.startswith("W_") 
            
            is_resource_activated = self.model.NewBoolVar(f"activated_{r_id}")
            self.model.AddMaxEquality(is_resource_activated, assigned_literals)

            if is_labor:
                self.objective_terms.append(is_resource_activated * PENALTY_ACTIVATE_LABOR)
            else:
                self.objective_terms.append(is_resource_activated * PENALTY_ACTIVATE_RESOURCE)

        # ------------------------------------------------------------------
        # NEW: GROUP CHUNKS OF THE SAME PO (SPAN CONTIGUOUS CONSTRAINT)
        # ------------------------------------------------------------------
        # 1. Gom nhóm các task Knitting theo Original Order ID
        po_knitting_groups: Dict[str, List[Dict]] = {}
        
        for t in self.tasks:
            # Lọc riêng khâu Knitting (Bạn có thể điều chỉnh điều kiện if này cho khớp với data của bạn)
            if t.get("operation", "").lower() == "knitting":
                po_id = t.get("original_order_id")
                if po_id:
                    po_knitting_groups.setdefault(po_id, []).append(t)

        # 2. Tạo ràng buộc cho từng nhóm
        for po_id, tasks_in_po in po_knitting_groups.items():
            if len(tasks_in_po) <= 1:
                continue # Đơn chỉ có 1 task thì không cần ép

            t_ids = [t["task_id"] for t in tasks_in_po]
            total_duration = sum(int(t["duration"]) for t in tasks_in_po)
            
            starts = [self.task_vars[tid]["start"] for tid in t_ids]
            ends = [self.task_vars[tid]["end"] for tid in t_ids]

            # --- RÀNG BUỘC 1: ÉP KHÔNG CÓ KHE HỞ (CONTIGUOUS) ---
            min_start = self.model.NewIntVar(0, self.horizon, f"min_start_po_{po_id}")
            max_end = self.model.NewIntVar(0, self.horizon, f"max_end_po_{po_id}")
            
            # min_start = min(tất cả các starts)
            self.model.AddMinEquality(min_start, starts)
            # max_end = max(tất cả các ends)
            self.model.AddMaxEquality(max_end, ends)
            
            # Độ rộng từ mẻ đầu đến mẻ cuối BẰNG ĐÚNG tổng thời gian chạy
            self.model.Add(max_end - min_start == total_duration)

            # --- RÀNG BUỘC 2: ÉP CHẠY CHUNG MỘT MÁY ---
            first_tid = t_ids[0]
            first_literals = self.task_vars[first_tid]["literals"]
            
            # So sánh các task còn lại với task đầu tiên
            for i in range(1, len(t_ids)):
                other_tid = t_ids[i]
                other_literals = self.task_vars[other_tid]["literals"]
                
                # Quét qua toàn bộ máy móc trong xưởng
                for r_id in self.resource_map.keys():
                    # Tìm cờ boolean "Task X chạy trên Máy Y"
                    lit_first = next((l for l in first_literals if l.Name().endswith(f"_on_{r_id}")), None)
                    lit_other = next((l for l in other_literals if l.Name().endswith(f"_on_{r_id}")), None)
                    
                    if lit_first is not None and lit_other is not None:
                        # Nếu task 1 chọn máy r_id, thì task i cũng PHẢI chọn máy r_id
                        self.model.Add(lit_first == lit_other)
                    elif lit_first is not None and lit_other is None:
                        # Logic phòng hờ: Nếu task i không thể chạy máy này, cấm luôn task 1 chạy máy này
                        self.model.Add(lit_first == 0)
                    elif lit_first is None and lit_other is not None:
                        self.model.Add(lit_other == 0)
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
