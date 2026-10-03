# app/task_manager.py

TASKS = {}

def create_task(
    title,
    assigned_to
):
    publish_event(
        "TASK_CREATED",
            assigned_to,
            title
)

    task_id = str(
        len(TASKS) + 1
    )

    TASKS[task_id] = {

        "id": task_id,

        "title": title,

        "assigned_to": assigned_to,

        "status": "pending"
    }

    return TASKS[task_id]


def get_task(task_id):

    return TASKS.get(task_id)


def list_tasks():

    return list(
        TASKS.values()
    )


def update_task(
    task_id,
    status
):

    if task_id in TASKS:

        TASKS[
            task_id
        ]["status"] = status

    return TASKS.get(
        task_id
    )

