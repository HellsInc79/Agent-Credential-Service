# app/orchestration_manager.py

from .room_manager import run_boardroom
from .agent_hierarchy import run_hierarchy
from .learning_manager import get_category_lessons

def classify_request(
    prompt
):

    prompt = prompt.lower()

    if any(
        word in prompt
        for word in [
            "business",
            "startup",
            "company",
            "revenue",
            "strategy",
            "profit"
        ]
    ):
        return "executive"

    if any(
        word in prompt
        for word in [
            "code",
            "python",
            "software",
            "program"
        ]
    ):
        return "technical"

    return "general"

past_lessons = (
    get_category_lessons(
        "Marketing"
    )
)

def orchestrate(
    prompt
):

    request_type = (
        classify_request(
            prompt
        )
    )

    if request_type == "executive":

        return {

            "orchestrator":
                "boardroom",

            "result":
                run_boardroom(
                    prompt
                )
        }

    if request_type == "technical":

        return {

            "orchestrator":
                "hierarchy",

            "result":
                run_hierarchy(
                    prompt
                )
        }

    return {

        "orchestrator":
            "default",

        "result":
            run_boardroom(
                prompt
            )
    }