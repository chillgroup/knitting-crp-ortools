from fastapi import FastAPI

from .api.v1.solver_route import router as solver_router
from .api.v1.health import router as health_router
from .schemas.request_schema import SolverTask

app = FastAPI(title="CP-SAT Solver API", version="1.0.0")

app.include_router(solver_router)
app.include_router(health_router)

# Required to resolve the self-referential SolverTask.sub_tasks field
SolverTask.model_rebuild()