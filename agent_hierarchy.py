# agent_hierarchy.py
# agent_hierarchy.py

from .room_manager import (
run_agent
)

from .agent_manager import (
get_agent
)

def run_hierarchy(prompt):

    results = {}

    research = app.agent_manager.get_agent(
        "Research-AI"
    )

    research_report = run_agent(
        research,
        prompt
    )

    results["research"] = (
        research_report
    )

    coding = app.agent_manager.get_agent(
        "Coding-AI"
    )

    coding_report = run_agent(
        coding,
        prompt
    )

    results["coding"] = (
        coding_report
    )

    security = app.agent_manager.get_agent(
        "Security-AI"
    )

    security_report = run_agent(
        security,
        prompt
    )

    results["security"] = (
        security_report
    )

    marketing = app.agent_manager.get_agent(
        "Marketing-AI"
    )

    marketing_report = run_agent(
        marketing,
        prompt
    )

    results["marketing"] = (
        marketing_report
    )

    finance = app.agent_manager.get_agent(
        "Finance-AI"
    )

    finance_report = run_agent(
        finance,
        prompt
    )

    results["finance"] = (
        finance_report
    )

    critic = app.agent_manager.get_agent(
        "Critic-AI"
    )

    critic_report = run_agent(
        critic,
        str(results)
    )

    ceo = app.agent_manager.get_agent(
        "CEO-AI"
    )

    ceo_prompt = f"""
User Request:

{prompt}

Specialist Reports:

{results}

Critic Review:

{critic_report}

Produce final executive decision.
"""

    ceo_report = run_agent(
        ceo,
        ceo_prompt
    )

    return {
        "specialists":
            results,

        "critic":
            critic_report,

        "ceo":
            ceo_report
    }