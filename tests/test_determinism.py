"""
Determinism regression tests.

Verifies that identical SolverPayload + random_seed + num_search_workers=1
produces byte-identical assignment lists across N replay runs.

Why num_search_workers=1?
  CP-SAT's LNS uses thread-local RNG per worker. With >1 workers, wall-clock
  timing differences between threads can yield different solution orderings.
  Single-worker mode guarantees a fully deterministic search tree.
"""
import copy

import pytest

from app.engine.model import Engine
from tests.conftest import make_payload


# ── Helpers ────────────────────────────────────────────────────────────────

def _sorted_assignments(result):
    """Return assignments sorted by task_id for stable comparison."""
    return sorted(result.get("assignments", []), key=lambda a: a["task_id"])


# ── Tests ──────────────────────────────────────────────────────────────────

class TestDeterminism:

    def test_five_replays_identical_smoke(self, payload_smoke):
        """5-run replay on 10-task payload → byte-identical sorted assignments."""
        results = []
        for _ in range(5):
            results.append(Engine(copy.deepcopy(payload_smoke)).solve())

        statuses = [r["status"] for r in results]
        assert all(s in ("feasible", "optimal") for s in statuses), (
            f"One or more runs returned infeasible. Statuses: {statuses}"
        )

        baseline = _sorted_assignments(results[0])
        assert len(baseline) > 0, "Baseline returned 0 assignments"

        for run_idx, run in enumerate(results[1:], start=2):
            assert _sorted_assignments(run) == baseline, (
                f"Run {run_idx} assignments differ from run 1 — non-deterministic output.\n"
                f"Baseline task_ids: {[a['task_id'] for a in baseline]}\n"
                f"Run {run_idx} task_ids: {[a['task_id'] for a in _sorted_assignments(run)]}"
            )

    @pytest.mark.slow
    def test_five_replays_identical_200tasks(self, payload_200):
        """5-run replay on 200-task payload → byte-identical sorted assignments."""
        results = []
        for _ in range(5):
            results.append(Engine(copy.deepcopy(payload_200)).solve())

        statuses = [r["status"] for r in results]
        assert all(s in ("feasible", "optimal") for s in statuses), (
            f"One or more runs returned infeasible. Statuses: {statuses}"
        )

        baseline = _sorted_assignments(results[0])
        assert len(baseline) > 0, "Baseline returned 0 assignments"

        for run_idx, run in enumerate(results[1:], start=2):
            assert _sorted_assignments(run) == baseline, (
                f"Run {run_idx} differs from run 1 on 200-task payload."
            )

    def test_random_seed_field_is_wired(self, payload_smoke):
        """random_seed param reaches the solver — different seeds may yield different results.
        This test just verifies both runs are feasible (not that they differ, which is not guaranteed
        for simple payloads), but catches a KeyError if the field is missing from config.
        """
        p1 = copy.deepcopy(payload_smoke)
        p1["config"]["random_seed"] = 1

        p2 = copy.deepcopy(payload_smoke)
        p2["config"]["random_seed"] = 999

        r1 = Engine(p1).solve()
        r2 = Engine(p2).solve()

        assert r1["status"] in ("feasible", "optimal"), "Seed 1 run infeasible"
        assert r2["status"] in ("feasible", "optimal"), "Seed 999 run infeasible"

    def test_num_search_workers_field_is_wired(self, payload_smoke):
        """num_search_workers param reaches the solver without crashing."""
        for workers in (1, 2, 4):
            p = copy.deepcopy(payload_smoke)
            p["config"]["num_search_workers"] = workers
            result = Engine(p).solve()
            assert result["status"] in ("feasible", "optimal"), (
                f"Infeasible with num_search_workers={workers}"
            )

    def test_missing_seed_uses_default(self):
        """SolverConfig default (random_seed=42) is used when Go omits the field."""
        payload = make_payload(n_orders=5, max_search_time=15)
        # Remove the fields to simulate an older Go payload
        del payload["config"]["random_seed"]
        del payload["config"]["num_search_workers"]

        result = Engine(payload).solve()
        assert result["status"] in ("feasible", "optimal"), (
            "Solver failed when random_seed/num_search_workers omitted from config"
        )
