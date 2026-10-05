# app/workflow_manager.py

import uuid
from .event_bus import publish_event
WORKFLOWS = {}

def create_workflow(
    name,
    goal
):

    workflow_id = str(
        uuid.uuid4()
    )

    WORKFLOWS[
        workflow_id
    ] = {

        "workflow_id":
            workflow_id,

        "name":
            name,

        "goal":
            goal,

        "status":
            "active",

        "steps": []
    }
    publish_event(
        "WORKFLOW_CREATED",
        "CEO-AI",
        workflow_id
    )
    return WORKFLOWS[
        workflow_id
    ]

    




def add_step(
    workflow_id,
    assigned_agent,
    objective
):

    step_id = str(
        uuid.uuid4()
    )

    WORKFLOWS[
        workflow_id
    ]["steps"].append({

        "step_id":
            step_id,

        "assigned_agent":
            assigned_agent,

        "objective":
            objective,

        "status":
            "pending"
    })

    return step_id


def complete_step(
    workflow_id,
    step_id
):

    for step in WORKFLOWS[
        workflow_id
    ]["steps"]:

        if (
            step["step_id"]
            ==
            step_id
        ):

            step["status"] = (
                "complete"
            )

            return step

    return None


def get_workflow(
    workflow_id
):

    return WORKFLOWS.get(
        workflow_id
    )


def list_workflows():

    return list(
        WORKFLOWS.values()
    )