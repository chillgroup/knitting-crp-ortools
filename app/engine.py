from ortools.sat.python import cp_model
from typing import Dict, Any, List
import json

class Engine:


    def __init__(self, payload: Dict[str, Any]):
        # 1. Parse Config (keep for using other parameters)
        import json
        self.config = {}
        print("📥 Parsing config from payload...", payload)
        raw_config = payload.get("config", "{}")
        if isinstance(raw_config, str):
            try:
                self.config = json.loads(raw_config)
            except:
                self.config = {}
        else:
            self.config = raw_config

        # 2. [FINALIZED] Read Machines from Payload Root (As confirmed by forensic log)
        machines_data = payload.get("machines", []) 
        
        # DEBUG: Print ALL machines raw data
        print(f"📦 RECEIVED {len(machines_data)} MACHINES FROM PAYLOAD")
        for m in machines_data:
            if m.get("id") == "SK11":
                print(f"🔍 SK11 RAW DATA: {json.dumps(m, indent=2)}")
        
        # Map data
        self.machine_states = {}
        for m in machines_data:
            m_id = m.get("id")
            if m_id:
                # After fixing Go, these 2 fields will appear
                self.machine_states[m_id] = {
                    "current_design": m.get("design_item_id", ""),
                    "current_color": m.get("color_config", "")
                }

        # 3. Debug to see if Design exists (Hopefully will see it this time)
        if "SK11" in self.machine_states:
            st = self.machine_states["SK11"]
            print(f"🎯 SK11 in machine_states -> Design: '{st['current_design']}' | Color: '{st['current_color']}'")
        
        # Init Resources & Tasks
        self.resources = payload.get("resources", [])
        self.tasks = payload.get("tasks", [])
        
        # Debug resources
        for r in self.resources:
            if r.get("id") == "SK11":
                print(f"🎯 SK11 in resources -> Design: '{r.get('design_item_id', '')}' | Color: '{r.get('color_config', '')}'")

        self.model = cp_model.CpModel()
        self.solver = cp_model.CpSolver()

    def solve(self) -> Dict[str, Any]:
        if not self.tasks:
            return {"status": "feasible", "assignments": [], "overloads": []}

        # Setup Horizon
        total_duration = sum(int(t.get("duration", 0)) for t in self.tasks)
        config_horizon = int(self.config.get("horizon_minutes", 40320))
        horizon = max(config_horizon, total_duration + 5000)

        # Configure penalty points
        # Heavy penalty if changing Design (because changing mold takes longer than changing thread)
        SETUP_DESIGN_PENALTY = 500 
        # Light penalty if changing Color (same mold, only changing thread)
        SETUP_COLOR_PENALTY = 100  

        resource_map = {r["id"]: r for r in self.resources}
        task_vars = {}
        objective_terms = []

        # ---------------------------------------------------------
        # BUILD MODEL
        # ---------------------------------------------------------
        for t in self.tasks:
            t_id = t["task_id"]
            duration = int(t["duration"])
            priority = int(t.get("priority", 5))
            due_at = int(t.get("due_at_min", horizon))
            start_after = int(t.get("start_after_min", 0))
            
            # Get Task characteristics
            task_design = t.get("design_item_id", "")
            task_color = t.get("color_config", "") # Example: "RED", "BLUE"

            # A. Time variables
            start_var = self.model.NewIntVar(0, horizon, f"start_{t_id}")
            end_var = self.model.NewIntVar(0, horizon, f"end_{t_id}")
            self.model.NewIntervalVar(start_var, duration, end_var, f"interval_{t_id}")
            self.model.Add(end_var == start_var + duration)
            if start_after > 0:
                self.model.Add(start_var >= start_after)

            # B. Select machine
            literals = []
            compatible_ids = t.get("compatible_resource_ids", [])
            
            if not compatible_ids:
                print(f"⚠️ Task {t_id} has NO compatible resources.")
                continue

            for r_id in compatible_ids:
                if r_id not in resource_map: continue
                
                is_selected = self.model.NewBoolVar(f"{t_id}_on_{r_id}")
                literals.append(is_selected)
                
                # Create Interval
                opt_interval = self.model.NewOptionalIntervalVar(
                    start_var, duration, end_var, is_selected, f"int_{t_id}_{r_id}"
                )
                if "intervals" not in resource_map[r_id]:
                    resource_map[r_id]["intervals"] = []
                resource_map[r_id]["intervals"].append(opt_interval)

# --- [NEW LOGIC V2] SETUP AFFINITY (COMPATIBILITY) ---
                # Read current machine state from resource
                curr_resource = resource_map[r_id]
                curr_design = curr_resource.get("design_item_id", "")
                curr_color = curr_resource.get("color_config", "")

                penalty_score = 0
                
                # Configure penalty points
                PENALTY_CHANGE_DESIGN = 500  # Must remove old mold and install new one
                PENALTY_COLD_START    = 200  # Machine is idle, must install mold from scratch
                PENALTY_CHANGE_COLOR  = 100  # Same mold, but must change thread
                PENALTY_SETUP_COLOR   = 50   # Machine has no thread color, must thread it

                # 1. CHECK DESIGN
                if task_design:
                    if curr_design == task_design:
                        # ✅ MATCH: No penalty
                        pass
                    elif curr_design == "":
                        # ⚠️ COLD START: Machine is idle -> Light penalty due to setup cost
                        penalty_score += PENALTY_COLD_START
                    else:
                        # ❌ MISMATCH: Machine running different design -> Heavy penalty
                        penalty_score += PENALTY_CHANGE_DESIGN

                # 2. CHECK COLOR
                if task_color:
                    if curr_color == task_color:
                        # ✅ MATCH: No penalty
                        pass
                    elif curr_color == "":
                         # ⚠️ SETUP COLOR: Machine has no thread -> Very light penalty
                        penalty_score += PENALTY_SETUP_COLOR
                    else:
                        # ❌ MISMATCH: Machine running different color -> Penalty for thread change
                        penalty_score += PENALTY_CHANGE_COLOR
                
                # Add penalty to objective function
                if penalty_score > 0:
                    objective_terms.append(is_selected * penalty_score)

            self.model.AddExactlyOne(literals)

            # C. Main Objective Function
            weight = 10 ** (6 - priority)
            lateness = self.model.NewIntVar(0, horizon, f"lat_{t_id}")
            self.model.Add(lateness >= end_var - due_at)
            
            objective_terms.append(lateness * weight * 100) # Priority 1: No delays
            objective_terms.append(start_var)               # Priority 2: Start early

            # Store Vars
            task_vars[t_id] = {
                "start": start_var,
                "end": end_var,
                "literals": literals,
                "r_ids": compatible_ids,
                "due": due_at,
                "original_order_id": t.get("original_order_id", ""),
                "group_id": t.get("group_id", ""),
                "depends_on": t.get("final_depends_on", []),
                "qty": t.get("qty", 0)
            }

        # ---------------------------------------------------------
        # CONSTRAINTS & SOLVE
        # ---------------------------------------------------------
        for r_id, res in resource_map.items():
            intervals = res.get("intervals", [])
            for window in res.get("unavailability", []):
                start, end = int(window["start"]), int(window["end"])
                if end > start:
                    unavail = self.model.NewFixedSizeIntervalVar(start, end-start, f"unavail_{r_id}")
                    intervals.append(unavail)
            if intervals:
                self.model.AddNoOverlap(intervals)

        for t_id, tv in task_vars.items():
            for parent_id in tv["depends_on"]:
                if parent_id in task_vars:
                    self.model.Add(tv["start"] >= task_vars[parent_id]["end"])

        self.model.Minimize(sum(objective_terms))
        self.solver.parameters.max_time_in_seconds = int(self.config.get("max_search_time", 60))
        self.solver.parameters.relative_gap_limit = 0.01 # Stop early if already optimal
        
        status = self.solver.Solve(self.model)
        
        assignments = []
        overloads = []

        if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            print(f"✅ Feasible! Obj: {self.solver.ObjectiveValue()}")
            for t_id, tv in task_vars.items():
                start_val = self.solver.Value(tv["start"])
                end_val = self.solver.Value(tv["end"])
                selected_res = None
                for i, lit in enumerate(tv["literals"]):
                    if self.solver.Value(lit) == 1:
                        selected_res = tv["r_ids"][i]
                        break
                
                if selected_res:
                    assignments.append({
                        "task_id": t_id,
                        "machine_id": selected_res,
                        "start_time": start_val,
                        "end_time": end_val,
                        "group_id": tv.get("group_id", ""),
                        "order_id": tv.get("original_order_id", ""),
                        "quantity": tv.get("qty", 0),
                        "status": "ON_TIME" if end_val <= tv["due"] else "LATE"
                    })

                    if end_val > tv["due"]:
                        overloads.append({
                            "task_id": t_id,
                            "order_id": tv.get("original_order_id", ""),
                            "status": "LATE",
                            "delay_minutes": end_val - tv["due"],
                            "root_cause_code": "CAPACITY_FULL",
                            "bottleneck_resource_id": selected_res,
                            "quantity": tv.get("qty", 0)
                        })
            return {"status": "feasible", "assignments": assignments, "overloads": overloads}
        else:
            return {"status": "infeasible", "assignments": [], "overloads": []}