# app/delegation_manager.py

import uuid

ACTIVE_TASKS = {}


def create_delegated_task(
    title,
    assigned_agent,
    details
):

    task_id = str(
        uuid.uuid4()
    )

    ACTIVE_TASKS[task_id] = {

        "task_id":
            task_id,

        "title":
            title,

        "assigned_agent":
            assigned_agent,

        "details":
            details,

        "status":
            "pending",

        "result":
            None
    }

    return ACTIVE_TASKS[task_id]


def complete_task(
    task_id,
    result
):

    if task_id not in ACTIVE_TASKS:

        return None

    ACTIVE_TASKS[task_id][
        "status"
    ] = "complete"

    ACTIVE_TASKS[task_id][
        "result"
    ] = result

    return ACTIVE_TASKS[task_id]


def get_task(
    task_id
):

    return ACTIVE_TASKS.get(
        task_id
    )


def list_tasks():

    return list(
        ACTIVE_TASKS.values()
    )