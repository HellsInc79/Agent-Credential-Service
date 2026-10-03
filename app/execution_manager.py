# app/execution_manager.py

from .workflow_manager import *
from .task_manager import *
from .audit_manager import *


def execute_workflow(
    workflow_id
):

    workflow = get_workflow(
        workflow_id
    )

    if not workflow:

        return {
            "error":
                "Workflow not found"
        }

    results = []

    for step in workflow["steps"]:

        log_event(
            "TASK_EXECUTION",
            step["assigned_agent"],
            step["objective"]
        )

        step["status"] = (
            "complete"
        )

        results.append({

            "agent":
                step[
                    "assigned_agent"
                ],

            "objective":
                step[
                    "objective"
                ],

            "status":
                "complete"
        })

    workflow["status"] = (
        "completed"
    )

    log_event(
        "WORKFLOW",
        workflow_id,
        "Workflow completed"
    )

    return {

        "workflow":
            workflow_id,

        "status":
            "completed",

        "results":
            results
    }