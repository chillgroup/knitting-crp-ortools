# CLAUDE.md — Claude Code Configuration for Knitting-CRP-Ortools

## Project Context

**App:** Knitting-CRP-Ortools
**Stack:** Python 3.9+, OR-Tools CP-SAT v9.8+, FastAPI, Celery + Redis, Pydantic v2
**Stage:** Phase 1 — Closing P1 gaps (random_seed, root-cause classifier, dynamic weights)
**User Level:** Developer (Operations Research Engineer)

## Directives

1. **Master Plan:** Read `AGENTS.md` first. It has the current phase, implementation order, and protected files.
2. **Documentation:** Use `agent_docs/` for patterns and conventions:
   - `agent_docs/code_patterns.md` — CP-SAT variable patterns, builder method structure
   - `agent_docs/tech_stack.md` — stack details, solver configuration examples
   - `agent_docs/testing.md` — fixture types, test class structure, coverage rules
3. **Plan-First:** For any `builder.py` or `model.py` change, state which methods are affected and what CP-SAT constraints change before writing code.
4. **One Step at a Time:** Implement one item from the phase order, run `pytest tests/ -v`, then proceed.
5. **Solver Correctness:** After any model change, run `pytest tests/test_constraints.py` to verify no hard constraint violations on known fixtures.
6. **Memory:** Update `MEMORY.md` after completing each phase step.

## Key Constraints (Never Violate)

- All CP-SAT variable bounds and penalty terms must be `int` — never `float`
- No CP-SAT calls outside `builder.py`
- `request_schema.py` and `response_schema.py` field names are Go wire contract — do NOT rename
- OR-Tools version pin in `requirements.txt` — do NOT change
- Vietnamese comments in `builder.py` — do NOT remove (domain-specific documentation)

## Commands

```bash
# Start API (dev)
uvicorn app.main:app --host 0.0.0.0 --port 8083 --reload

# Start worker (dev)
celery -A app.core.celery_app worker --loglevel=info --concurrency=1

# Run tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=app --cov-report=term-missing

# Lint
ruff check app/ tests/

# Type check
mypy app/

# Docker (all services)
docker compose up --build
```

## Current Phase Tasks

See `MEMORY.md` for exact current task. Phase 1 order:

1. `random_seed` + `num_search_workers` in `request_schema.py` + `model.py`
2. Determinism test + 200-task fixture
3. `_classify_root_cause()` in `builder.py`
4. Root-cause tests + fixtures
5. Ghost-task count guard in `builder.py`
6. Overload-ratio diagnostic in `model.py`
7. Dynamic objective weight calibration in `builder.py`
