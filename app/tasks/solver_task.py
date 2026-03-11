import os
import logging
import requests

from ..core.celery_app import celery_app
from ..engine.model import Engine
from ..engine.utils import filter_dummy_tasks, filter_dummy_overloads

logger = logging.getLogger(__name__)

WEBHOOK_URL: str = os.getenv("WEBHOOK_URL", "http://backend:8082/api/webhook/solver")


@celery_app.task(bind=True, name="optimize_schedule")
def optimize_schedule(self, payload: dict):
    try:
        logger.info(f"[Task {self.request.id}] Starting...")
        logger.info(
            f"[Task {self.request.id}] Payload received with job_id: {payload.get('job_id')}"
        )

        engine = Engine(payload)
        result = engine.solve()

        raw_assignments = result.get("assignments", [])
        raw_overloads = result.get("overloads", [])
        clean_assignments = filter_dummy_tasks(raw_assignments)
        clean_overloads = filter_dummy_overloads(raw_overloads)

        response_data = {
            "job_id": payload.get("job_id"),
            "task_id": self.request.id,
            "status": result["status"],
            "assignments": clean_assignments,
            "overloads": clean_overloads,
        }

        logger.info(f"Sending response to webhook: {WEBHOOK_URL}")
        logger.info(
            f"Response: status={result['status']}, "
            f"assignments={len(clean_assignments)}, overloads={len(clean_overloads)}"
        )

        resp = requests.post(WEBHOOK_URL, json=response_data, timeout=10)
        if resp.status_code == 200:
            logger.info("✅ Callback successful")
            return "Callback Successful"
        else:
            logger.warning(f"⚠️ Callback Failed: {resp.text}")
            return "Callback Failed"

    except Exception as exc:
        logger.error(f"[Task {self.request.id}] Error: {exc}", exc_info=True)
        raise
