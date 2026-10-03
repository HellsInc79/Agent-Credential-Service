"""Run specialist agents and assemble a boardroom response."""

from app.agent_manager import get_agent
from app.memory_manager import add_memory, build_memory_context
from app.boardroom_history import create_boardroom_session, save_report
from app.ollama_client import ask_model


def run_agent(agent, prompt, session_id=None):
    if not agent:
        raise ValueError("Requested boardroom agent is not configured")
    memory_context = build_memory_context(agent["name"])
    enhanced_prompt = f"Previous context:\n{memory_context}\n\nTask:\n{prompt}"
    result = ask_model(agent["model"], enhanced_prompt, agent["system"])
    reply = result.get("response") or result.get("message", {}).get("content") or "The model returned an empty response."
    add_memory(agent["name"], reply)
    if session_id:
        save_report(session_id, agent["name"], reply)
    return reply


def run_boardroom(prompt, on_agent_reply=None):
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("Please enter a prompt for the boardroom")

    session_id = create_boardroom_session(prompt)
    specialists = ["Research-AI", "Coding-AI", "Security-AI", "Marketing-AI", "Finance-AI"]
    results = {}
    for name in specialists:
        try:
            results[name] = run_agent(get_agent(name), prompt, session_id)
        except Exception as exc:
            results[name] = f"{name} could not respond: {exc}"
        if on_agent_reply:
            on_agent_reply(name, results[name])

    critic_prompt = f"Review these specialist reports for mistakes, risks, and assumptions:\n{results}"
    try:
        critic_response = run_agent(get_agent("Critic-AI"), critic_prompt, session_id)
    except Exception as exc:
        critic_response = f"Critic-AI could not respond: {exc}"
    if on_agent_reply:
        on_agent_reply("Critic-AI", critic_response)

    ceo_prompt = (
        f"User request:\n{prompt}\n\nSpecialist reports:\n{results}\n\n"
        f"Critic review:\n{critic_response}\n\nProvide a practical final answer."
    )
    try:
        ceo_response = run_agent(get_agent("CEO-AI"), ceo_prompt, session_id)
    except Exception as exc:
        ceo_response = f"CEO-AI could not respond: {exc}"
    if on_agent_reply:
        on_agent_reply("CEO-AI", ceo_response)

    return {
        "session_id": session_id,
        "specialists": results,
        "critic_ai": critic_response,
        "ceo_ai": ceo_response,
    }
