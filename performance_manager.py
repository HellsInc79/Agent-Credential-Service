# app/performance_manager.py

from datetime import datetime

PERFORMANCE = {}

def agent_rankings():

    ranking = []

    for agent in PERFORMANCE:

        ranking.append({

            "agent":
                agent,

            "average":
                get_average_score(
                    agent
                )
        })

    ranking.sort(
        key=lambda x:
        x["average"],

        reverse=True
    )

    return ranking

def get_average_score(
    agent_name
):

    entries = PERFORMANCE.get(
        agent_name,
        []
    )

    if not entries:
        return 0

    scores = [

        x["score"]

        for x in entries
    ]

    return sum(scores) / len(scores)

def score_agent(
    agent_name,
    score,
    notes=""
):

    if agent_name not in PERFORMANCE:

        PERFORMANCE[
            agent_name
        ] = []

    PERFORMANCE[
        agent_name
    ].append({

        "score":
            score,

        "notes":
            notes,

        "created_at":
            datetime.utcnow().isoformat()
    })

    return True