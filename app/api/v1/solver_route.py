from fastapi import APIRouter

from ...schemas.request_schema import SolverPayload
from ...tasks.solver_task import optimize_schedule

router = APIRouter()


@router.post("/api/v1/solve")
async def create_solve_task(payload: SolverPayload):
    """Queue a new optimization task to the Celery worker."""
    task = optimize_schedule.delay(payload.model_dump(by_alias=False))
    return {
        "message": "Optimization task queued",
        "celery_task_id": task.id,
        "job_id": payload.job_id,
    }


@router.post("/api/re-schedule")
async def re_schedule_task(payload: SolverPayload):
    """Queue a re-scheduling task to the Celery worker."""
    task = optimize_schedule.delay(payload.model_dump(by_alias=False))
    return {
        "message": "Re-scheduling task queued",
        "celery_task_id": task.id,
        "job_id": payload.job_id,
    }
