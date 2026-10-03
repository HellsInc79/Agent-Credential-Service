# app/autonomous_boardroom.py

from .room_manager import run_boardroom
from .knowledge_manager import search_knowledge
from .learning_manager import get_lessons
from .audit_manager import log_event


def autonomous_session(
    objective
):

    knowledge = search_knowledge(
        objective
    )

    lessons = get_lessons()

    context = f"""
OBJECTIVE:

{objective}

KNOWLEDGE:

{knowledge}

LESSONS:

{lessons}
"""

    result = run_boardroom(
        context
    )

    log_event(
        "AUTONOMOUS_BOARDROOM",
        "CEO-AI",
        objective
    )

    return {

        "objective":
            objective,

        "knowledge_used":
            len(knowledge),

        "lessons_used":
            len(lessons),

        "boardroom_result":
            result
    }