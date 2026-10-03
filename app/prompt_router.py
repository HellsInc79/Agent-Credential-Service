# app/prompt_router.py

from .room_manager import run_boardroom
from .agent_hierarchy import run_hierarchy


def route_prompt(
    prompt
):

    prompt_lower = prompt.lower()

    technology = [

        "code",
        "python",
        "software",
        "fastapi",
        "database",
        "docker",
        "program"
    ]

    finance = [

        "finance",
        "budget",
        "revenue",
        "income",
        "profit",
        "pricing"
    ]

    research = [

        "research",
        "competitor",
        "market",
        "study"
    ]

    security = [

        "security",
        "risk",
        "authentication",
        "jwt",
        "encryption"
    ]

    if any(
        word in prompt_lower
        for word in technology
    ):

        return {

            "route":
                "hierarchy",

            "result":
                run_hierarchy(
                    prompt
                )
        }

    if any(
        word in prompt_lower
        for word in finance
    ):

        return {

            "route":
                "boardroom",

            "result":
                run_boardroom(
                    prompt
                )
        }

    if any(
        word in prompt_lower
        for word in research
    ):

        return {

            "route":
                "boardroom",

            "result":
                run_boardroom(
                    prompt
                )
        }

    if any(
        word in prompt_lower
        for word in security
    ):

        return {

            "route":
                "hierarchy",

            "result":
                run_hierarchy(
                    prompt
                )
        }

    return {

        "route":
            "default",

        "result":
            run_boardroom(
                prompt
            )
    }