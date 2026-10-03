"""Run one agent inference and account for its local estimated-token allowance."""

import math

from app.model_runtime import generate
from app.platform_store import (
    agent_system_prompt,
    get_agent_provider_credential,
    reserve_agent_tokens,
    settle_agent_tokens,
)


def generate_for_agent(agent, prompt, images=None, provider=None, model_id=None):
    provider = provider or agent["provider"]
    model_id = model_id or agent["model_id"]
    system_prompt = agent_system_prompt(agent)
    images = images or []
    estimated_input = math.ceil((len(system_prompt) + len(prompt)) / 4) + len(images) * 1024
    reserved = estimated_input + 2000
    if not reserve_agent_tokens(agent["id"], reserved):
        raise RuntimeError(
            f"{agent['name']} is out of its local estimated-token allowance. Use Top up tokens in the agent registry."
        )
    try:
        api_key = get_agent_provider_credential(agent["id"]) if provider == agent["provider"] else ""
        answer = generate(provider, model_id, system_prompt, prompt, images=images, api_key=api_key)
    except Exception:
        settle_agent_tokens(agent["id"], reserved, 0)
        raise
    actual = max(1, math.ceil((len(system_prompt) + len(prompt) + len(answer)) / 4) + len(images) * 1024)
    settle_agent_tokens(agent["id"], reserved, actual)
    return answer
