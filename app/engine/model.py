import json
import logging
import os
from datetime import datetime
from typing import Dict, Any

from ortools.sat.python import cp_model

from .builder import TaskModelBuilder

# Per-run log file so each solve session is independently traceable
_base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) or "."
_log_dir = os.path.join(_base_dir, "logs")
os.makedirs(_log_dir, exist_ok=True)
_log_file = os.path.join(_log_dir, f"scheduling_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

logger = logging.getLogger(__name__)
logger.handlers.clear()
logger.setLevel(logging.INFO)

_file_handler = logging.FileHandler(_log_file)
_file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(_file_handler)

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))
logger.addHandler(_console_handler)

logger.info(f"🔵 Logs will be saved to: {_log_file}")


class Engine:
    """
    Orchestrates the CP-SAT solving pipeline.
    Parses the raw JSON payload from Go, delegates model construction to
    TaskModelBuilder, then runs the solver and returns the result dict.
    """

    def __init__(self, payload: Dict[str, Any]) -> None:
        logger.info("📥 Parsing config from payload...")
        raw_config = payload.get("config", "{}")
        if isinstance(raw_config, str):
            try:
                self.config: Dict[str, Any] = json.loads(raw_config)
            except Exception:
                self.config = {}
        else:
            self.config = raw_config

        machines_data = payload.get("machines", [])
        logger.info(f"📦 RECEIVED {len(machines_data)} MACHINES FROM PAYLOAD")

        # Capture each machine's current design/color state for affinity scoring
        self.machine_states: Dict[str, Dict[str, str]] = {}
        for m in machines_data:
            m_id = m.get("id")
            if m_id:
                self.machine_states[m_id] = {
                    "current_design": m.get("design_item_id", ""),
                    "current_color": m.get("color_config", ""),
                }

        self.resources = payload.get("resources", [])
        self.tasks = payload.get("tasks", [])

    def solve(self) -> Dict[str, Any]:
        if not self.tasks:
            return {"status": "feasible", "assignments": [], "overloads": []}

        builder = (
            TaskModelBuilder(self.config, self.resources, self.tasks, self.machine_states)
            .build_time_variables()
            .build_resource_allocations()
            .apply_routing_constraints()
            .apply_dependency_constraints()
            .apply_batch_offset_constraints()
            .define_objective()
        )

        logger.info(
            f"\n📋 TASKS IN MODEL ({len(builder.task_vars)}): "
            f"{sorted(builder.task_vars.keys())}"
        )
        logger.info("🗺  TRANSLATION MAP (non-identity entries):")
        for k, v in builder.task_translation_map.items():
            if k != v:
                logger.info(f"   {k} -> {v}")

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = int(self.config.get("max_search_time", 60))
        # Stop early when the gap to the proven lower-bound is ≤ 1 %
        solver.parameters.relative_gap_limit = 0.01
        solver.parameters.num_search_workers = 8

        status = solver.Solve(builder.model)
        return builder.extract_results(solver, status)
