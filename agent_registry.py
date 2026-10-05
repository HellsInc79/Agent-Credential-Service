# app/agent_registry.py

import uuid

AGENT_REGISTRY = {}


def register_agent(
    name,
    role,
    model,
    system_prompt
):

    agent_id = str(
        uuid.uuid4()
    )

    AGENT_REGISTRY[
        agent_id
    ] = {

        "agent_id":
            agent_id,

        "name":
            name,

        "role":
            role,

        "model":
            model,

        "system_prompt":
            system_prompt,

        "enabled":
            True
    }

    return AGENT_REGISTRY[
        agent_id
    ]


def get_agent(
    agent_id
):

    return AGENT_REGISTRY.get(
        agent_id
    )


def list_agents():

    return list(
        AGENT_REGISTRY.values()
    )


def disable_agent(
    agent_id
):

    if agent_id in AGENT_REGISTRY:

        AGENT_REGISTRY[
            agent_id
        ]["enabled"] = False

    return AGENT_REGISTRY.get(
        agent_id
    )