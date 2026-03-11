from typing import List, Dict, Any


def filter_dummy_tasks(assignments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Remove DUMMY_ tasks and Unavailability entries before returning to backend."""
    if not assignments:
        return []
    return [
        task for task in assignments
        if not str(task.get("task_id", "")).startswith("DUMMY_")
        and str(task.get("operation", "")) != "Unavailability"
    ]


def filter_dummy_overloads(overloads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Remove DUMMY_ tasks and Unavailability entries from overloads."""
    if not overloads:
        return []
    return [
        overload for overload in overloads
        if not str(overload.get("task_id", "")).startswith("DUMMY_")
        and str(overload.get("operation", "")) != "Unavailability"
    ]
