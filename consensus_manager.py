# app/consensus_manager.py
from .event_bus import publish_event
VOTES = {}


def get_consensus(
    vote_id
):

    if vote_id not in VOTES:

        return None

    votes = VOTES[
        vote_id
    ]["votes"]

    if not votes:

        return None

    average = sum(
        vote["score"]
        for vote in votes
    ) / len(votes)

    return {

        "proposal":
            VOTES[vote_id][
                "proposal"
            ],

        "average_score":
            average,

        "total_votes":
            len(votes),

        "votes":
            votes
    }

def create_vote_session(
    proposal
):

    vote_id = str(
        len(VOTES) + 1
    )

    VOTES[vote_id] = {

        "proposal":
            proposal,

        "votes": []
    }

    return vote_id

def cast_vote(
    vote_id,
    agent_name,
    score,
    comment="",
):

    if vote_id not in VOTES:

        return None

    VOTES[
        vote_id
    ]["votes"].append({

        "agent":
            agent_name,

        "score":
            score,

        "comment":
            comment
    })
    publish_event(
        "CONSENSUS_VOTE",
        agent_name,
        f"Vote {score}"
    )

    return True